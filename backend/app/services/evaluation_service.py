import random
import math
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from app.models.models import (
    Customer, Payment, RevenueRiskCase, RecoveryAction, RecoveryOutcome, AuditLog, Merchant,
    EvaluationRun, EvaluationMetric, EvaluationBreakdown
)
from app.services.synthetic_generator import seed_random, generate_customer_profile, generate_payments_for_customer
from app.services.risk_engine import RiskEngineService
from app.services.baseline_strategy import BaselineStrategyService
from app.services.policy_engine import PolicyEngineService
from app.services.action_executor import ActionExecutorService
from app.core.logging import logger

class EvaluationService:
    @staticmethod
    def run_offline_evaluation(db: Session, name: str, random_seed: int = 42, sample_size: int = 50) -> EvaluationRun:
        """
        Runs a mathematically transparent paired offline counterfactual evaluation.
        Compares BASELINE vs AI strategies under identical cloned case conditions and an observable-only conversion model.
        """
        logger.info("evaluation_run_started", name=name, seed=random_seed, sample_size=sample_size)

        # 1. Clear previous simulation evaluation cases/data to prevent DB contamination
        db.query(RecoveryOutcome).filter(RecoveryOutcome.verification_source == "OFFLINE_SIMULATION").delete(synchronize_session=False)
        db.query(RecoveryAction).filter(
            RecoveryAction.case_id.in_(
                db.query(RevenueRiskCase.id).filter(RevenueRiskCase.is_synthetic == True)
            )
        ).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.case_id.in_(
            db.query(RevenueRiskCase.id).filter(RevenueRiskCase.is_synthetic == True)
        )).delete(synchronize_session=False)
        db.query(RevenueRiskCase).filter(RevenueRiskCase.is_synthetic == True).delete(synchronize_session=False)
        db.query(Payment).filter(Payment.is_synthetic == True).delete(synchronize_session=False)
        db.query(Customer).filter(Customer.is_synthetic == True).delete(synchronize_session=False)
        db.commit()

        # Seed random state for reproducibility
        seed_random(random_seed)
        sim_rand = random.Random(random_seed)

        # Ensure default merchant exists
        merchant = db.query(Merchant).filter(Merchant.id == "mer_synth_001").first()
        if not merchant:
            merchant = Merchant(id="mer_synth_001", name="Evaluation Merchant")
            db.add(merchant)
            db.commit()

        # 2. Generate Matched Synthetic Profiles
        case_pairs = []
        for i in range(1, sample_size + 1):
            cust_prof = generate_customer_profile(i)
            db_customer = Customer(
                id=cust_prof["id"], email=cust_prof["email"], name=cust_prof["name"],
                phone=cust_prof["phone"], is_synthetic=True, metadata_json=cust_prof["metadata_json"]
            )
            db.add(db_customer)

            # Generate 1 failed payment representing the active recovery trigger
            pay_profiles = generate_payments_for_customer(cust_prof, 0)
            pay_prof = pay_profiles[-1]
            pay_prof["status"] = "failed"  # Ensure it is failed to trigger case creation
            
            # Map failure reason based on method to have diverse segments
            method = sim_rand.choice(["card", "upi", "netbanking"])
            pay_prof["method"] = method
            if method == "card":
                pay_prof["failure_reason"] = sim_rand.choice(["expired_card", "insufficient_funds"])
            elif method == "upi":
                pay_prof["failure_reason"] = sim_rand.choice(["bank_timeout", "insufficient_funds"])
            else:
                pay_prof["failure_reason"] = "bank_timeout"

            db_payment = Payment(
                id=pay_prof["id"], amount=pay_prof["amount"], currency=pay_prof["currency"],
                status=pay_prof["status"], method=pay_prof["method"], failure_reason=pay_prof["failure_reason"],
                customer_id=pay_prof["customer_id"], merchant_id=merchant.id, is_synthetic=True,
                metadata_json=pay_prof["metadata_json"], created_at=pay_prof["created_at"]
            )
            db.add(db_payment)
            db.flush()

            # 3. Create Parallel Matched Clones
            # Clone A: Baseline Strategy Case
            case_baseline = RevenueRiskCase(
                payment_id=db_payment.id, customer_id=db_customer.id, merchant_id=merchant.id,
                amount_at_risk=db_payment.amount, event_type="FAILED_PAYMENT", current_state="NEW",
                failure_reason=db_payment.failure_reason, recovery_strategy_group="BASELINE",
                is_synthetic=True, recovery_probability=0.5, prioritization_score=db_payment.amount * 0.5
            )
            db.add(case_baseline)

            # Clone B: AI Strategy Case
            case_ai = RevenueRiskCase(
                payment_id=db_payment.id, customer_id=db_customer.id, merchant_id=merchant.id,
                amount_at_risk=db_payment.amount, event_type="FAILED_PAYMENT", current_state="NEW",
                failure_reason=db_payment.failure_reason, recovery_strategy_group="AI",
                is_synthetic=True, recovery_probability=0.5, prioritization_score=db_payment.amount * 0.5
            )
            db.add(case_ai)
            db.flush()

            case_pairs.append((case_baseline, case_ai))

        db.commit()

        # Update initial expected risk scores for both cases using RiskEngine helpers
        from app.services.risk_engine import calculate_loss_risk_score, calculate_recovery_probability
        
        for cb, ca in case_pairs:
            # Baseline cb
            cust_b = db.query(Customer).filter(Customer.id == cb.customer_id).first()
            hist_success_b = cust_b.metadata_json.get("historical_success_rate", 0.80) if cust_b and cust_b.metadata_json else 0.80
            cb.loss_risk_score = calculate_loss_risk_score(cb.failure_reason, cb.recovery_attempts + 1)
            cb.recovery_probability = calculate_recovery_probability(cb.failure_reason, hist_success_b, cb.recovery_attempts)
            cb.prioritization_score = cb.amount_at_risk * cb.recovery_probability

            # AI ca
            cust_a = db.query(Customer).filter(Customer.id == ca.customer_id).first()
            hist_success_a = cust_a.metadata_json.get("historical_success_rate", 0.80) if cust_a and cust_a.metadata_json else 0.80
            ca.loss_risk_score = calculate_loss_risk_score(ca.failure_reason, ca.recovery_attempts + 1)
            ca.recovery_probability = calculate_recovery_probability(ca.failure_reason, hist_success_a, ca.recovery_attempts)
            ca.prioritization_score = ca.amount_at_risk * ca.recovery_probability
        db.commit()

        # 4. Multi-Attempt Simulation Loop
        # Simulate step-by-step recovery process up to 3 attempts
        max_simulation_attempts = 3
        
        # Keep track of AI actions proposed for success rate metrics
        ai_proposals_count = 0
        ai_success_count = 0
        ai_fallback_count = 0

        for attempt in range(max_simulation_attempts):
            for cb, ca in case_pairs:
                # A. Simulate BASELINE step
                if cb.current_state not in ["RECOVERED", "STOPPED", "NOT_RECOVERED"]:
                    action_type, action_params = BaselineStrategyService.determine_next_action(
                        cb.failure_reason, cb.recovery_attempts
                    )
                    act = RecoveryAction(case_id=cb.id, action_type=action_type, parameters=action_params, status="PENDING")
                    db.add(act)
                    db.flush()

                    approved, block_reason = PolicyEngineService.validate_action(db, cb, act)
                    if approved:
                        act.status = "EXECUTED"
                        act.executed_at = datetime.utcnow()
                        cb.recovery_attempts += 1
                        cb.current_state = "ACTION_EXECUTED"
                        
                        # Simulate outcome using observable-only conversion probabilities
                        success = EvaluationService._roll_simulated_outcome(act, cb, sim_rand)
                        if success:
                            EvaluationService._record_simulated_outcome(db, cb, act, True)
                        elif cb.recovery_attempts >= cb.max_attempts:
                            EvaluationService._record_simulated_outcome(db, cb, act, False)
                    else:
                        act.status = "BLOCKED"
                        act.parameters = {"block_reason": block_reason}
                        cb.current_state = "STOPPED"
                        EvaluationService._record_simulated_outcome(db, cb, act, False)

                # B. Simulate AI step
                if ca.current_state not in ["RECOVERED", "STOPPED", "NOT_RECOVERED"]:
                    ai_proposals_count += 1
                    # Deterministically simulate AI recommended action ( seed-dependent, strategy-agnostic converter )
                    rec_action = EvaluationService._simulate_ai_recommendation(ca, attempt, sim_rand)
                    
                    act = RecoveryAction(case_id=ca.id, action_type=rec_action, parameters={}, status="PENDING")
                    db.add(act)
                    db.flush()

                    approved, block_reason = PolicyEngineService.validate_action(db, ca, act)
                    if approved:
                        ai_success_count += 1
                        act.status = "EXECUTED"
                        act.executed_at = datetime.utcnow()
                        ca.recovery_attempts += 1
                        ca.current_state = "ACTION_EXECUTED"

                        success = EvaluationService._roll_simulated_outcome(act, ca, sim_rand)
                        if success:
                            EvaluationService._record_simulated_outcome(db, ca, act, True)
                        elif ca.recovery_attempts >= ca.max_attempts:
                            EvaluationService._record_simulated_outcome(db, ca, act, False)
                    else:
                        # Double Loop Fallback trigger
                        ai_fallback_count += 1
                        act.status = "BLOCKED"
                        act.parameters = {"block_reason": block_reason}
                        
                        # Fetch baseline alternative
                        fb_action, fb_params = BaselineStrategyService.determine_next_action(
                            ca.failure_reason, ca.recovery_attempts
                        )
                        fb_act = RecoveryAction(case_id=ca.id, action_type=fb_action, parameters=fb_params, status="PENDING")
                        db.add(fb_act)
                        db.flush()

                        fb_approved, fb_block_reason = PolicyEngineService.validate_action(db, ca, fb_act)
                        if fb_approved:
                            fb_act.status = "EXECUTED"
                            fb_act.executed_at = datetime.utcnow()
                            ca.recovery_attempts += 1
                            ca.current_state = "ACTION_EXECUTED"

                            success = EvaluationService._roll_simulated_outcome(fb_act, ca, sim_rand)
                            if success:
                                EvaluationService._record_simulated_outcome(db, ca, fb_act, True)
                            elif ca.recovery_attempts >= ca.max_attempts:
                                EvaluationService._record_simulated_outcome(db, ca, fb_act, False)
                        else:
                            fb_act.status = "BLOCKED"
                            fb_act.parameters = {"block_reason": fb_block_reason}
                            ca.current_state = "STOPPED"
                            EvaluationService._record_simulated_outcome(db, ca, fb_act, False)
            db.commit()

        # 5. Compute Paired Matrix ( McNemar Contingency Cells )
        both_recovered = 0
        ai_only_recovered = 0
        baseline_only_recovered = 0
        neither_recovered = 0

        for cb, ca in case_pairs:
            # Fetch outcomes
            out_b = db.query(RecoveryOutcome).filter(RecoveryOutcome.case_id == cb.id, RecoveryOutcome.is_recovered == True).first()
            out_a = db.query(RecoveryOutcome).filter(RecoveryOutcome.case_id == ca.id, RecoveryOutcome.is_recovered == True).first()

            is_b_rec = out_b is not None
            is_a_rec = out_a is not None

            if is_a_rec and is_b_rec:
                both_recovered += 1
            elif is_a_rec and not is_b_rec:
                ai_only_recovered += 1
            elif not is_a_rec and is_b_rec:
                baseline_only_recovered += 1
            else:
                neither_recovered += 1

        # McNemar paired stats
        mismatched_total = ai_only_recovered + baseline_only_recovered
        if mismatched_total > 0:
            mcnemar_stat = float(((abs(ai_only_recovered - baseline_only_recovered) - 1.0) ** 2) / mismatched_total)
            odds_ratio = float(ai_only_recovered / baseline_only_recovered) if baseline_only_recovered > 0 else float('inf')
        else:
            mcnemar_stat = 0.0
            odds_ratio = 1.0

        p_value = EvaluationService._binomial_exact_p_value(ai_only_recovered, baseline_only_recovered)
        statistically_significant = p_value < 0.05

        paired_summary = {
            "both_recovered": both_recovered,
            "ai_only_recovered": ai_only_recovered,
            "baseline_only_recovered": baseline_only_recovered,
            "neither_recovered": neither_recovered,
            "mcnemar_statistic": mcnemar_stat,
            "odds_ratio": odds_ratio,
            "p_value": p_value,
            "statistically_significant": statistically_significant,
            "is_paired": True,
            "statistical_warning": sample_size < 30 or mismatched_total < 25
        }

        # 6. Save Persistent Evaluation Report
        run = EvaluationRun(
            name=name,
            random_seed=random_seed,
            simulation_config={
                "sample_size": sample_size,
                "max_attempts": max_simulation_attempts,
                "version": "1.0.0"
            },
            created_at=datetime.utcnow()
        )
        db.add(run)
        db.flush()

        # Compute separate strategy metrics
        for group in ["BASELINE", "AI"]:
            cases = [c for cb, ca in case_pairs for c in (cb, ca) if c.recovery_strategy_group == group]
            total_cases = len(cases)
            
            if total_cases == 0:
                continue

            total_at_risk = sum(c.amount_at_risk for c in cases)
            
            outcomes = db.query(RecoveryOutcome).filter(
                RecoveryOutcome.case_id.in_([c.id for c in cases])
            ).all()

            rec_outcomes = [o for o in outcomes if o.is_recovered]
            recovered_count = len(rec_outcomes)
            recovery_rate = float(recovered_count / total_cases)
            total_rec_revenue = sum(o.recovered_amount for o in rec_outcomes)
            rec_revenue_rate = float(total_rec_revenue / total_at_risk) if total_at_risk > 0 else 0.0
            avg_rec_amount = float(total_rec_revenue / recovered_count) if recovered_count > 0 else 0.0

            # Calculate time to recovery (from raw outcomes data)
            t_recovered = [o.raw_verification_data.get("time_to_recovery_seconds", 0.0) for o in rec_outcomes]
            avg_time = float(sum(t_recovered) / len(t_recovered)) if t_recovered else 0.0
            
            if t_recovered:
                sorted_t = sorted(t_recovered)
                mid = len(sorted_t) // 2
                median_time = float(sorted_t[mid]) if len(sorted_t) % 2 != 0 else float((sorted_t[mid - 1] + sorted_t[mid]) / 2.0)
            else:
                median_time = 0.0

            # Attempts and blocks
            actions = db.query(RecoveryAction).filter(
                RecoveryAction.case_id.in_([c.id for c in cases])
            ).all()
            total_attempts = len([a for a in actions if a.status == "EXECUTED"])
            blocked_actions = len([a for a in actions if a.status == "BLOCKED"])
            
            # Fallback action detection (AI group actions created as baseline alternatives)
            if group == "AI":
                fallback_actions = len([a for a in actions if a.status == "EXECUTED" and a.ai_decision_id is None])
                success_rate = float(ai_success_count / ai_proposals_count) if ai_proposals_count > 0 else 0.0
                fallback_rate = float(ai_fallback_count / ai_proposals_count) if ai_proposals_count > 0 else 0.0
            else:
                fallback_actions = 0
                success_rate = 1.0
                fallback_rate = 0.0

            policy_block_rate = float(blocked_actions / len(actions)) if actions else 0.0

            # Expected vs Actual
            expected_rev = sum(int(c.amount_at_risk * c.recovery_probability) for c in cases)
            actual_rev = total_rec_revenue

            # 95% Confidence Interval for proportion
            se = math.sqrt((recovery_rate * (1 - recovery_rate)) / total_cases)
            ci_low = max(0.0, recovery_rate - (1.96 * se))
            ci_high = min(1.0, recovery_rate + (1.96 * se))

            metric = EvaluationMetric(
                run_id=run.id,
                strategy_group=group,
                total_cases=total_cases,
                total_revenue_at_risk=total_at_risk,
                recovered_cases=recovered_count,
                recovery_rate=recovery_rate,
                total_recovered_revenue=total_rec_revenue,
                recovery_revenue_rate=rec_revenue_rate,
                average_recovered_amount=avg_rec_amount,
                avg_time_to_recovery=avg_time,
                median_time_to_recovery=median_time,
                total_attempts=total_attempts,
                blocked_actions=blocked_actions,
                fallback_actions=fallback_actions,
                ai_decision_success_rate=success_rate,
                ai_fallback_rate=fallback_rate,
                policy_block_rate=policy_block_rate,
                expected_recovered_revenue=expected_rev,
                actual_recovered_revenue=actual_rev,
                confidence_interval_low=ci_low,
                confidence_interval_high=ci_high,
                paired_metrics=paired_summary if group == "AI" else None
            )
            db.add(metric)
            db.flush()

            # Record Category breakdowns (failure reason, payment method, transaction size)
            # 1. Failure Reason breakdown
            reasons = set(c.failure_reason for c in cases)
            for r in reasons:
                r_cases = [c for c in cases if c.failure_reason == r]
                r_outcomes = [o for o in rec_outcomes if db.query(RevenueRiskCase).filter(RevenueRiskCase.id == o.case_id).first().failure_reason == r]
                
                db.add(EvaluationBreakdown(
                    metric_id=metric.id, breakdown_type="failure_reason", key=r,
                    total_cases=len(r_cases), recovered_cases=len(r_outcomes),
                    recovered_revenue=sum(o.recovered_amount for o in r_outcomes),
                    expected_recovered_revenue=sum(int(c.amount_at_risk * c.recovery_probability) for c in r_cases)
                ))

            # 2. Payment Method breakdown
            payments = [db.query(Payment).filter(Payment.id == c.payment_id).first() for c in cases]
            methods = set(p.method for p in payments if p)
            for m in methods:
                m_cases = [c for c in cases if db.query(Payment).filter(Payment.id == c.payment_id).first().method == m]
                m_outcomes = [o for o in rec_outcomes if db.query(Payment).filter(Payment.id == db.query(RevenueRiskCase).filter(RevenueRiskCase.id == o.case_id).first().payment_id).first().method == m]
                
                db.add(EvaluationBreakdown(
                    metric_id=metric.id, breakdown_type="payment_method", key=m,
                    total_cases=len(m_cases), recovered_cases=len(m_outcomes),
                    recovered_revenue=sum(o.recovered_amount for o in m_outcomes),
                    expected_recovered_revenue=sum(int(c.amount_at_risk * c.recovery_probability) for c in m_cases)
                ))

            # 3. Transaction Value Bucket breakdown
            buckets = {"low_value": lambda a: a < 100000, "mid_value": lambda a: 100000 <= a < 1000000, "high_value": lambda a: a >= 1000000}
            for b_name, b_filter in buckets.items():
                b_cases = [c for c in cases if b_filter(c.amount_at_risk)]
                b_outcomes = [o for o in rec_outcomes if b_filter(db.query(RevenueRiskCase).filter(RevenueRiskCase.id == o.case_id).first().amount_at_risk)]
                
                db.add(EvaluationBreakdown(
                    metric_id=metric.id, breakdown_type="transaction_value_bucket", key=b_name,
                    total_cases=len(b_cases), recovered_cases=len(b_outcomes),
                    recovered_revenue=sum(o.recovered_amount for o in b_outcomes),
                    expected_recovered_revenue=sum(int(c.amount_at_risk * c.recovery_probability) for c in b_cases)
                ))

        db.commit()
        return run

    @staticmethod
    def _simulate_ai_recommendation(case: RevenueRiskCase, attempt: int, sim_rand: random.Random) -> str:
        """
        Simulates AI recovery recommendation deterministically based on seed and failure characteristics.
        Ensures simulation config reproduces identical decisions.
        """
        reason = (case.failure_reason or "").lower()
        
        # Proposes more targeted action paths than baseline (representing typical AI logic)
        if reason == "expired_card":
            # AI knows expired cards cannot be retried directly. Recommends payment links.
            return "PAYMENT_LINK" if attempt == 0 else "HUMAN_ESCALATION"
        
        elif reason == "insufficient_funds":
            # For insufficient funds, AI balances retries and links
            return sim_rand.choice(["PAYMENT_LINK", "RETRY_LATER"]) if attempt == 0 else "REMINDER"
        
        elif reason == "bank_timeout":
            # Timeout segment: retry quickly
            return "RETRY_NOW" if attempt == 0 else "PAYMENT_LINK"
            
        return "PAYMENT_LINK"

    @staticmethod
    def _roll_simulated_outcome(action: RecoveryAction, case: RevenueRiskCase, sim_rand: random.Random) -> bool:
        """
        Observable-only conversion model.
        The probability of recovery depends STRICTLY on observable inputs (action type and case segments).
        It does NOT favor AI actions arbitrarily just because they are in the AI group.
        """
        action_type = action.action_type.upper()
        reason = (case.failure_reason or "").lower()

        # Static action-effectiveness conversion matrix mapping action + reason combinations
        base_probs = {
            "PAYMENT_LINK": {"expired_card": 0.70, "insufficient_funds": 0.40, "bank_timeout": 0.55},
            "RETRY_NOW": {"expired_card": 0.00, "insufficient_funds": 0.15, "bank_timeout": 0.45},
            "RETRY_LATER": {"expired_card": 0.00, "insufficient_funds": 0.20, "bank_timeout": 0.40},
            "REMINDER": {"expired_card": 0.05, "insufficient_funds": 0.10, "bank_timeout": 0.10},
            "HUMAN_ESCALATION": {"expired_card": 0.30, "insufficient_funds": 0.25, "bank_timeout": 0.20},
            "STOP": {"expired_card": 0.00, "insufficient_funds": 0.00, "bank_timeout": 0.00}
        }

        # Fallback values for missing reasons/actions
        default_probs = {
            "PAYMENT_LINK": 0.50,
            "RETRY_NOW": 0.15,
            "RETRY_LATER": 0.15,
            "REMINDER": 0.08,
            "HUMAN_ESCALATION": 0.20,
            "STOP": 0.00
        }

        base_p = base_probs.get(action_type, {}).get(reason, default_probs.get(action_type, 0.0))

        # Adjust based on historical success rate of the customer profile
        customer = action.case.customer if hasattr(action, "case") and action.case else None
        success_rate = 0.80
        if customer and customer.metadata_json:
            success_rate = customer.metadata_json.get("historical_success_rate", 0.80)

        # Decayed probability based on attempts made
        decay = (0.7) ** max(0, case.recovery_attempts - 1)
        
        final_prob = base_p * success_rate * decay
        # Generate a deterministic roll seed based on the payment's synthetic ID and current attempt number
        # so matched case clones (A/B) evaluate the exact same roll for their respective actions.
        import hashlib
        seed_str = f"roll_seed_{case.payment_id}_{case.recovery_attempts}"
        h = hashlib.sha256(seed_str.encode("utf-8")).digest()
        roll_seed = int.from_bytes(h[:4], byteorder="big")
        roll_rand = random.Random(roll_seed)
        roll = roll_rand.random()

        logger.info(
            "evaluation_conversion_roll", 
            case_id=case.id, action_type=action_type, reason=reason, 
            final_prob=round(final_prob, 3), roll=round(roll, 3), success=(roll <= final_prob)
        )
        return roll <= final_prob

    @staticmethod
    def _record_simulated_outcome(db: Session, case: RevenueRiskCase, action: RecoveryAction, is_recovered: bool) -> None:
        """
        Creates and writes simulated outcomes marked as OFFLINE_SIMULATION.
        Ensures zero contamination with real verified production outcomes.
        """
        recovered_amount = case.amount_at_risk if is_recovered else 0
        outcome = RecoveryOutcome(
            case_id=case.id,
            action_id=action.id,
            recovered_amount=recovered_amount,
            is_recovered=is_recovered,
            verification_source="OFFLINE_SIMULATION",
            raw_verification_data={
                "payment_id": f"pay_sim_{case.id}_{action.id}",
                "payment_link_id": f"plink_sim_{case.id}",
                "payment_method": "simulated",
                "time_to_recovery_seconds": float(case.recovery_attempts * 3600),
                "strategy_group": case.recovery_strategy_group
            },
            created_at=datetime.utcnow()
        )
        db.add(outcome)
        
        # Transition case terminal states
        if is_recovered:
            case.current_state = "RECOVERED"
        else:
            case.current_state = "NOT_RECOVERED" if case.recovery_attempts >= case.max_attempts else "STOPPED"
        
        case.updated_at = datetime.utcnow()

        audit = AuditLog(
            case_id=case.id,
            event_name="RECOVERY_OUTCOME_RESOLVED",
            description=f"Simulated outcome recorded. Recovered: {is_recovered}.",
            metadata_json=outcome.raw_verification_data,
            timestamp=datetime.utcnow()
        )
        db.add(audit)

    @staticmethod
    def _binomial_exact_p_value(b: int, c: int) -> float:
        """
        Computes the exact two-tailed binomial probability for mismatched pairs.
        Used for McNemar paired testing when sample size / discordant pairs are small.
        """
        n = b + c
        if n == 0:
            return 1.0
            
        mismatched_p = 0.0
        expected = n / 2.0
        observed_diff = abs(b - expected)
        
        for k in range(n + 1):
            if abs(k - expected) >= observed_diff:
                mismatched_p += math.comb(n, k) * (0.5 ** n)
                
        return min(1.0, mismatched_p)
