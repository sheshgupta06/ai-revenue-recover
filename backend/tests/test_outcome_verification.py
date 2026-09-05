import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock, PropertyMock
from sqlalchemy.orm import Session
from app.models.models import Customer, Payment, RevenueRiskCase, RecoveryAction, RecoveryOutcome, AuditLog, Merchant
from app.services.razorpay_service import RazorpayService
from app.services.outcome_verification import OutcomeVerificationService

# --- 1. Test Webhook: Successful Recovery Outcome Verification ---

def test_outcome_verification_webhook_success(db: Session):
    """
    Asserts that a successful captured webhook event updates case/payment states to RECOVERED,
    validates amount/currency, and logs the outcome.
    """
    merchant = Merchant(id="mer_synth_701", name="Merchant 701")
    customer = Customer(id="cust_synth_701", email="c701@example.com", name="C701", is_synthetic=True)
    payment = Payment(
        id="pay_synth_701", amount=50000, status="failed", method="card",
        customer_id="cust_synth_701", merchant_id="mer_synth_701", is_synthetic=True
    )
    db.add_all([merchant, customer, payment])
    db.commit()

    case = RevenueRiskCase(
        payment_id="pay_synth_701", customer_id="cust_synth_701", merchant_id="mer_synth_701",
        amount_at_risk=50000, event_type="FAILED_PAYMENT", current_state="ACTION_EXECUTED",
        failure_reason="expired_card", is_synthetic=True
    )
    db.add(case)
    db.commit()

    action = RecoveryAction(
        case_id=case.id, action_type="PAYMENT_LINK", status="EXECUTED",
        parameters={"payment_link_id": "plink_701", "payment_link_url": "https://rzp.io/i/701"},
        executed_at=datetime.utcnow()
    )
    db.add(action)
    db.commit()

    webhook_payload = {
        "event": "payment_link.paid",
        "created_at": int(datetime.utcnow().timestamp()),
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_701",
                    "amount_paid": 50000,
                    "currency": "INR",
                    "status": "paid"
                }
            },
            "payment": {
                "entity": {
                    "id": "pay_capture_701",
                    "amount": 50000,
                    "currency": "INR",
                    "status": "captured",
                    "method": "upi",
                    "payment_link_id": "plink_701"
                }
            }
        }
    }

    outcome = OutcomeVerificationService.verify_outcome_from_webhook(db, "payment_link.paid", webhook_payload)
    db.commit()

    assert outcome is not None
    assert outcome.is_recovered is True
    assert outcome.recovered_amount == 50000
    assert outcome.verification_source == "WEBHOOK"
    assert outcome.raw_verification_data["payment_id"] == "pay_capture_701"
    assert outcome.raw_verification_data["payment_link_id"] == "plink_701"
    assert outcome.raw_verification_data["time_to_recovery_seconds"] >= 0.0

    # Assert state transitions
    assert case.current_state == "RECOVERED"
    assert payment.status == "captured"

    # Verify audit trail
    audit = db.query(AuditLog).filter(AuditLog.case_id == case.id, AuditLog.event_name == "RECOVERY_OUTCOME_RESOLVED").first()
    assert audit is not None

# --- 2. Test Webhook: Amount Mismatch Gate ---

def test_outcome_verification_amount_mismatch(db: Session):
    """Asserts that amount mismatch logs a failure and blocks RECOVERED state transition."""
    customer = Customer(id="cust_synth_702", email="c702@example.com", name="C702", is_synthetic=True)
    case = RevenueRiskCase(
        customer_id="cust_synth_702", amount_at_risk=50000, event_type="FAILED_PAYMENT",
        current_state="ACTION_EXECUTED", failure_reason="insufficient_funds", is_synthetic=True
    )
    db.add_all([customer, case])
    db.commit()

    action = RecoveryAction(
        case_id=case.id, action_type="PAYMENT_LINK", status="EXECUTED",
        parameters={"payment_link_id": "plink_702"}, executed_at=datetime.utcnow()
    )
    db.add(action)
    db.commit()

    # Webhook contains mismatched amount (e.g. 20000 instead of 50000)
    webhook_payload = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_capture_702",
                    "amount": 20000,
                    "currency": "INR",
                    "status": "captured",
                    "payment_link_id": "plink_702"
                }
            }
        }
    }

    outcome = OutcomeVerificationService.verify_outcome_from_webhook(db, "payment.captured", webhook_payload)
    db.commit()

    assert outcome is None
    assert case.current_state == "ACTION_EXECUTED"

    # Verify audit warning exists
    audit = db.query(AuditLog).filter(AuditLog.case_id == case.id, AuditLog.event_name == "RECOVERY_AMOUNT_MISMATCH").first()
    assert audit is not None

