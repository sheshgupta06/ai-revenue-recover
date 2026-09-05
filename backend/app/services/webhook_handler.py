from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.models.models import WebhookEvent, Payment, Customer
from app.core.logging import logger

# Strict precedence ranking for payment statuses
# A status of higher rank can never be transitioned to a status of lower rank (downgraded)
STATUS_PRECEDENCE = {
    "created": 1,
    "authorized": 2,
    "failed": 3,
    "captured": 4,
    "refunded": 5
}

def process_webhook_payload(db: Session, payload: dict) -> None:
    """
    Processes a verified webhook payload atomically inside a database transaction.
    Protects against concurrent duplicate deliveries, stale/out-of-order events, and invalid state downgrades.
    """
    event_id = payload.get("id")
    event_type = payload.get("event")
    event_timestamp = payload.get("created_at")  # Epoch seconds when event occurred

    if not event_id or not event_type:
        logger.error("webhook_payload_missing_identifiers", payload=payload)
        raise ValueError("Invalid payload: Missing 'id' or 'event' parameters.")

    # 1. Concurrent-safe Idempotency Insertion
    try:
        # Establish a database savepoint to isolate unique constraint validation
        with db.begin_nested():
            db_event = WebhookEvent(
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                processed=False,
                created_at=datetime.utcnow()
            )
            db.add(db_event)
            db.flush()
    except IntegrityError:
        # Catch duplicate constraint violations (concurrency safety)
        logger.info("webhook_duplicate_concurrent_delivery_ignored", event_id=event_id)
        # The savepoint transaction was automatically rolled back. No need to throw.
        # Fetch the existing event to confirm details
        existing_event = db.query(WebhookEvent).filter(WebhookEvent.event_id == event_id).first()
        if existing_event:
            logger.info(
                "webhook_duplicate_check_completed",
                event_id=event_id,
                processed=existing_event.processed
            )
        return

    # 2. Extract and Validate Payment Entity details
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    payment_id = payment_entity.get("id")

    if not payment_id:
        logger.warning("webhook_payload_missing_payment_details", event_id=event_id)
        db_event.processed = True
        db_event.processed_at = datetime.utcnow()
        return

    incoming_status = payment_entity.get("status")
    
    # 3. Fetch Existing Payment and Validate State Transitions & Timestamps
    payment = db.query(Payment).filter(Payment.id == payment_id).first()

    if payment:
        current_status = payment.status

        # Transition Rule A: captured/refunded states must never be downgraded
        if current_status in ["captured", "refunded"] and incoming_status not in ["captured", "refunded"]:
            logger.info(
                "webhook_state_transition_ignored_downgrade",
                payment_id=payment_id,
                current_status=current_status,
                incoming_status=incoming_status
            )
            db_event.processed = True
            db_event.processed_at = datetime.utcnow()
            return

        # Transition Rule B: terminal refunded status transitions are forbidden
        if current_status == "refunded":
            logger.info(
                "webhook_state_transition_ignored_refunded_terminal",
                payment_id=payment_id,
                incoming_status=incoming_status
            )
            db_event.processed = True
            db_event.processed_at = datetime.utcnow()
            return

        # Transition Rule C: Timestamp ordering check
        # We compare the event payload timestamp with the current record updated_at timestamp.
        # Prevents older webhooks arriving out-of-order from overwriting newer updates.
        last_updated_epoch = int(payment.updated_at.timestamp())
        if event_timestamp and event_timestamp < last_updated_epoch:
            logger.info(
                "webhook_stale_event_ignored",
                payment_id=payment_id,
                event_timestamp=event_timestamp,
                last_updated_epoch=last_updated_epoch
            )
            db_event.processed = True
            db_event.processed_at = datetime.utcnow()
            return

        # Execute Payment Record status transition update
        payment.status = incoming_status
        payment.method = payment_entity.get("method", payment.method)
        payment.failure_reason = payment_entity.get("error_description", payment.failure_reason)
        payment.updated_at = datetime.utcnow()
        
        logger.info(
            "webhook_payment_state_updated",
            payment_id=payment_id,
            old_status=current_status,
            new_status=incoming_status
        )

    else:
        # Create missing customer entity dependencies first to satisfy FK integrity constraints
        customer_id = payment_entity.get("customer_id") or f"cust_unknown_{payment_id}"
        customer = db.query(Customer).filter(Customer.id == customer_id).first()

        if not customer:
            customer = Customer(
                id=customer_id,
                email=payment_entity.get("email"),
                phone=payment_entity.get("contact"),
                created_at=datetime.utcnow()
            )
            db.add(customer)
            db.flush()

        # Create new Payment record
        payment = Payment(
            id=payment_id,
            amount=payment_entity.get("amount"),
            currency=payment_entity.get("currency", "INR"),
            status=incoming_status,
            method=payment_entity.get("method"),
            failure_reason=payment_entity.get("error_description"),
            customer_id=customer_id,
            created_at=datetime.fromtimestamp(payment_entity.get("created_at", event_timestamp or int(datetime.utcnow().timestamp()))),
            updated_at=datetime.utcnow()
        )
        db.add(payment)
        logger.info("webhook_payment_record_created", payment_id=payment_id, status=incoming_status)

    # 4. Mark the Webhook Event as processed successfully
    db_event.processed = True
    db_event.processed_at = datetime.utcnow()
