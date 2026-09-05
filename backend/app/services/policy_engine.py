from datetime import datetime
from typing import Tuple
from sqlalchemy.orm import Session
from app.models.models import RevenueRiskCase, Customer, Payment, RecoveryAction
from app.core.logging import logger

class PolicyEngineService:
    @staticmethod
    def validate_action(db: Session, case: RevenueRiskCase, action: RecoveryAction) -> Tuple[bool, str]:
        """
        Validates the proposed recovery action against deterministic safety policies.
        Returns a tuple of (is_approved, block_reason).
        """
        logger.info(
            "policy_engine_evaluating_action", 
            case_id=case.id, 
            action_type=action.action_type,
            attempts=case.recovery_attempts
        )

        action_type = action.action_type.upper()

        # 1. Rule: Already Recovered
        # If the case state is RECOVERED or the payment status is captured, block execution.
        if case.current_state == "RECOVERED":
            return False, "PAYMENT_ALREADY_RECOVERED"
        
        if case.payment_id:
            payment = db.query(Payment).filter(Payment.id == case.payment_id).first()
            if payment and payment.status == "captured":
                return False, "PAYMENT_ALREADY_RECOVERED"

        # 2. Rule: Customer Opt-out
        # If the customer has explicitly opted out of communications, block execution.
        customer = db.query(Customer).filter(Customer.id == case.customer_id).first()
        if customer and customer.metadata_json and customer.metadata_json.get("opted_out") is True:
            return False, "CUSTOMER_OPTED_OUT"

        # 3. Rule: Retry Limit Exceeded
        # Do not allow active recovery if attempt bounds are hit (unless action is STOP or ESCALATION)
        if action_type not in ["STOP", "HUMAN_ESCALATION"]:
            if case.recovery_attempts >= case.max_attempts:
                return False, "RECOVERY_ATTEMPTS_EXCEEDED"

        # 4. Rule: Recovery Time-Window Expired
        if action_type not in ["STOP"]:
            created_at = case.created_at or datetime.utcnow()
            elapsed_seconds = (datetime.utcnow() - created_at).total_seconds()
            
            reason = (case.failure_reason or "").lower()
            event_type = case.event_type.upper()

            # Assign maximum time bounds based on profile types
            if "checkout_abandoned" in reason or event_type == "CHECKOUT_ABANDONMENT":
                limit_seconds = 2 * 3600  # 2 hours
            elif "overdue" in reason or "payment_terms" in reason:
                limit_seconds = 30 * 24 * 3600  # 30 days
            else:
                limit_seconds = 7 * 24 * 3600  # 7 days

            if elapsed_seconds > limit_seconds:
                return False, "RECOVERY_WINDOW_EXPIRED"

        # 5. Rule: High-value Transaction review escalation
        # If the amount at risk is >= ₹100,000 (100,000,00 paisa) and it hasn't been escalated, route to human review.
        if action_type not in ["HUMAN_ESCALATION", "STOP"]:
            if case.amount_at_risk >= 10000000:
                return False, "HIGH_VALUE_REQUIRES_HUMAN_ESCALATION"

        # 6. Rule: Supported Action Types Gating
        # Direct payment retries are unsupported without card/UPI tokenization agreements.
        SUPPORTED_ACTIONS = ["PAYMENT_LINK", "REMINDER", "HUMAN_ESCALATION", "STOP"]
        
        customer = db.query(Customer).filter(Customer.id == case.customer_id).first()
        has_recurring_consent = False
        if customer and customer.metadata_json:
            has_recurring_consent = customer.metadata_json.get("is_subscribed") is True

        allowed_actions = SUPPORTED_ACTIONS
        if has_recurring_consent:
            allowed_actions = SUPPORTED_ACTIONS + ["RETRY_NOW", "RETRY_LATER"]

        if action_type not in allowed_actions:
            if action_type in ["RETRY_NOW", "RETRY_LATER"]:
                return False, "RETRIES_NOT_SUPPORTED_WITHOUT_RECURRING_CONSENT"
            return False, "UNSUPPORTED_ACTION_TYPE"

        # If no rules are violated, the action is approved for execution.
        return True, "APPROVED"