# --- 3. Test Webhook: Delayed Webhook Reconciliation ---

def test_outcome_verification_delayed_success(db: Session):
    """
    Asserts that a delayed verified success event is allowed to reconcile a case
    that is still unrecovered (even if event timestamp is older than case's updated_at).
    """
    customer = Customer(id="cust_synth_703", email="c703@example.com", name="C703", is_synthetic=True)
    # Case is in ACTION_FAILED state, updated_at set to current timestamp
    case = RevenueRiskCase(
        customer_id="cust_synth_703", amount_at_risk=50000, event_type="FAILED_PAYMENT",
        current_state="ACTION_FAILED", failure_reason="expired_card", updated_at=datetime.utcnow(), is_synthetic=True
    )
    db.add_all([customer, case])
    db.commit()

    action = RecoveryAction(
        case_id=case.id, action_type="PAYMENT_LINK", status="EXECUTED",
        parameters={"payment_link_id": "plink_703"}, executed_at=datetime.utcnow()
    )
    db.add(action)
    db.commit()

    # Webhook epoch timestamp is in the past (delayed webhook)
    past_epoch = int(datetime.utcnow().timestamp()) - 3600

    webhook_payload = {
        "event": "payment.captured",
        "created_at": past_epoch,
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_capture_703",
                    "amount": 50000,
                    "currency": "INR",
                    "status": "captured",
                    "payment_link_id": "plink_703"
                }
            }
        }
    }

    outcome = OutcomeVerificationService.verify_outcome_from_webhook(db, "payment.captured", webhook_payload)
    db.commit()

    # Delayed success must reconcile successfully
    assert outcome is not None
    assert outcome.is_recovered is True
    assert case.current_state == "RECOVERED"

# --- 4. Test Polling: PendingCreated link remains unchanged ---

def test_outcome_verification_polling_pending_created(db: Session):
    """Asserts that links in created/pending status do not write outcomes or modify case states."""
    customer = Customer(id="cust_synth_704", email="c704@example.com", name="C704", is_synthetic=True)
    case = RevenueRiskCase(
        customer_id="cust_synth_704", amount_at_risk=50000, event_type="FAILED_PAYMENT",
        current_state="ACTION_EXECUTED", failure_reason="expired_card", is_synthetic=True
    )
    db.add_all([customer, case])
    db.commit()

    action = RecoveryAction(
        case_id=case.id, action_type="PAYMENT_LINK", status="EXECUTED",
        parameters={"payment_link_id": "plink_704"}, executed_at=datetime.utcnow()
    )
    db.add(action)
    db.commit()

    # Mock Razorpay fetch link returning 'created' status (non-terminal)
    mock_link = {
        "id": "plink_704",
        "status": "created",
        "amount": 50000,
        "currency": "INR"
    }

    with patch.object(RazorpayService, "client", new_callable=PropertyMock) as mock_client_prop:
        mock_client = MagicMock()
        mock_client.payment_link.fetch.return_value = mock_link
        mock_client_prop.return_value = mock_client

        outcome = OutcomeVerificationService.poll_and_verify_action_outcome(db, case.id)
        db.commit()

        assert outcome is None
        assert case.current_state == "ACTION_EXECUTED"

# --- 5. Test Polling: Expired status registers failed outcome ---

