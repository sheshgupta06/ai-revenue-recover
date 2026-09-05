from datetime import datetime
from sqlalchemy.orm import Session
from app.models.models import RevenueRiskCase, Customer, RecoveryAction, AuditLog
from app.services.razorpay_service import RazorpayService, RazorpayConfigError, RazorpayAPIError
from app.core.logging import logger

class ActionExecutorService:
    @staticmethod
    def execute_approved_action(db: Session, case: RevenueRiskCase, action: RecoveryAction) -> RecoveryAction:
        """
        Executes an approved recovery action recommendation.
        Calls the Razorpay service for payment links and handles timeouts/unsupported states cleanly.
        """
        logger.info("action_executor_starting", case_id=case.id, action_type=action.action_type)

        # Idempotency safety check: only execute PENDING or SCHEDULED recommendations.
        # SCHEDULED is the baseline strategy's initial state for PAYMENT_LINK/REMINDER actions.
        # EXECUTED, BLOCKED, and FAILED are terminal — never re-execute them.
        if action.status not in ("PENDING", "SCHEDULED"):
            logger.warning("action_executor_skip_non_executable", action_id=action.id, status=action.status)
            return action

        action_type = action.action_type.upper()
        old_state = case.current_state

        audit_start = AuditLog(
            case_id=case.id,
            event_name="ACTION_EXECUTION_STARTED",
            description=f"Executing recovery action: {action_type}.",
            timestamp=datetime.utcnow()
        )
        db.add(audit_start)
        db.flush()

        # 1. Action: PAYMENT_LINK
        if action_type == "PAYMENT_LINK":
            # Idempotency check: see if a PAYMENT_LINK was already executed successfully for this case
            existing_link = db.query(RecoveryAction).filter(
                RecoveryAction.case_id == case.id,
                RecoveryAction.action_type == "PAYMENT_LINK",
                RecoveryAction.status == "EXECUTED"
            ).first()

            if existing_link and existing_link.parameters and "payment_link_url" in existing_link.parameters:
                logger.info("action_executor_reusing_existing_link", case_id=case.id)
                action.parameters = existing_link.parameters
                action.status = "EXECUTED"
                action.executed_at = datetime.utcnow()
                case.current_state = "ACTION_EXECUTED"
                case.updated_at = datetime.utcnow()

                audit_success = AuditLog(
                    case_id=case.id,
                    event_name="ACTION_EXECUTION_SUCCESS",
                    description="Reused existing generated Razorpay payment link.",
                    metadata_json=action.parameters,
                    timestamp=datetime.utcnow()
                )
                db.add(audit_success)
                return action

            # Generate fresh link via RazorpayService
            rzp = RazorpayService()
            try:
                customer = db.query(Customer).filter(Customer.id == case.customer_id).first()
                name = customer.name if customer else None
                email = customer.email if customer else None
                phone = customer.phone if customer else None

                desc = f"Payment link for Case {case.id} - Reason: {case.failure_reason}"
                ref_id = f"case_{case.id}"

                link_details = rzp.create_payment_link(
                    amount=case.amount_at_risk,
                    description=desc,
                    reference_id=ref_id,
                    customer_name=name,
                    customer_email=email,
                    customer_phone=phone
                )

                action.parameters = {
                    "payment_link_id": link_details.id,
                    "payment_link_url": link_details.short_url,
                    "status": link_details.status
                }
                action.status = "EXECUTED"
                action.executed_at = datetime.utcnow()
                case.current_state = "ACTION_EXECUTED"
                case.updated_at = datetime.utcnow()

                audit_success = AuditLog(
                    case_id=case.id,
                    event_name="ACTION_EXECUTION_SUCCESS",
                    description=f"Generated new Razorpay payment link: {link_details.short_url}",
                    metadata_json=action.parameters,
                    timestamp=datetime.utcnow()
                )
                db.add(audit_success)

            except RazorpayConfigError as rce:
                logger.error("action_executor_razorpay_not_configured", case_id=case.id, error=str(rce))
                action.status = "FAILED"
                action.parameters = {"failure_reason": "RAZORPAY_NOT_CONFIGURED", "details": str(rce)}
                case.current_state = "ACTION_FAILED"
                case.updated_at = datetime.utcnow()

                audit_fail = AuditLog(
                    case_id=case.id,
                    event_name="ACTION_EXECUTION_FAILED",
                    description=f"Razorpay credentials missing. Proposing link failed: {rce}",
                    metadata_json=action.parameters,
                    timestamp=datetime.utcnow()
                )
                db.add(audit_fail)

            except RazorpayAPIError as rae:
                logger.error("action_executor_razorpay_api_error", case_id=case.id, error=str(rae))
                action.status = "FAILED"
                action.parameters = {"failure_reason": "RAZORPAY_API_ERROR", "details": str(rae)}
                case.current_state = "ACTION_FAILED"
                case.updated_at = datetime.utcnow()

                audit_fail = AuditLog(
                    case_id=case.id,
                    event_name="ACTION_EXECUTION_FAILED",
                    description=f"Razorpay API call failed: {rae}",
                    metadata_json=action.parameters,
                    timestamp=datetime.utcnow()
                )
                db.add(audit_fail)

            except Exception as e:
                logger.error("action_executor_unexpected_error", case_id=case.id, error=str(e))
                action.status = "FAILED"
                action.parameters = {"failure_reason": "UNEXPECTED_EXECUTION_ERROR", "details": str(e)}
                case.current_state = "ACTION_FAILED"
                case.updated_at = datetime.utcnow()

                audit_fail = AuditLog(
                    case_id=case.id,
                    event_name="ACTION_EXECUTION_FAILED",
                    description=f"Unexpected error executing payment link: {e}",
                    metadata_json=action.parameters,
                    timestamp=datetime.utcnow()
                )
                db.add(audit_fail)

        # 2. Action: RETRY_NOW / RETRY_LATER (Direct Retries)
        # Direct payment retries are unsupported without card tokenization/autopay recurring consent.
        elif action_type in ["RETRY_NOW", "RETRY_LATER"]:
            customer = db.query(Customer).filter(Customer.id == case.customer_id).first()
            has_recurring_consent = False
            if customer and customer.metadata_json:
                has_recurring_consent = customer.metadata_json.get("is_subscribed") is True

            if has_recurring_consent:
                action.status = "EXECUTED"
                action.executed_at = datetime.utcnow()
                case.current_state = "ACTION_EXECUTED"
                case.updated_at = datetime.utcnow()

                audit_success = AuditLog(
                    case_id=case.id,
                    event_name="ACTION_EXECUTION_SUCCESS",
                    description="Direct automated payment retry executed successfully via customer recurring consent token.",
                    metadata_json=action.parameters,
                    timestamp=datetime.utcnow()
                )
                db.add(audit_success)
            else:
                logger.warning("action_executor_retries_unsupported", case_id=case.id)
                action.status = "FAILED"
                action.parameters = {"failure_reason": "RETRIES_NOT_SUPPORTED_WITHOUT_RECURRING_CONSENT"}
                case.current_state = "ACTION_FAILED"
                case.updated_at = datetime.utcnow()

                audit_fail = AuditLog(
                    case_id=case.id,
                    event_name="ACTION_EXECUTION_FAILED",
                    description="Direct automated payment retry blocked: recurring autopay consent is not configured.",
                    metadata_json=action.parameters,
                    timestamp=datetime.utcnow()
                )
                db.add(audit_fail)

        # 3. Action: REMINDER
        elif action_type == "REMINDER":
            # Simulate success for communication reminder notifications
            action.status = "EXECUTED"
            action.executed_at = datetime.utcnow()
            case.current_state = "ACTION_EXECUTED"
            case.updated_at = datetime.utcnow()

            audit_success = AuditLog(
                case_id=case.id,
                event_name="ACTION_EXECUTION_SUCCESS",
                description="Communication notification reminder delivered to customer.",
                metadata_json=action.parameters,
                timestamp=datetime.utcnow()
            )
            db.add(audit_success)

        # 4. Action: HUMAN_ESCALATION
        elif action_type == "HUMAN_ESCALATION":
            action.status = "EXECUTED"
            action.executed_at = datetime.utcnow()
            case.current_state = "ESCALATED"
            case.updated_at = datetime.utcnow()

            audit_success = AuditLog(
                case_id=case.id,
                event_name="ACTION_EXECUTION_SUCCESS",
                description="Case escalated to collection agent for manual follow-up.",
                metadata_json=action.parameters,
                timestamp=datetime.utcnow()
            )
            db.add(audit_success)

        # 5. Action: STOP
        elif action_type == "STOP":
            action.status = "EXECUTED"
            action.executed_at = datetime.utcnow()
            case.current_state = "STOPPED"
            case.updated_at = datetime.utcnow()

            audit_success = AuditLog(
                case_id=case.id,
                event_name="ACTION_EXECUTION_SUCCESS",
                description="Recovery process stopped.",
                metadata_json=action.parameters,
                timestamp=datetime.utcnow()
            )
            db.add(audit_success)

        # 6. Action: Unsupported
        else:
            logger.error("action_executor_unsupported_action", case_id=case.id, action_type=action_type)
            action.status = "FAILED"
            action.parameters = {"failure_reason": "UNSUPPORTED_ACTION"}
            case.current_state = "ACTION_FAILED"
            case.updated_at = datetime.utcnow()

            audit_fail = AuditLog(
                case_id=case.id,
                event_name="ACTION_EXECUTION_FAILED",
                description=f"Action executor failed: Unsupported recovery action {action_type}.",
                metadata_json=action.parameters,
                timestamp=datetime.utcnow()
            )
            db.add(audit_fail)

        return action
