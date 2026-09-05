"""
Tests for POST /api/v1/cases/trigger-failed-payment.

Key invariants:
  1. Non-existent payment_id must return HTTP 400 (never 500 / ForeignKeyViolation).
  2. Existing payment_id must return HTTP 200 and create a RevenueRiskCase.
  3. Re-triggering the same payment_id is idempotent (updates, not duplicates).
  4. Invalid strategy_group returns HTTP 422.
"""
import pytest
from datetime import datetime
from app.models.models import Payment, Customer, RevenueRiskCase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_customer(db):
    customer = Customer(
        id="cust_test_001",
        email="test@example.com",
        phone="+919999999999",
        name="Test Customer",
        is_synthetic=True,
        created_at=datetime.utcnow(),
    )
    db.add(customer)
    db.flush()
    return customer


def _seed_payment(db, payment_id="pay_test_001", status="failed", failure_reason="bank_timeout"):
    _seed_customer(db)
    payment = Payment(
        id=payment_id,
        customer_id="cust_test_001",
        merchant_id=None,
        amount=50000,
        currency="INR",
        status=status,
        failure_reason=failure_reason,
        is_synthetic=True,
        created_at=datetime.utcnow(),
    )
    db.add(payment)
    db.flush()
    return payment


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestTriggerFailedPayment:

    def test_synthetic_generation_can_create_ai_only_dataset(self, client, db):
        """AI-only generation must not create BASELINE recovery cases."""
        response = client.post(
            "/api/v1/synthetic/generate",
            json={"num_customers": 8, "seed": 123, "strategy_group": "AI"},
        )

        assert response.status_code == 201, response.text
        cases = db.query(RevenueRiskCase).filter(RevenueRiskCase.is_synthetic.is_(True)).all()
        assert cases
        assert {case.recovery_strategy_group for case in cases} == {"AI"}

    def test_nonexistent_payment_returns_400(self, client):
        """Non-existent payment_id must return HTTP 400, never 500."""
        response = client.post(
            "/api/v1/cases/trigger-failed-payment",
            json={"payment_id": "pay_does_not_exist", "strategy_group": "BASELINE"},
        )
        assert response.status_code == 400, (
            f"Expected 400 for non-existent payment_id, got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert "Payment not found" in body["detail"] or "not found" in body["detail"].lower()

    def test_default_string_value_returns_400(self, client):
        """Swagger default 'string' must return 400, not crash with 500."""
        response = client.post(
            "/api/v1/cases/trigger-failed-payment",
            json={"payment_id": "string", "strategy_group": "BASELINE"},
        )
        assert response.status_code == 400, (
            f"Swagger default 'string' must return 400, got {response.status_code}"
        )

    def test_valid_payment_creates_case(self, client, db):
        """Happy path: valid existing payment_id creates a RevenueRiskCase."""
        _seed_payment(db, payment_id="pay_valid_001")
        db.commit()

        response = client.post(
            "/api/v1/cases/trigger-failed-payment",
            json={"payment_id": "pay_valid_001", "strategy_group": "BASELINE"},
        )
        assert response.status_code == 200, (
            f"Expected 200 for valid payment_id, got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert body["payment_id"] == "pay_valid_001"
        assert body["current_state"] == "NEW"
        assert body["event_type"] == "FAILED_PAYMENT"
        assert body["recovery_strategy_group"] == "BASELINE"

    def test_valid_payment_ai_strategy(self, client, db):
        """AI strategy group is accepted for valid payment_id."""
        _seed_payment(db, payment_id="pay_valid_ai_001")
        db.commit()

        response = client.post(
            "/api/v1/cases/trigger-failed-payment",
            json={"payment_id": "pay_valid_ai_001", "strategy_group": "AI"},
        )
        assert response.status_code == 200
        assert response.json()["recovery_strategy_group"] == "AI"

    def test_idempotent_retrigger(self, client, db):
        """Re-triggering the same payment_id updates the case, not a duplicate."""
        _seed_payment(db, payment_id="pay_idem_001")
        db.commit()

        r1 = client.post(
            "/api/v1/cases/trigger-failed-payment",
            json={"payment_id": "pay_idem_001", "strategy_group": "BASELINE"},
        )
        assert r1.status_code == 200
        case_id_first = r1.json()["id"]

        r2 = client.post(
            "/api/v1/cases/trigger-failed-payment",
            json={"payment_id": "pay_idem_001", "strategy_group": "BASELINE"},
        )
        assert r2.status_code == 200
        assert r2.json()["id"] == case_id_first, "Idempotent retrigger must not duplicate cases"

        count = db.query(RevenueRiskCase).filter(
            RevenueRiskCase.payment_id == "pay_idem_001"
        ).count()
        assert count == 1

    def test_invalid_strategy_group_returns_422(self, client):
        """Pydantic validation rejects invalid strategy_group."""
        response = client.post(
            "/api/v1/cases/trigger-failed-payment",
            json={"payment_id": "pay_any", "strategy_group": "INVALID"},
        )
        assert response.status_code == 422