def test_outcome_verification_polling_expired(db: Session):
    """Asserts that terminal non-recovery states (expired/cancelled) write FAILED outcomes and transition case states."""
    customer = Customer(id="cust_synth_705", email="c705@example.com", name="C705", is_synthetic=True)
    case = RevenueRiskCase(
        customer_id="cust_synth_705", amount_at_risk=50000, event_type="FAILED_PAYMENT",
        current_state="ACTION_EXECUTED", failure_reason="expired_card", is_synthetic=True
    )
    db.add_all([customer, case])
    db.commit()

    action = RecoveryAction(
        case_id=case.id, action_type="PAYMENT_LINK", status="EXECUTED",
        parameters={"payment_link_id": "plink_705"}, executed_at=datetime.utcnow()
    )
    db.add(action)
    db.commit()

    # Mock link returning 'expired'
    mock_link = {
        "id": "plink_705",
        "status": "expired",
        "amount": 50000,
        "currency": "INR"
    }

    with patch.object(RazorpayService, "client", new_callable=PropertyMock) as mock_client_prop:
        mock_client = MagicMock()
        mock_client.payment_link.fetch.return_value = mock_link
        mock_client_prop.return_value = mock_client

        outcome = OutcomeVerificationService.poll_and_verify_action_outcome(db, case.id)
        db.commit()

        assert outcome is not None
        assert outcome.is_recovered is False
        assert outcome.recovered_amount == 0
        assert case.current_state == "NOT_RECOVERED"

# --- 6. Test Polling: Idempotency protections ---

def test_outcome_verification_idempotency(db: Session):
    """Asserts that duplicate verification triggers yield the same outcome and do not write duplicate database records."""
    customer = Customer(id="cust_synth_706", email="c706@example.com", name="C706", is_synthetic=True)
    case = RevenueRiskCase(
        customer_id="cust_synth_706", amount_at_risk=50000, event_type="FAILED_PAYMENT",
        current_state="ACTION_EXECUTED", failure_reason="expired_card", is_synthetic=True
    )
    db.add_all([customer, case])
    db.commit()

    action = RecoveryAction(
        case_id=case.id, action_type="PAYMENT_LINK", status="EXECUTED",
        parameters={"payment_link_id": "plink_706"}, executed_at=datetime.utcnow()
    )
    db.add(action)
    db.commit()

    mock_link = {
        "id": "plink_706",
        "status": "paid",
        "amount_paid": 50000,
        "currency": "INR",
        "payments": [{"payment_id": "pay_706"}]
    }

    with patch.object(RazorpayService, "client", new_callable=PropertyMock) as mock_client_prop:
        mock_client = MagicMock()
        mock_client.payment_link.fetch.return_value = mock_link
        mock_client_prop.return_value = mock_client

        outcome1 = OutcomeVerificationService.poll_and_verify_action_outcome(db, case.id)
        db.commit()

        outcome2 = OutcomeVerificationService.poll_and_verify_action_outcome(db, case.id)
        db.commit()

        assert outcome1 is not None
        assert outcome2 is not None
        assert outcome1.id == outcome2.id

        # Verify only one outcome row exists in DB
        outcomes_count = db.query(RecoveryOutcome).filter(RecoveryOutcome.action_id == action.id).count()
        assert outcomes_count == 1

# --- 7. Invariant Tests: Enforce strict verification rules ---

def test_outcome_verification_executed_alone_not_recovered(db: Session):
    """
    Invariant 1: ACTION_EXECUTED state alone must NEVER result in case state being RECOVERED,
    nor should any RecoveryOutcome record exist before verification occurs.
    """
    customer = Customer(id="cust_synth_inv1", email="inv1@example.com", name="Inv1", is_synthetic=True)
    case = RevenueRiskCase(
        customer_id="cust_synth_inv1", amount_at_risk=50000, event_type="FAILED_PAYMENT",
        current_state="ACTION_EXECUTED", failure_reason="expired_card", is_synthetic=True
    )
    db.add_all([customer, case])
    db.commit()

    # Verify that being in ACTION_EXECUTED does not equal RECOVERED
    assert case.current_state != "RECOVERED"
    
    # Assert no outcome exists
    outcome_count = db.query(RecoveryOutcome).filter(RecoveryOutcome.case_id == case.id).count()
    assert outcome_count == 0


