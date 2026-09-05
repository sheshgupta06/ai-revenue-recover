import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.models.models import RevenueRiskCase, Customer, Payment, RecoveryOutcome, RecoveryAction

def test_dashboard_metrics_calculation(db: Session, client: TestClient):
    """
    Verifies that dashboard metrics correctly compute aggregates for real cases
    and completely exclude synthetic/offline simulation cases.
    """
    # 1. Add a real customer, payment, and resolved case (is_synthetic = False)
    cust_real = Customer(id="cust_real_001", name="Real Cust", email="real@example.com", phone="12345", is_synthetic=False)
    db.add(cust_real)
    db.flush()

    pay_real = Payment(id="pay_real_001", amount=500000, currency="INR", status="captured", method="card", customer_id="cust_real_001", is_synthetic=False)
    db.add(pay_real)
    db.flush()

    case_real = RevenueRiskCase(
        payment_id="pay_real_001", customer_id="cust_real_001", amount_at_risk=500000,
        event_type="FAILED_PAYMENT", current_state="RECOVERED", recovery_strategy_group="AI",
        is_synthetic=False
    )
    db.add(case_real)
    db.flush()

    action_real = RecoveryAction(case_id=case_real.id, action_type="PAYMENT_LINK", status="EXECUTED")
    db.add(action_real)
    db.flush()

    outcome_real = RecoveryOutcome(
        case_id=case_real.id, action_id=action_real.id, recovered_amount=500000, is_recovered=True,
        verification_source="WEBHOOK", raw_verification_data={"payment_id": "pay_real_001"}
    )
    db.add(outcome_real)

    # 2. Add a synthetic/simulation resolved case (is_synthetic = True)
    cust_synth = Customer(id="cust_syn_001", name="Synth Cust", email="syn@example.com", phone="54321", is_synthetic=True)
    db.add(cust_synth)
    db.flush()

    pay_synth = Payment(id="pay_syn_001", amount=800000, currency="INR", status="captured", method="card", customer_id="cust_syn_001", is_synthetic=True)
    db.add(pay_synth)
    db.flush()

    case_synth = RevenueRiskCase(
        payment_id="pay_syn_001", customer_id="cust_syn_001", amount_at_risk=800000,
        event_type="FAILED_PAYMENT", current_state="RECOVERED", recovery_strategy_group="AI",
        is_synthetic=True
    )
    db.add(case_synth)
    db.flush()

    action_synth = RecoveryAction(case_id=case_synth.id, action_type="PAYMENT_LINK", status="EXECUTED")
    db.add(action_synth)
    db.flush()

    outcome_synth = RecoveryOutcome(
        case_id=case_synth.id, action_id=action_synth.id, recovered_amount=800000, is_recovered=True,
        verification_source="OFFLINE_SIMULATION", raw_verification_data={"payment_id": "pay_syn_001"}
    )
    db.add(outcome_synth)
    db.commit()

    # 3. Request dashboard metrics
    res = client.get("/api/v1/dashboard/metrics")
    assert res.status_code == 200
    data = res.json()

    # Assert metrics contain ONLY the real case elements
    assert data["total_cases"] == 1
    assert data["recovered_cases"] == 1
    assert data["total_revenue_at_risk"] == 500000
    assert data["total_recovered_revenue"] == 500000
    assert data["recovery_rate"] == 1.0

def test_evaluation_list_endpoint(client: TestClient):
    """Verifies retrieval of completed simulation run records list."""
    res = client.get("/api/v1/evaluation")
    assert res.status_code == 200
    assert isinstance(res.json(), list)

def test_static_file_routing(client: TestClient):
    """Verifies that the FastAPI StaticFiles mount resolves the SPA dashboard page."""
    res = client.get("/dashboard/")
    assert res.status_code == 200
    assert "<!DOCTYPE html>" in res.text

