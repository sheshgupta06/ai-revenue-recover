import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.models.models import Payment, WebhookEvent, Customer
from app.services.razorpay_service import RazorpayAPIError, RazorpayConfigError

# Mock webhook payload templates matching official Razorpay structures
def create_mock_payment_payload(event_id: str, payment_id: str, status: str, amount: int = 15000, created_at: int = None) -> dict:
    if created_at is None:
        created_at = int(datetime.utcnow().timestamp())
    return {
        "id": event_id,
        "entity": "event",
        "account_id": "acc_BF4NFN8r7nUvj1",
        "event": f"payment.{status}",
        "created_at": created_at,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "entity": "payment",
                    "amount": amount,
                    "currency": "INR",
                    "status": status,
                    "method": "upi",
                    "error_description": "Card has expired" if status == "failed" else None,
                    "created_at": created_at,
                    "customer_id": "cust_test_123",
                    "email": "test@example.com",
                    "contact": "+919999999999"
                }
            }
        }
    }

@patch("app.api.webhooks.razorpay_service")
def test_handle_webhook_signature_success(mock_service: MagicMock, client: TestClient) -> None:
    """
    Verifies that a valid webhook signature header and payload returns 200 OK.
    """
    # Mock signature verification to pass silently
    mock_service.verify_webhook_signature.return_value = None

    payload = create_mock_payment_payload("evt_success_123", "pay_success_123", "captured")
    headers = {"X-Razorpay-Signature": "valid_signature_placeholder"}
    
    response = client.post("/api/v1/webhooks/razorpay", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"status": "processed"}

@patch("app.api.webhooks.razorpay_service")
def test_handle_webhook_signature_missing(mock_service: MagicMock, client: TestClient) -> None:
    """
    Verifies that a missing X-Razorpay-Signature header returns 400 Bad Request.
    """
    payload = create_mock_payment_payload("evt_missing_123", "pay_missing_123", "failed")
    response = client.post("/api/v1/webhooks/razorpay", json=payload)
    assert response.status_code == 400
    assert "Missing required" in response.json()["detail"]

@patch("app.api.webhooks.razorpay_service")
def test_handle_webhook_signature_failure(mock_service: MagicMock, client: TestClient) -> None:
    """
    Verifies that a signature verification mismatch error returns 400 Bad Request.
    """
    # Mock signature check to raise API error
    mock_service.verify_webhook_signature.side_effect = RazorpayAPIError("Signature mismatch")

    payload = create_mock_payment_payload("evt_fail_123", "pay_fail_123", "failed")
    headers = {"X-Razorpay-Signature": "invalid_signature"}

    response = client.post("/api/v1/webhooks/razorpay", json=payload, headers=headers)
    assert response.status_code == 400
    assert "Signature verification failed" in response.json()["detail"]

@patch("app.api.webhooks.razorpay_service")
def test_webhook_idempotency_duplicate_events(mock_service: MagicMock, client: TestClient, db: Session) -> None:
    """
    Verifies that duplicate webhook deliveries with the same event ID are processed only once and return 200.
    """
    mock_service.verify_webhook_signature.return_value = None

    payload = create_mock_payment_payload("evt_dup_123", "pay_dup_123", "failed")
    headers = {"X-Razorpay-Signature": "valid_sig"}

    # First delivery: should process and record in database
    response = client.post("/api/v1/webhooks/razorpay", json=payload, headers=headers)
    assert response.status_code == 200

    # Verify event stored in DB
    stored_event = db.query(WebhookEvent).filter(WebhookEvent.event_id == "evt_dup_123").first()
    assert stored_event is not None
    assert stored_event.processed is True

    # Second concurrent-like delivery: should match unique constraint, ignore, and return 200 OK
    response_dup = client.post("/api/v1/webhooks/razorpay", json=payload, headers=headers)
    assert response_dup.status_code == 200

    # Ensure payment record is created only once (count is 1)
    payment_count = db.query(Payment).filter(Payment.id == "pay_dup_123").count()
    assert payment_count == 1

