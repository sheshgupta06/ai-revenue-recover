import pytest
import json
from datetime import datetime
from unittest.mock import patch, MagicMock, PropertyMock
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from app.models.models import Customer, Payment, RevenueRiskCase, RecoveryAction, AuditLog
from app.services.razorpay_service import RazorpayService, RazorpayPaymentLinkDetails, RazorpayConfigError
from app.services.policy_engine import PolicyEngineService
from app.services.action_executor import ActionExecutorService

# --- 1. Test Razorpay Service Extension Compatibility ---

def test_create_payment_link_extension():
    """
    Verifies that the RazorpayService.create_payment_link signature remains backward-compatible
    and correctly maps additional customer details when provided.
    """
    service = RazorpayService()
    
    # Mock self.client property
    mock_client = MagicMock()
    mock_client.payment_link.create.return_value = {
        "id": "plink_test_123",
        "short_url": "https://rzp.io/i/test_123",
        "status": "created",
        "reference_id": "case_1001"
    }
    
    with patch.object(RazorpayService, "client", new_callable=PropertyMock) as mock_client_prop:
        mock_client_prop.return_value = mock_client
        
        # Test backward-compatibility call signature (3 positional arguments)
        details1 = service.create_payment_link(1000, "Description 1", "case_1001")
        assert details1.id == "plink_test_123"
        assert details1.short_url == "https://rzp.io/i/test_123"
        
        # Test extended call signature with optional customer details
        details2 = service.create_payment_link(
            amount=2000,
            description="Description 2",
            reference_id="case_1002",
            customer_name="Synthetic Customer",
            customer_email="synth@example.com",
            customer_phone="+919876543210"
        )
        assert details2.id == "plink_test_123"
        
        # Verify the SDK payload call included the customer details dictionary
        mock_client.payment_link.create.assert_called_with(data={
            "amount": 2000,
            "currency": "INR",
            "accept_partial": False,
            "description": "Description 2",
            "reference_id": "case_1002",
            "customer": {
                "name": "Synthetic Customer",
                "email": "synth@example.com",
                "contact": "+919876543210"
            }
        })

# --- 2. Test Policy: Retry Limit Exceeded Gating ---

def test_policy_retry_limit_block(db: Session):
    """Asserts that the Policy Engine blocks active recovery steps once attempt limits are reached."""
    customer = Customer(id="cust_synth_601", email="c601@example.com", name="C601", is_synthetic=True)
    case = RevenueRiskCase(
        customer_id="cust_synth_601", amount_at_risk=50000, event_type="FAILED_PAYMENT",
        current_state="ACTION_PROPOSED", failure_reason="insufficient_funds",
        recovery_attempts=3, max_attempts=3, is_synthetic=True
    )
    db.add_all([customer, case])
    db.commit()

    action = RecoveryAction(case_id=case.id, action_type="PAYMENT_LINK", status="PENDING")
    db.add(action)
    db.commit()

    approved, reason = PolicyEngineService.validate_action(db, case, action)
    assert approved is False
    assert reason == "RECOVERY_ATTEMPTS_EXCEEDED"

# --- 3. Test Policy: Customer Opt-out Gating ---

def test_policy_customer_opt_out_block(db: Session):
    """Asserts that the Policy Engine blocks recovery actions for customers who opted out."""
    customer = Customer(
        id="cust_synth_602", email="c602@example.com", name="C602", is_synthetic=True,
        metadata_json={"opted_out": True}
    )
    case = RevenueRiskCase(
        customer_id="cust_synth_602", amount_at_risk=50000, event_type="FAILED_PAYMENT",
        current_state="ACTION_PROPOSED", failure_reason="expired_card", is_synthetic=True
    )
    db.add_all([customer, case])
    db.commit()

    action = RecoveryAction(case_id=case.id, action_type="PAYMENT_LINK", status="PENDING")
    db.add(action)
    db.commit()

    approved, reason = PolicyEngineService.validate_action(db, case, action)
    assert approved is False
    assert reason == "CUSTOMER_OPTED_OUT"

# --- 4. Test Policy: High-value Transaction Escalation ---

def test_policy_high_value_escalation(db: Session):
    """Asserts that high-value cases trigger review escalation and block active automation."""
    customer = Customer(id="cust_synth_603", email="c603@example.com", name="C603", is_synthetic=True)
    case = RevenueRiskCase(
        customer_id="cust_synth_603", amount_at_risk=12000000, event_type="FAILED_PAYMENT",
        current_state="ACTION_PROPOSED", failure_reason="expired_card", is_synthetic=True
    )
    db.add_all([customer, case])
    db.commit()

    action = RecoveryAction(case_id=case.id, action_type="PAYMENT_LINK", status="PENDING")
    db.add(action)
    db.commit()

    approved, reason = PolicyEngineService.validate_action(db, case, action)
    assert approved is False
    assert reason == "HIGH_VALUE_REQUIRES_HUMAN_ESCALATION"

# --- 5. Test Action Executor: Retry Blocked & Logged ---

