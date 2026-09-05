import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from app.models.models import Customer, Payment, RevenueRiskCase, RecoveryAction
from app.services.synthetic_generator import (
    seed_synthetic_dataset,
    SyntheticCustomerSchema,
    SyntheticPaymentSchema
)
from app.services.risk_engine import (
    calculate_loss_risk_score,
    calculate_recovery_probability,
    RiskEngineService
)
from app.services.baseline_strategy import BaselineStrategyService

# --- 1. Test Pydantic Validation ---

def test_pydantic_validation_valid():
    """Verifies that correctly formed synthetic metadata passes validation."""
    customer_data = {
        "id": "cust_synth_0001",
        "email": "synth_1@example.com",
        "phone": "+919999999999",
        "name": "Synthetic Customer 1",
        "is_synthetic": True,
        "metadata_json": {"historical_success_rate": 0.85, "customer_tenure_days": 100, "is_subscribed": True}
    }
    validated = SyntheticCustomerSchema(**customer_data)
    assert validated.id == "cust_synth_0001"

def test_pydantic_validation_invalid_id():
    """Verifies that malformed synthetic IDs trigger validation errors."""
    customer_data = {
        "id": "cust_real_0001",  # Fails ID regex constraint
        "email": "synth_1@example.com",
        "phone": "+919999999999",
        "name": "Synthetic Customer 1",
        "is_synthetic": True,
        "metadata_json": {}
    }
    with pytest.raises(ValidationError):
        SyntheticCustomerSchema(**customer_data)

def test_pydantic_validation_invalid_amount():
    """Verifies that negative transaction amounts fail validation."""
    payment_data = {
        "id": "pay_synth_101",
        "amount": -500,  # Fails gt=0 constraint
        "currency": "INR",
        "status": "failed",
        "method": "upi",
        "failure_reason": "insufficient_funds",
        "customer_id": "cust_synth_101",
        "is_synthetic": True,
        "metadata_json": {},
        "created_at": "2026-08-27T12:00:00"
    }
    with pytest.raises(ValidationError):
        SyntheticPaymentSchema(**payment_data)

# --- 2. Test Synthetic Generation Reproducibility ---

def test_synthetic_generation_reproducibility(db: Session):
    """
    Verifies that seeding with a static seed generates the exact same dataset output.
    """
    res1 = seed_synthetic_dataset(db, num_customers=5, seed=42)
    custs_run1 = db.query(Customer).filter(Customer.is_synthetic == True).order_by(Customer.id).all()
    pays_run1 = db.query(Payment).filter(Payment.is_synthetic == True).order_by(Payment.id).all()
    
    # Store key properties
    cust_ids_1 = [c.id for c in custs_run1]
    pay_ids_1 = [p.id for p in pays_run1]
    pay_amounts_1 = [p.amount for p in pays_run1]

    # Re-seed using the same seed value
    res2 = seed_synthetic_dataset(db, num_customers=5, seed=42)
    custs_run2 = db.query(Customer).filter(Customer.is_synthetic == True).order_by(Customer.id).all()
    pays_run2 = db.query(Payment).filter(Payment.is_synthetic == True).order_by(Payment.id).all()

    cust_ids_2 = [c.id for c in custs_run2]
    pay_ids_2 = [p.id for p in pays_run2]
    pay_amounts_2 = [p.amount for p in pays_run2]

    assert res1 == res2
    assert cust_ids_1 == cust_ids_2
    assert pay_ids_1 == pay_ids_2
    assert pay_amounts_1 == pay_amounts_2

# --- 3. Test Decoupled Risk & Recovery Scoring Matrix ---

def test_decoupled_scoring_logic():
    """
    Asserts that loss risk and recovery probability are correctly decoupled
    based on the severity of the failure category.
    """
    # 1. Expired Card: high risk of permanent loss, low chance of automated recovery
    loss_risk_expired = calculate_loss_risk_score("expired_card")
    rec_prob_expired = calculate_recovery_probability("expired_card", historical_success_rate=1.0)
    assert loss_risk_expired == 0.85
    assert rec_prob_expired == 0.20

    # 2. Bank Timeout: low risk of permanent loss, extremely high chance of retry recovery
    loss_risk_timeout = calculate_loss_risk_score("bank_timeout")
    rec_prob_timeout = calculate_recovery_probability("bank_timeout", historical_success_rate=1.0)
    assert loss_risk_timeout == 0.10
    assert rec_prob_timeout == 0.95

# --- 4. Test Expected Value Prioritization ---

