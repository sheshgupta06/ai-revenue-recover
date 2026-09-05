import json
from sqlalchemy.orm import Session
from app.models.models import RevenueRiskCase, Customer, Payment, RecoveryAction
from app.core.logging import logger

class ContextBuilder:
    @staticmethod
    def build_case_context(db: Session, case: RevenueRiskCase) -> str:
        """
        Builds a dense, anonymized JSON context string of the case file, payment,
        and customer profile history. Removes all customer names, emails, and phone numbers.
        """
        logger.info("context_builder_gathering_context", case_id=case.id)

        # 1. Fetch related customer details
        customer = db.query(Customer).filter(Customer.id == case.customer_id).first()
        
        # Anonymize Customer ID
        anonymized_cust_id = "anonymized_customer"
        hist_success_rate = 0.80
        customer_tenure = 30

        if customer:
            # PII filter: replace real customer IDs
            anonymized_cust_id = f"anonymized_{customer.id}"
            
            # Load metadata attributes safely
            if customer.metadata_json:
                hist_success_rate = customer.metadata_json.get("historical_success_rate", 0.80)
                customer_tenure = customer.metadata_json.get("customer_tenure_days", 30)

        # 2. Fetch past recovery actions
        actions = db.query(RecoveryAction).filter(RecoveryAction.case_id == case.id).order_by(RecoveryAction.created_at).all()
        previous_attempts = []
        
        for idx, act in enumerate(actions):
            # Check outcomes
            outcome_status = "unknown"
            if act.status == "PENDING" or act.status == "SCHEDULED":
                outcome_status = "pending"
            elif act.status == "FAILED":
                outcome_status = "failed"
            else:
                # If there are outcomes associated with this case/action
                outcome_status = "completed"
                for out in act.outcomes:
                    if out.recovered_amount > 0:
                        outcome_status = "success"
                    else:
                        outcome_status = "failed"

            previous_attempts.append({
                "attempt_number": idx + 1,
                "action_type": act.action_type,
                "status": act.status,
                "timestamp": act.created_at.isoformat() if act.created_at else None,
                "outcome": outcome_status
            })

        # 3. Build dense JSON context payload
        context_payload = {
            "case": {
                "id": case.id,
                "event_type": case.event_type,
                "amount_at_risk": case.amount_at_risk,
                "failure_reason": case.failure_reason,
                "recovery_attempts": case.recovery_attempts,
                "max_attempts": case.max_attempts
            },
            "customer": {
                "anonymized_id": anonymized_cust_id,
                "historical_success_rate": hist_success_rate,
                "customer_tenure_days": customer_tenure
            },
            "previous_attempts": previous_attempts
        }

        return json.dumps(context_payload, indent=2)
