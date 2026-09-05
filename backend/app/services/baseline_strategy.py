from datetime import datetime
from typing import Optional, Tuple, Dict, Any
from sqlalchemy.orm import Session
from app.models.models import Customer, RevenueRiskCase, RecoveryAction, AuditLog
from app.core.logging import logger
from app.services.risk_engine import calculate_recovery_probability

class BaselineStrategyService:
    @staticmethod
    def determine_next_action(failure_reason: str, attempts: int) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        Determines the next recovery action and parameters based on attempts and failure categories.
        """
        reason = failure_reason.lower()

        # 1. B2B Invoice Overdue
        if "overdue" in reason or "payment_terms_overdue" in reason:
            if attempts == 0:
                return "PAYMENT_LINK", {"type": "invoice_reminder", "link_expiry_hours": 48}
            elif attempts == 1:
                return "HUMAN_ESCALATION", {"escalation_reason": "B2B payment overdue retry exhausted"}
            else:
                return "STOP", None

        # 2. Expired Card
        elif "expired_card" in reason:
            if attempts == 0:
                return "PAYMENT_LINK", {"type": "update_billing_reminder", "link_expiry_hours": 24}
            else:
                return "STOP", None

        # 3. Checkout Abandoned
        elif "checkout_abandoned" in reason:
            if attempts == 0:
                return "PAYMENT_LINK", {"type": "cart_reminder", "link_expiry_hours": 24}
            else:
                return "STOP", None

        # 4. Soft failures (insufficient funds, bank timeout, network failure)
        elif "insufficient_funds" in reason or "bank_timeout" in reason or "network_failure" in reason:
            if attempts == 0:
                return "RETRY_NOW", None
            elif attempts == 1:
                return "RETRY_LATER", {"delay_minutes": 120}
            elif attempts == 2:
                return "PAYMENT_LINK", {"type": "email_reminder", "link_expiry_hours": 24}
            else:
                return "STOP", None

        # 5. Default Generic Strategy
        else:
            if attempts == 0:
                return "RETRY_NOW", None
            elif attempts == 1:
                return "PAYMENT_LINK", {"type": "generic_reminder", "link_expiry_hours": 24}
            else:
                return "STOP", None

    @classmethod
    def execute_baseline_step(cls, db: Session, case: RevenueRiskCase) -> RecoveryAction:
        """
        Executes a single step of the deterministic baseline strategy on the case file.
        Updates attempts, applies decay factors to recovery probability, and logs transitions.
        """
        logger.info(
            "executing_baseline_step",
            case_id=case.id,
            attempts=case.recovery_attempts,
            failure_reason=case.failure_reason
        )

        # Force STOP if attempts exceed max allowable bounds
        if case.recovery_attempts >= case.max_attempts:
            action_type = "STOP"
            parameters = None
        else:
            action_type, parameters = cls.determine_next_action(case.failure_reason, case.recovery_attempts)

        # 1. Create RecoveryAction
        action = RecoveryAction(
            case_id=case.id,
            ai_decision_id=None,  # Baseline is fully deterministic, no AI decision
            action_type=action_type,
            parameters=parameters,
            status="PENDING",
            created_at=datetime.utcnow()
        )
        db.add(action)
        db.flush()

        # 2. Update Case State & Metadata
        old_state = case.current_state
        
        # State machine transition rules
        if action_type == "STOP":
            case.current_state = "STOPPED"
        elif action_type == "HUMAN_ESCALATION":
            case.current_state = "ESCALATED"
        elif action_type == "RETRY_NOW":
            case.current_state = "ACTION_EXECUTED"
            action.status = "EXECUTED"
            action.executed_at = datetime.utcnow()
        else:
            case.current_state = "ACTION_SCHEDULED"
            action.status = "SCHEDULED"

        # Apply attempt increment and decay to recovery probability
        case.recovery_attempts += 1
        
        # Fetch historical success rate to compute decayed probability
        customer = db.query(Customer).filter(Customer.id == case.customer_id).first()
        hist_success_rate = customer.metadata_json.get("historical_success_rate", 0.80) if customer else 0.80
        
        case.recovery_probability = calculate_recovery_probability(
            case.failure_reason, 
            hist_success_rate, 
            attempts=case.recovery_attempts
        )
        # Update prioritization score with decayed value
        case.prioritization_score = case.amount_at_risk * case.recovery_probability
        case.updated_at = datetime.utcnow()

        # 3. Log state transitions in Audit Trail
        audit = AuditLog(
            case_id=case.id,
            event_name="BASELINE_ACTION_TRIGGERED",
            description=f"Baseline action {action_type} executed. State transition: {old_state} -> {case.current_state}. Decayed recovery probability: {case.recovery_probability}",
            metadata_json={
                "action_type": action_type,
                "parameters": parameters,
                "old_state": old_state,
                "new_state": case.current_state,
                "attempts": case.recovery_attempts,
                "decayed_probability": case.recovery_probability
            },
            timestamp=datetime.utcnow()
        )
        db.add(audit)
        
        return action