def test_unsupported_retry_execution_fails(db: Session):
    """Asserts that retries are rejected with a clear unsupported status and no simulated success."""
    customer = Customer(id="cust_synth_604", email="c604@example.com", name="C604", is_synthetic=True)
    case = RevenueRiskCase(
        customer_id="cust_synth_604", amount_at_risk=50000, event_type="FAILED_PAYMENT",
        current_state="ACTION_PROPOSED", failure_reason="insufficient_funds", is_synthetic=True
    )
    db.add_all([customer, case])
    db.commit()

    action = RecoveryAction(case_id=case.id, action_type="RETRY_NOW", status="PENDING")
    db.add(action)
    db.commit()

    # Even if it bypassed policy validation, the executor itself must reject and fail
    ActionExecutorService.execute_approved_action(db, case, action)
    db.commit()

    assert action.status == "FAILED"
    assert action.parameters["failure_reason"] == "RETRIES_NOT_SUPPORTED_WITHOUT_RECURRING_CONSENT"
    assert case.current_state == "ACTION_FAILED"

    # Audit log check
    audit = db.query(AuditLog).filter(AuditLog.case_id == case.id, AuditLog.event_name == "ACTION_EXECUTION_FAILED").first()
    assert audit is not None

# --- 6. Test Action Executor: Missing Credentials Fails Cleanly ---

def test_missing_credentials_fails_cleanly(db: Session):
    """
    Asserts that if Razorpay API keys are not configured,payment link execution
    fails cleanly with RAZORPAY_NOT_CONFIGURED (no fake link generated).
    """
    customer = Customer(id="cust_synth_605", email="c605@example.com", name="C605", is_synthetic=True)
    case = RevenueRiskCase(
        customer_id="cust_synth_605", amount_at_risk=50000, event_type="FAILED_PAYMENT",
        current_state="ACTION_PROPOSED", failure_reason="expired_card", is_synthetic=True
    )
    db.add_all([customer, case])
    db.commit()

    action = RecoveryAction(case_id=case.id, action_type="PAYMENT_LINK", status="PENDING")
    db.add(action)
    db.commit()

    # Mock Razorpay client getter to raise Config Error representing missing keys
    with patch.object(RazorpayService, "client", new_callable=PropertyMock) as mock_client_prop:
        mock_client_prop.side_effect = RazorpayConfigError("Missing Key ID")
        
        ActionExecutorService.execute_approved_action(db, case, action)
        db.commit()

        assert action.status == "FAILED"
        assert action.parameters["failure_reason"] == "RAZORPAY_NOT_CONFIGURED"
        assert case.current_state == "ACTION_FAILED"

# --- 7. Test API: Double Policy Fallback Loop ---

def test_double_policy_fallback_loop(db: Session, client: TestClient):
    """
    Verifies the double policy validation fallback loop:
    1. Proposes RETRY_NOW (AI recommendation).
    2. Policy blocks it (retries unsupported).
    3. Queries baseline strategy fallback (PAYMENT_LINK).
    4. Policy approves PAYMENT_LINK fallback.
    5. Action Executor generates link successfully.
    """
    from app.models.models import Merchant
    merchant = Merchant(id="mer_synth_001", name="Merchant")
    db.add(merchant)

    customer = Customer(id="cust_synth_606", email="c606@example.com", name="C606", is_synthetic=True)
    payment = Payment(
        id="pay_synth_606", amount=50000, status="failed", method="card",
        failure_reason="expired_card", customer_id="cust_synth_606", merchant_id="mer_synth_001", is_synthetic=True
    )
    db.add_all([customer, payment])
    db.commit()

    case = RevenueRiskCase(
        payment_id="pay_synth_606",
        customer_id="cust_synth_606",
        merchant_id="mer_synth_001",
        amount_at_risk=50000,
        event_type="FAILED_PAYMENT",
        current_state="NEW",
        failure_reason="expired_card",
        recovery_strategy_group="AI",
        is_synthetic=True
    )
    db.add(case)
    db.commit()

    # AI recommends RETRY_NOW
    action = RecoveryAction(case_id=case.id, action_type="RETRY_NOW", status="PENDING", created_at=datetime.utcnow())
    db.add(action)
    db.commit()

    mock_link_details = RazorpayPaymentLinkDetails(
        id="plink_synth_606",
        short_url="https://rzp.io/i/synth_606",
        status="created",
        reference_id=f"case_{case.id}"
    )

    # Mock the Razorpay service link generation to simulate success (when mock keys are loaded)
    with patch.object(RazorpayService, "create_payment_link", return_value=mock_link_details):
        response = client.post(f"/api/v1/cases/{case.id}/execute-pending")
        assert response.status_code == 200
        
        data = response.json()
        assert data["status"] == "fallback_success"
        assert data["policy_approved"] is False
        assert data["original_block_reason"] == "RETRIES_NOT_SUPPORTED_WITHOUT_RECURRING_CONSENT"
        assert data["action_executed"] == "PAYMENT_LINK"
        assert data["execution_status"] == "EXECUTED"
        assert data["case_new_state"] == "ACTION_EXECUTED"

        # Check action statuses in DB
        actions = db.query(RecoveryAction).filter(RecoveryAction.case_id == case.id).order_by(RecoveryAction.created_at).all()
        assert len(actions) == 2
        assert actions[0].action_type == "RETRY_NOW"
        assert actions[0].status == "BLOCKED"
        
        assert actions[1].action_type == "PAYMENT_LINK"
        assert actions[1].status == "EXECUTED"
        assert actions[1].parameters["payment_link_url"] == "https://rzp.io/i/synth_606"

        # Verify audit entries log the fallback loop steps
        audits = db.query(AuditLog).filter(AuditLog.case_id == case.id).all()
        event_names = [a.event_name for a in audits]
        assert "POLICY_BLOCKED" in event_names
        assert "FALLBACK_LOOP_TRIGGERED" in event_names
        assert "POLICY_APPROVED" in event_names
        assert "ACTION_EXECUTION_SUCCESS" in event_names

