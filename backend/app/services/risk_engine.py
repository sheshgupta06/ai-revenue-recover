from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from app.models.models import Payment, Customer, RevenueRiskCase, AuditLog
from app.core.logging import logger

def calculate_loss_risk_score(failure_reason: Optional[str], attempt_number: int = 1) -> float:
    """
    Deterministically computes the probability that the revenue will become permanently unrecoverable
    based on the failure category. Does not use AI.
    """
    if not failure_reason:
        return 0.40

    reason = failure_reason.lower()
    
    if "expired_card" in reason or "invalid_card" in reason:
        return 0.85
    elif attempt_number > 1:
        return 0.70
    elif "checkout_abandoned" in reason:
        return 0.60
    elif "insufficient_funds" in reason:
        return 0.30
    elif "bank_timeout" in reason or "network_failure" in reason:
        return 0.10
    else:
        return 0.40

def calculate_recovery_probability(
    failure_reason: Optional[str], 
    historical_success_rate: float, 
    attempts: int = 0
) -> float:
    """
    Estimates the recovery probability dynamically based on failure types, customer profile history,
    and attempt decay. Does not use AI.
    """
    if not failure_reason:
        base_factor = 0.50
    else:
        reason = failure_reason.lower()
        if "bank_timeout" in reason or "network_failure" in reason:
            base_factor = 0.95
        elif "insufficient_funds" in reason:
            base_factor = 0.70
        elif "checkout_abandoned" in reason:
            base_factor = 0.45
        elif "expired_card" in reason:
            base_factor = 0.20
        else:
            base_factor = 0.50

    # Calculate probability and apply 30% decay per attempt
    prob = base_factor * historical_success_rate * (0.7 ** attempts)
    return round(max(0.0, min(1.0, prob)), 4)

class RiskEngineService:
    @staticmethod
    def create_or_update_recovery_case(
        db: Session,
        payment_id: Optional[str] = None,
        event_type: str = "FAILED_PAYMENT",
        customer_id: Optional[str] = None,
        amount: Optional[int] = None,
        failure_reason: Optional[str] = None,
        is_synthetic: bool = False,
        strategy_group: str = "BASELINE"
    ) -> RevenueRiskCase:
        """
        Ingests a payment failure or checkout abandonment, calculates risk scores, 
        creates/updates the case file, and records audit logs.
        """
        logger.info(
            "risk_engine_processing_case",
            payment_id=payment_id,
            event_type=event_type,
            strategy_group=strategy_group
        )

        payment = None
        customer = None
        attempt_number = 1

        # 1. Resolve payment and customer dependencies
        if payment_id:
            payment = db.query(Payment).filter(Payment.id == payment_id).first()
            if payment:
                customer_id = payment.customer_id
                amount = payment.amount
                failure_reason = payment.failure_reason
                is_synthetic = payment.is_synthetic
                
                # Retrieve attempt count from payment metadata if present
                if payment.metadata_json:
                    attempt_number = payment.metadata_json.get("payment_attempt_number", 1)

        if customer_id:
            customer = db.query(Customer).filter(Customer.id == customer_id).first()

        # Fallbacks for missing data
        if amount is None:
            amount = 0
        if failure_reason is None:
            failure_reason = "unknown_failure"

        # 2. Extract historical success rate from Customer metadata
        hist_success_rate = 0.80
        if customer and customer.metadata_json:
            hist_success_rate = customer.metadata_json.get("historical_success_rate", 0.80)

        # 3. Calculate Scoring Metrics
        loss_risk = calculate_loss_risk_score(failure_reason, attempt_number)
        rec_prob = calculate_recovery_probability(failure_reason, hist_success_rate, attempts=0)
        prioritization_score = amount * rec_prob

        # Assign risk level category
        if loss_risk < 0.30:
            risk_level = "LOW"
        elif loss_risk < 0.70:
            risk_level = "MEDIUM"
        else:
            risk_level = "HIGH"

        # 4. Check if case already exists (deduplication)
        case = None
        if payment_id:
            case = db.query(RevenueRiskCase).filter(
                RevenueRiskCase.payment_id == payment_id,
                RevenueRiskCase.recovery_strategy_group == strategy_group,
            ).first()

        if case:
            # Update existing case details
            case.loss_risk_score = loss_risk
            case.recovery_probability = rec_prob
            case.prioritization_score = prioritization_score
            case.risk_level = risk_level
            case.updated_at = datetime.utcnow()
            logger.info("risk_engine_case_updated", case_id=case.id, prioritization_score=prioritization_score)
        else:
            # Create a brand new case file
            case = RevenueRiskCase(
                payment_id=payment_id,
                customer_id=customer_id,
                merchant_id=payment.merchant_id if payment else "mer_synth_001",
                amount_at_risk=amount,
                event_type=event_type,
                current_state="NEW",
                failure_reason=failure_reason,
                risk_level=risk_level,
                loss_risk_score=loss_risk,
                recovery_probability=rec_prob,
                prioritization_score=prioritization_score,
                recovery_strategy_group=strategy_group,
                recovery_attempts=0,
                max_attempts=3,
                is_synthetic=is_synthetic,
                dataset_type=payment.dataset_type if payment else "EVALUATION",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.add(case)
            db.flush()  # Populates case.id for audit log
            logger.info("risk_engine_case_created", case_id=case.id, prioritization_score=prioritization_score)

        # 5. Write audit trail event
        audit = AuditLog(
            case_id=case.id,
            event_name="CASE_CREATED" if not payment_id or case.created_at == case.updated_at else "CASE_UPDATED",
            description=f"Revenue risk evaluated. Loss Risk: {loss_risk}, Recovery Prob: {rec_prob}, Prioritization Score: {prioritization_score}",
            metadata_json={
                "loss_risk_score": loss_risk,
                "recovery_probability": rec_prob,
                "prioritization_score": prioritization_score,
                "risk_level": risk_level,
                "strategy_group": strategy_group
            },
            timestamp=datetime.utcnow()
        )
        db.add(audit)
        
        return case
