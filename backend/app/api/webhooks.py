import json
from fastapi import APIRouter, Depends, Request, HTTPException, status
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.logging import logger
from app.services.razorpay_service import RazorpayService, RazorpayAPIError, RazorpayConfigError
from app.services.webhook_handler import process_webhook_payload

router = APIRouter()
razorpay_service = RazorpayService()

@router.post("/webhooks/razorpay")
async def handle_razorpay_webhook(
    request: Request,
    db: Session = Depends(get_db)
) -> dict:
    """
    POST webhook receiver for incoming Razorpay payment events.
    Verifies signatures using raw request body bytes, enforces idempotency, and updates states atomically.
    """
    # 1. Retrieve raw body bytes (Mandatory for cryptographic signature verification)
    payload_bytes = await request.body()

    signature = request.headers.get("X-Razorpay-Signature")
    if not signature:
        logger.warning("webhook_request_missing_signature")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required X-Razorpay-Signature header."
        )

    # 2. Verify Cryptographic Webhook Signature
    try:
        razorpay_service.verify_webhook_signature(payload_bytes, signature)
    except RazorpayConfigError as e:
        logger.error("webhook_server_config_missing", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server is not configured to verify webhook signatures."
        )
    except RazorpayAPIError as e:
        logger.warning("webhook_signature_check_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Signature verification failed: {e}"
        )

    # 3. Decode payload bytes to dictionary
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except json.JSONDecodeError as e:
        logger.error("webhook_payload_invalid_json", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON request payload."
        )

    # 4. Atomic Database Processing & State Transitions
    try:
        # Explicit transaction begin, commit, and rollback control
        if not db.in_transaction():
            db.begin()
            
        process_webhook_payload(db, payload)
        
        # Route outcome verification to transition case/payment state on payment success or terminal failures
        event_type = payload.get("event")
        if event_type:
            from app.services.outcome_verification import OutcomeVerificationService
            OutcomeVerificationService.verify_outcome_from_webhook(db, event_type, payload)

        db.commit()
    except Exception as e:
        if db.in_transaction():
            db.rollback()
        logger.error("webhook_transaction_failed_and_rolled_back", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database transaction failed during processing: {e}"
        )

    return {"status": "processed"}