def test_outcome_verification_link_created_remains_pending(db: Session):
    """
    Invariant 2: PAYMENT_LINK action with Razorpay link status 'created' must keep the case
    in ACTION_EXECUTED / pending state and create no RecoveryOutcome.
    """
    customer = Customer(id="cust_synth_inv2", email="inv2@example.com", name="Inv2", is_synthetic=True)
    case = RevenueRiskCase(
        customer_id="cust_synth_inv2", amount_at_risk=50000, event_type="FAILED_PAYMENT",
        current_state="ACTION_EXECUTED", failure_reason="expired_card", is_synthetic=True
    )
    db.add_all([customer, case])
    db.commit()

    action = RecoveryAction(
        case_id=case.id, action_type="PAYMENT_LINK", status="EXECUTED",
        parameters={"payment_link_id": "plink_inv2"}, executed_at=datetime.utcnow()
    )
    db.add(action)
    db.commit()

    # Mock Razorpay response to status='created' (unpaid link)
    mock_link = {
        "id": "plink_inv2",
        "status": "created",
        "amount_paid": 0,
        "currency": "INR",
        "payments": []
    }

    with patch.object(RazorpayService, "client", new_callable=PropertyMock) as mock_client_prop:
        mock_client = MagicMock()
        mock_client.payment_link.fetch.return_value = mock_link
        mock_client_prop.return_value = mock_client

        outcome = OutcomeVerificationService.poll_and_verify_action_outcome(db, case.id)
        db.commit()

        # Should remain pending
        assert outcome is None
        assert case.current_state == "ACTION_EXECUTED"
        
        # Verify no outcome created
        outcome_count = db.query(RecoveryOutcome).filter(RecoveryOutcome.case_id == case.id).count()
        assert outcome_count == 0


def test_outcome_verification_link_paid_recovers_case(db: Session):
    """
    Invariant 3: Razorpay status 'paid' + matching amount + matching currency + payment evidence
    must result in a successful RecoveryOutcome and case state transitions to RECOVERED.
    """
    customer = Customer(id="cust_synth_inv3", email="inv3@example.com", name="Inv3", is_synthetic=True)
    case = RevenueRiskCase(
        customer_id="cust_synth_inv3", amount_at_risk=50000, event_type="FAILED_PAYMENT",
        current_state="ACTION_EXECUTED", failure_reason="expired_card", is_synthetic=True
    )
    db.add_all([customer, case])
    db.commit()

    action = RecoveryAction(
        case_id=case.id, action_type="PAYMENT_LINK", status="EXECUTED",
        parameters={"payment_link_id": "plink_inv3"}, executed_at=datetime.utcnow()
    )
    db.add(action)
    db.commit()

    # Mock Razorpay response to status='paid' (paid link) with matching details
    mock_link = {
        "id": "plink_inv3",
        "status": "paid",
        "amount_paid": 50000,
        "currency": "INR",
        "payments": [{"payment_id": "pay_inv3"}]
    }

    with patch.object(RazorpayService, "client", new_callable=PropertyMock) as mock_client_prop:
        mock_client = MagicMock()
        mock_client.payment_link.fetch.return_value = mock_link
        mock_client_prop.return_value = mock_client

        outcome = OutcomeVerificationService.poll_and_verify_action_outcome(db, case.id)
        db.commit()

        # Should recover successfully
        assert outcome is not None
        assert outcome.is_recovered is True
        assert outcome.recovered_amount == 50000
        assert outcome.raw_verification_data["payment_id"] == "pay_inv3"
        assert outcome.raw_verification_data["currency"] == "INR"

        # Case should be RECOVERED
        assert case.current_state == "RECOVERED"
        
        # Verify outcome count is 1
        outcome_count = db.query(RecoveryOutcome).filter(RecoveryOutcome.case_id == case.id).count()
        assert outcome_count == 1

