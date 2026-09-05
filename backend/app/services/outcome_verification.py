from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from app.models.models import RevenueRiskCase, RecoveryAction, RecoveryOutcome, Payment, AuditLog
from app.services.razorpay_service import RazorpayService
from app.core.logging import logger
import sys

class OutcomeVerificationService:
    @staticmethod
    def verify_outcome_from_webhook(db: Session, event_type: str, payload: dict) -> Optional[RecoveryOutcome]:
        """
        Processes webhook payment events to verify recovery outcomes.
        Supports delayed webhook reconciliation without discarding success updates.
        """
        logger.info("webhook_verifying_outcome_started", event_type=event_type)

        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        payment_link_entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {})

        # Extract payment/link details
        payment_id = payment_entity.get("id")
        payment_link_id = payment_link_entity.get("id") or payment_entity.get("payment_link_id")
        amount = payment_entity.get("amount") or payment_link_entity.get("amount_paid")
        currency = payment_entity.get("currency") or payment_link_entity.get("currency")
        method = payment_entity.get("method")

        # 1. Resolve Case & Action
        case = None
        action = None

        if payment_link_id:
            # Match by payment link ID stored inside action parameters
            actions = db.query(RecoveryAction).filter(RecoveryAction.action_type == "PAYMENT_LINK").all()
            for a in actions:
                if a.parameters and a.parameters.get("payment_link_id") == payment_link_id:
                    action = a
                    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.id == a.case_id).first()
                    break

        if not case and payment_id:
            # Fallback matching by payment_id
            case = db.query(RevenueRiskCase).filter(RevenueRiskCase.payment_id == payment_id).first()
            if case:
                action = db.query(RecoveryAction).filter(
                    RecoveryAction.case_id == case.id, 
                    RecoveryAction.status == "EXECUTED"
                ).order_by(RecoveryAction.executed_at.desc()).first()

        if not case or not action:
            logger.warning("webhook_outcome_verification_skipped_unmapped", payment_id=payment_id, payment_link_id=payment_link_id)
            return None

        # 2. Check Event Type & Precedence
        is_success_event = event_type in ["payment.captured", "payment_link.paid"]

        # Already Recovered Precedence check: once RECOVERED, never downgrade
        if case.current_state == "RECOVERED":
            existing_outcome = db.query(RecoveryOutcome).filter(RecoveryOutcome.action_id == action.id).first()
            if existing_outcome:
                logger.info("webhook_outcome_verification_already_resolved", case_id=case.id, outcome_id=existing_outcome.id)
                return existing_outcome
            return None

        event_timestamp_epoch = payload.get("created_at")
        event_dt = datetime.utcfromtimestamp(event_timestamp_epoch) if event_timestamp_epoch else datetime.utcnow()

        if not is_success_event:
            # Stale check for failure events: do not process failure updates if older than case's updated_at
            if case.updated_at and event_dt < case.updated_at:
                logger.info("webhook_stale_failure_ignored", case_id=case.id, event_time=event_dt, case_updated=case.updated_at)
                return None

            # Verify if it is a terminal failure state
            terminal_failures = ["payment.failed", "payment_link.expired", "payment_link.cancelled"]
            if event_type in terminal_failures:
                # Enforce idempotency
                existing_outcome = db.query(RecoveryOutcome).filter(RecoveryOutcome.action_id == action.id).first()
                if existing_outcome:
                    return existing_outcome

                outcome = RecoveryOutcome(
                    case_id=case.id,
                    action_id=action.id,
                    recovered_amount=0,
                    is_recovered=False,
                    verification_source="WEBHOOK",
                    raw_verification_data={
                        "payment_id": payment_id,
                        "payment_link_id": payment_link_id,
                        "reason": f"Terminal event {event_type} received.",
                        "strategy_group": case.recovery_strategy_group
                    },
                    created_at=datetime.utcnow()
                )
                db.add(outcome)
                case.current_state = "NOT_RECOVERED"
                case.updated_at = datetime.utcnow()

                audit = AuditLog(
                    case_id=case.id,
                    event_name="RECOVERY_OUTCOME_RESOLVED",
                    description=f"Action failed verification via webhook: {event_type}.",
                    metadata_json=outcome.raw_verification_data,
                    timestamp=datetime.utcnow()
                )
                db.add(audit)
                db.commit()
                return outcome
            return None

        # 3. Success Gate Validations
        # A. Currency validation
        if currency != "INR":
            logger.warning("webhook_outcome_currency_mismatch", case_id=case.id, currency=currency)
            audit = AuditLog(
                case_id=case.id,
                event_name="RECOVERY_CURRENCY_MISMATCH",
                description=f"Payment currency {currency} is unsupported. Expected INR.",
                metadata_json={"payload": payload},
                timestamp=datetime.utcnow()
            )
            db.add(audit)
            db.commit()
            return None

        # B. Amount validation
        if amount != case.amount_at_risk:
            logger.error("webhook_outcome_amount_mismatch", case_id=case.id, expected=case.amount_at_risk, actual=amount)
            audit = AuditLog(
                case_id=case.id,
                event_name="RECOVERY_AMOUNT_MISMATCH",
                description=f"Mismatched recovered amount: received {amount} paisa, expected {case.amount_at_risk}.",
                metadata_json={"expected": case.amount_at_risk, "actual": amount, "payload": payload},
                timestamp=datetime.utcnow()
            )
            db.add(audit)
            db.commit()
            return None

        # 4. Record Successful Outcome
        # Idempotency double check
        outcome = db.query(RecoveryOutcome).filter(RecoveryOutcome.action_id == action.id).first()
        if not outcome:
            time_to_recovery = (event_dt - (case.created_at or event_dt)).total_seconds()
            outcome = RecoveryOutcome(
                case_id=case.id,
                action_id=action.id,
                recovered_amount=amount,
                is_recovered=True,
                verification_source="WEBHOOK",
                raw_verification_data={
                    "payment_id": payment_id,
                    "payment_link_id": payment_link_id,
                    "payment_method": method,
                    "currency": currency,
                    "time_to_recovery_seconds": max(0.0, time_to_recovery),
                    "strategy_group": case.recovery_strategy_group
                },
                created_at=datetime.utcnow()
            )
            db.add(outcome)

            # Update Payment status
            if case.payment_id:
                payment = db.query(Payment).filter(Payment.id == case.payment_id).first()
                if payment:
                    payment.status = "captured"
                    payment.updated_at = datetime.utcnow()

            # Transition case
            case.current_state = "RECOVERED"
            case.updated_at = datetime.utcnow()

            audit = AuditLog(
                case_id=case.id,
                event_name="RECOVERY_OUTCOME_RESOLVED",
                description=f"Recovery verified via webhook. Amount: {amount} paisa.",
                metadata_json=outcome.raw_verification_data,
                timestamp=datetime.utcnow()
            )
            db.add(audit)
            db.commit()

        return outcome

    @staticmethod
    def poll_and_verify_action_outcome(db: Session, case_id: int) -> Optional[RecoveryOutcome]:
        """
        Active polling verification checking Razorpay API for link/payment state updates.
        Does not mark NOT_RECOVERED for pending/created links.
        """
        logger.info("polling_verification_outcome_started", case_id=case_id)

        case = db.query(RevenueRiskCase).filter(RevenueRiskCase.id == case_id).first()
        if not case:
            return None

        # Log OUTCOME_VERIFICATION_STARTED
        db.add(AuditLog(
            case_id=case.id,
            event_name="OUTCOME_VERIFICATION_STARTED",
            description="Outcome verification process started.",
            timestamp=datetime.utcnow()
        ))
        db.flush()

        if case.current_state == "RECOVERED":
            return db.query(RecoveryOutcome).filter(RecoveryOutcome.case_id == case.id, RecoveryOutcome.is_recovered == True).first()

        action = db.query(RecoveryAction).filter(
            RecoveryAction.case_id == case.id,
            RecoveryAction.status == "EXECUTED"
        ).order_by(RecoveryAction.executed_at.desc()).first()

        if not action:
            logger.warning("polling_outcome_no_executed_action", case_id=case_id)
            return None

        # Enforce idempotency
        existing_outcome = db.query(RecoveryOutcome).filter(RecoveryOutcome.action_id == action.id).first()
        if existing_outcome:
            return existing_outcome

        rzp = RazorpayService()

        # 1. Action: PAYMENT_LINK
        if action.parameters and "payment_link_id" in action.parameters:
            payment_link_id = action.parameters["payment_link_id"]
            try:
                # Fetch payment link details from Razorpay SDK directly
                link_details = rzp.client.payment_link.fetch(payment_link_id)
                status = link_details.get("status")

                if status == "paid":
                    amount = link_details.get("amount_paid")
                    currency = link_details.get("currency")
                    
                    # Success Gate Validations
                    if currency != "INR":
                        logger.warning("polling_outcome_currency_mismatch", case_id=case.id, currency=currency)
                        db.add(AuditLog(
                            case_id=case.id, event_name="RECOVERY_CURRENCY_MISMATCH",
                            description=f"Polling currency {currency} is unsupported. Expected INR.",
                            timestamp=datetime.utcnow()
                        ))
                        db.commit()
                        return None

                    if amount != case.amount_at_risk:
                        logger.error("polling_outcome_amount_mismatch", case_id=case.id, expected=case.amount_at_risk, actual=amount)
                        db.add(AuditLog(
                            case_id=case.id, event_name="RECOVERY_AMOUNT_MISMATCH",
                            description=f"Polling mismatched amount: received {amount}, expected {case.amount_at_risk}.",
                            timestamp=datetime.utcnow()
                        ))
                        db.commit()
                        return None

                    # Extract payment ID from link details if present
                    payments = link_details.get("payments", [])
                    payment_id = payments[0].get("payment_id") if payments else "polling_checkout"

                    time_to_recovery = (datetime.utcnow() - (case.created_at or datetime.utcnow())).total_seconds()
                    outcome = RecoveryOutcome(
                        case_id=case.id,
                        action_id=action.id,
                        recovered_amount=amount,
                        is_recovered=True,
                        verification_source="API_CHECK",
                        raw_verification_data={
                            "payment_id": payment_id,
                            "payment_link_id": payment_link_id,
                            "payment_method": "payment_link",
                            "currency": currency,
                            "time_to_recovery_seconds": max(0.0, time_to_recovery),
                            "strategy_group": case.recovery_strategy_group
                        },
                        created_at=datetime.utcnow()
                    )
                    db.add(outcome)

                    # Update Payment status
                    if case.payment_id:
                        payment = db.query(Payment).filter(Payment.id == case.payment_id).first()
                        if payment:
                            payment.status = "captured"
                            payment.updated_at = datetime.utcnow()

                    case.current_state = "RECOVERED"
                    case.updated_at = datetime.utcnow()

                    db.add(AuditLog(
                        case_id=case.id,
                        event_name="RECOVERED",
                        description=f"Case status updated to RECOVERED.",
                        timestamp=datetime.utcnow()
                    ))

                    audit = AuditLog(
                        case_id=case.id,
                        event_name="RECOVERY_OUTCOME_RESOLVED",
                        description=f"Recovery verified via polling. Amount: {amount} paisa.",
                        metadata_json=outcome.raw_verification_data,
                        timestamp=datetime.utcnow()
                    )
                    db.add(audit)
                    db.commit()
                    return outcome

                elif status in ["expired", "cancelled"]:
                    outcome = RecoveryOutcome(
                        case_id=case.id,
                        action_id=action.id,
                        recovered_amount=0,
                        is_recovered=False,
                        verification_source="API_CHECK",
                        raw_verification_data={
                            "payment_link_id": payment_link_id,
                            "reason": f"Link is terminally {status}.",
                            "strategy_group": case.recovery_strategy_group
                        },
                        created_at=datetime.utcnow()
                    )
                    db.add(outcome)
                    case.current_state = "NOT_RECOVERED"
                    case.updated_at = datetime.utcnow()

                    db.add(AuditLog(
                        case_id=case.id,
                        event_name="NOT_RECOVERED",
                        description=f"Case status updated to NOT_RECOVERED.",
                        timestamp=datetime.utcnow()
                    ))

                    audit = AuditLog(
                        case_id=case.id,
                        event_name="RECOVERY_OUTCOME_RESOLVED",
                        description=f"Action failed polling verification: Link was {status}.",
                        metadata_json=outcome.raw_verification_data,
                        timestamp=datetime.utcnow()
                    )
                    db.add(audit)
                    db.commit()
                    return outcome

                else:
                    # Still in created / non-terminal state: do nothing and do not write outcome
                    logger.info("polling_outcome_still_pending", case_id=case.id, link_status=status)
                    db.add(AuditLog(
                        case_id=case.id,
                        event_name="OUTCOME_PENDING",
                        description=f"Outcome verification pending: link is still in state {status}.",
                        timestamp=datetime.utcnow()
                    ))
                    db.commit()
                    return None

            except Exception as e:
                logger.error("polling_verification_link_failed", case_id=case.id, error=str(e))
                return None

        # 2. General/Reminders: check Payment status directly
        elif case.payment_id:
            try:
                # Fetch payment details from Razorpay SDK directly
                payment_details = rzp.get_payment_details(case.payment_id)
                status = payment_details.status

                if status == "captured":
                    amount = payment_details.amount
                    
                    if amount != case.amount_at_risk:
                        logger.error("polling_outcome_direct_amount_mismatch", case_id=case.id, expected=case.amount_at_risk, actual=amount)
                        return None

                    time_to_recovery = (datetime.utcnow() - (case.created_at or datetime.utcnow())).total_seconds()
                    outcome = RecoveryOutcome(
                        case_id=case.id,
                        action_id=action.id,
                        recovered_amount=amount,
                        is_recovered=True,
                        verification_source="API_CHECK",
                        raw_verification_data={
                            "payment_id": case.payment_id,
                            "payment_method": "direct",
                            "time_to_recovery_seconds": max(0.0, time_to_recovery),
                            "strategy_group": case.recovery_strategy_group
                        },
                        created_at=datetime.utcnow()
                    )
                    db.add(outcome)

                    # Update Payment status
                    payment = db.query(Payment).filter(Payment.id == case.payment_id).first()
                    if payment:
                        payment.status = "captured"
                        payment.updated_at = datetime.utcnow()

                    case.current_state = "RECOVERED"
                    case.updated_at = datetime.utcnow()

                    db.add(AuditLog(
                        case_id=case.id,
                        event_name="RECOVERED",
                        description=f"Case status updated to RECOVERED.",
                        timestamp=datetime.utcnow()
                    ))

                    audit = AuditLog(
                        case_id=case.id,
                        event_name="RECOVERY_OUTCOME_RESOLVED",
                        description="Recovery verified via direct payment status check.",
                        metadata_json=outcome.raw_verification_data,
                        timestamp=datetime.utcnow()
                    )
                    db.add(audit)
                    db.commit()
                    return outcome
                elif status in ["failed", "refunded"]:
                    outcome = RecoveryOutcome(
                        case_id=case.id,
                        action_id=action.id,
                        recovered_amount=0,
                        is_recovered=False,
                        verification_source="API_CHECK",
                        raw_verification_data={
                            "payment_id": case.payment_id,
                            "reason": f"Payment is terminally {status}.",
                            "strategy_group": case.recovery_strategy_group
                        },
                        created_at=datetime.utcnow()
                    )
                    db.add(outcome)
                    case.current_state = "NOT_RECOVERED"
                    case.updated_at = datetime.utcnow()

                    db.add(AuditLog(
                        case_id=case.id,
                        event_name="NOT_RECOVERED",
                        description=f"Direct payment retry terminally failed: status is {status}.",
                        timestamp=datetime.utcnow()
                    ))
                    db.commit()
                    return outcome
                else:
                    db.add(AuditLog(
                        case_id=case.id,
                        event_name="OUTCOME_PENDING",
                        description=f"Outcome verification pending: payment is in state {status}.",
                        timestamp=datetime.utcnow()
                    ))
                    db.commit()
                    return None

            except Exception as e:
                logger.error("polling_verification_payment_failed", case_id=case.id, error=str(e))
                return None

        return None