@patch("app.api.webhooks.razorpay_service")
def test_explicit_state_transition_downgrade_policy(mock_service: MagicMock, client: TestClient, db: Session) -> None:
    """
    Verifies that a payment state cannot be downgraded (e.g. captured -> failed transitions are ignored).
    """
    mock_service.verify_webhook_signature.return_value = None
    headers = {"X-Razorpay-Signature": "valid_sig"}

    now_epoch = int(datetime.utcnow().timestamp())

    # 1. Send payment.captured webhook (transitions state to captured)
    payload_captured = create_mock_payment_payload("evt_cap_99", "pay_state_99", "captured", created_at=now_epoch)
    response = client.post("/api/v1/webhooks/razorpay", json=payload_captured, headers=headers)
    assert response.status_code == 200

    # Assert payment is captured in db
    payment = db.query(Payment).filter(Payment.id == "pay_state_99").first()
    assert payment.status == "captured"

    # 2. Send a late payment.failed webhook (created at same timestamp, but status is failed)
    # The transition policy should reject changing status from captured -> failed
    payload_failed = create_mock_payment_payload("evt_fail_99", "pay_state_99", "failed", created_at=now_epoch)
    response = client.post("/api/v1/webhooks/razorpay", json=payload_failed, headers=headers)
    assert response.status_code == 200

    # Assert status remains captured (is not downgraded to failed)
    db.refresh(payment)
    assert payment.status == "captured"

@patch("app.api.webhooks.razorpay_service")
def test_out_of_order_timestamp_ordering_protection(mock_service: MagicMock, client: TestClient, db: Session) -> None:
    """
    Verifies that older/stale events are ignored based on timestamp ordering.
    """
    mock_service.verify_webhook_signature.return_value = None
    headers = {"X-Razorpay-Signature": "valid_sig"}

    now = datetime.utcnow()
    future_epoch = int((now + timedelta(minutes=10)).timestamp())
    past_epoch = int((now - timedelta(minutes=10)).timestamp())

    # 1. Send a newer event in the future (status failed)
    payload_future = create_mock_payment_payload("evt_future_55", "pay_order_55", "failed", created_at=future_epoch)
    response = client.post("/api/v1/webhooks/razorpay", json=payload_future, headers=headers)
    assert response.status_code == 200

    payment = db.query(Payment).filter(Payment.id == "pay_order_55").first()
    assert payment.status == "failed"

    # Set updated_at artificially to match the future event timestamp
    payment.updated_at = datetime.fromtimestamp(future_epoch)
    db.commit()

    # 2. Send an older event in the past (status captured)
    # Even though captured is higher rank than failed, since the event's created_at (past_epoch)
    # is older than the record's updated_at (future_epoch), it must be ignored as a stale out-of-order event.
    payload_past = create_mock_payment_payload("evt_past_55", "pay_order_55", "captured", created_at=past_epoch)
    response = client.post("/api/v1/webhooks/razorpay", json=payload_past, headers=headers)
    assert response.status_code == 200

    # Assert payment remains failed (stale captured webhook ignored)
    db.refresh(payment)
    assert payment.status == "failed"

@patch("app.api.webhooks.razorpay_service")
def test_webhook_atomic_transaction_rollback(mock_service: MagicMock, client: TestClient, db: Session) -> None:
    """
    Verifies that the entire transaction is rolled back on processing errors, persisting nothing.
    """
    mock_service.verify_webhook_signature.return_value = None
    headers = {"X-Razorpay-Signature": "valid_sig"}

    # Mock db.add to raise an exception specifically when inserting a Customer object
    original_add = db.add
    def mock_add(obj):
        if isinstance(obj, Customer):
            raise ValueError("Simulated database constraint failure")
        return original_add(obj)

    with patch.object(db, "add", side_effect=mock_add):
        payload = create_mock_payment_payload("evt_rollback_88", "pay_rollback_88", "failed")
        response = client.post("/api/v1/webhooks/razorpay", json=payload, headers=headers)
        
        # Should raise 500 error from transaction failure
        assert response.status_code == 500

        # Clear session to ensure we read fresh state from database
        db.rollback()
        db.expunge_all()

        # Assert webhook_event was NOT created (rolled back completely)
        stored_event = db.query(WebhookEvent).filter(WebhookEvent.event_id == "evt_rollback_88").first()
        assert stored_event is None

        # Assert payment was NOT created
        stored_payment = db.query(Payment).filter(Payment.id == "pay_rollback_88").first()
        assert stored_payment is None