def test_prioritization_score_calculation(db: Session):
    """
    Verifies that cases are prioritized based on expected recoverable revenue,
    ensuring high-value low-probability cases outrank low-value high-probability cases.
    """
    # Seed default merchant
    from app.models.models import Merchant
    merchant = Merchant(id="mer_synth_001", name="Merchant")
    db.add(merchant)

    # Customer 1 (Good History)
    c1 = Customer(
        id="cust_synth_0001", email="c1@example.com", name="C1", is_synthetic=True,
        metadata_json={"historical_success_rate": 1.0}
    )
    # Customer 2 (Average History)
    c2 = Customer(
        id="cust_synth_0002", email="c2@example.com", name="C2", is_synthetic=True,
        metadata_json={"historical_success_rate": 0.80}
    )
    db.add_all([c1, c2])
    db.commit()

    # Case A: Low Value (₹500), High Probability (UPI Bank Timeout -> 95% * 1.0 = 95%)
    # Expected Recoverable Revenue: 50000 * 0.95 = 47,500 paisa
    p1 = Payment(id="pay_synth_a", amount=50000, status="failed", method="upi", failure_reason="bank_timeout", customer_id="cust_synth_0001", merchant_id="mer_synth_001", is_synthetic=True)
    
    # Case B: High Value (₹10,000), Low Probability (Card Expired -> 20% * 0.8 = 16%)
    # Expected Recoverable Revenue: 1000000 * 0.16 = 160,000 paisa
    p2 = Payment(id="pay_synth_b", amount=1000000, status="failed", method="card", failure_reason="expired_card", customer_id="cust_synth_0002", merchant_id="mer_synth_001", is_synthetic=True)
    
    db.add_all([p1, p2])
    db.commit()

    case_a = RiskEngineService.create_or_update_recovery_case(db, payment_id="pay_synth_a")
    case_b = RiskEngineService.create_or_update_recovery_case(db, payment_id="pay_synth_b")
    db.commit()

    assert case_a.prioritization_score == 47500.0
    assert case_b.prioritization_score == 160000.0

    # High value expected recovery should be prioritized higher than low value high probability
    assert case_b.prioritization_score > case_a.prioritization_score

# --- 5. Test Baseline Strategy Execution ---

def test_baseline_strategy_execution(db: Session):
    """
    Verifies step-by-step state changes, attempt increments, and probability decay
    for the deterministic baseline strategy.
    """
    from app.models.models import Merchant
    merchant = Merchant(id="mer_synth_001", name="Merchant")
    db.add(merchant)

    customer = Customer(
        id="cust_synth_0009", email="c9@example.com", name="C9", is_synthetic=True,
        metadata_json={"historical_success_rate": 1.0}
    )
    payment = Payment(
        id="pay_synth_test_step", amount=100000, status="failed", method="upi",
        failure_reason="bank_timeout", customer_id="cust_synth_0009", merchant_id="mer_synth_001", is_synthetic=True
    )
    db.add_all([customer, payment])
    db.commit()

    case = RiskEngineService.create_or_update_recovery_case(db, payment_id="pay_synth_test_step")
    db.commit()

    assert case.current_state == "NEW"
    assert case.recovery_attempts == 0
    assert case.recovery_probability == 0.95  # bank_timeout (0.95) * success_rate (1.0) * (0.7 ^ 0)

    # Step 1: Should execute RETRY_NOW
    act1 = BaselineStrategyService.execute_baseline_step(db, case)
    db.commit()
    assert act1.action_type == "RETRY_NOW"
    assert case.current_state == "ACTION_EXECUTED"
    assert case.recovery_attempts == 1
    assert case.recovery_probability == 0.665  # 0.95 * 1.0 * (0.7 ^ 1)

    # Step 2: Should execute RETRY_LATER
    act2 = BaselineStrategyService.execute_baseline_step(db, case)
    db.commit()
    assert act2.action_type == "RETRY_LATER"
    assert case.current_state == "ACTION_SCHEDULED"
    assert case.recovery_attempts == 2
    assert case.recovery_probability == 0.4655  # 0.95 * 1.0 * (0.7 ^ 2)

    # Step 3: Should execute PAYMENT_LINK
    act3 = BaselineStrategyService.execute_baseline_step(db, case)
    db.commit()
    assert act3.action_type == "PAYMENT_LINK"
    assert case.current_state == "ACTION_SCHEDULED"
    assert case.recovery_attempts == 3

    # Step 4: Should execute STOP (retry limit exceeded)
    act4 = BaselineStrategyService.execute_baseline_step(db, case)
    db.commit()
    assert act4.action_type == "STOP"
    assert case.current_state == "STOPPED"

# --- 6. Test Database Isolation ---

def test_database_isolation_boundaries(db: Session, client: TestClient):
    """
    Verifies that synthetic data structures and endpoints never query
    or mix with non-synthetic real payment records.
    """
    from app.models.models import Merchant
    merchant = Merchant(id="mer_synth_001", name="Merchant")
    db.add(merchant)

    # 1. Insert non-synthetic customer & payment
    c_real = Customer(id="cust_real_9999", email="real@example.com", name="Real Customer", is_synthetic=False)
    p_real = Payment(id="pay_real_9999", amount=50000, status="failed", method="card", failure_reason="insufficient_funds", customer_id="cust_real_9999", merchant_id="mer_synth_001", is_synthetic=False)
    db.add_all([c_real, p_real])
    db.commit()

    # 2. Trigger case ingestion for the real payment
    case_real = RiskEngineService.create_or_update_recovery_case(db, payment_id="pay_real_9999")
    db.commit()
    assert case_real.is_synthetic is False

    # 3. Call the API list synthetic endpoint
    response = client.get("/api/v1/cases/synthetic")
    assert response.status_code == 200
    
    # Assert that the real case is NOT in the response list (only is_synthetic=True records returned)
    case_ids = [c["id"] for c in response.json()]
    assert case_real.id not in case_ids

