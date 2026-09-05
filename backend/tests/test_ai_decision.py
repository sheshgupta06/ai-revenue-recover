import pytest
import json
from unittest.mock import patch
from sqlalchemy.orm import Session
from app.models.models import Customer, Payment, RevenueRiskCase, AIDecision, RecoveryAction, AuditLog
from app.services.context_builder import ContextBuilder
from app.services.ai_decision import AIDecisionService
from app.services.llm_provider import LLMProviderException

# --- 1. Test Context Builder Anonymization (PII Stripping) ---

def test_context_builder_anonymization(db: Session):
    """
    Asserts that customer PII (name, email, phone) is strictly filtered out
    and replaced with anonymized tokens before LLM context generation.
    """
    from app.models.models import Merchant
    merchant = Merchant(id="mer_synth_001", name="Merchant")
    db.add(merchant)

    customer = Customer(
        id="cust_synth_1111",
        email="real_customer_pii@example.com",
        phone="+919876543210",
        name="Sensitive Customer Name PII",
        is_synthetic=True,
        metadata_json={"historical_success_rate": 0.90, "customer_tenure_days": 180}
    )
    payment = Payment(
        id="pay_synth_1111",
        amount=150000,
        status="failed",
        method="card",
        failure_reason="expired_card",
        customer_id="cust_synth_1111",
        merchant_id="mer_synth_001",
        is_synthetic=True
    )
    db.add_all([customer, payment])
    db.commit()

    case = RevenueRiskCase(
        payment_id="pay_synth_1111",
        customer_id="cust_synth_1111",
        merchant_id="mer_synth_001",
        amount_at_risk=150000,
        event_type="FAILED_PAYMENT",
        current_state="NEW",
        failure_reason="expired_card",
        recovery_strategy_group="AI",
        is_synthetic=True
    )
    db.add(case)
    db.commit()

    context_str = ContextBuilder.build_case_context(db, case)
    context_data = json.loads(context_str)

    # Verify PII details are absent
    assert "real_customer_pii@example.com" not in context_str
    assert "+919876543210" not in context_str
    assert "Sensitive Customer Name PII" not in context_str

    # Verify anonymized structure is populated
    assert context_data["customer"]["anonymized_id"] == "anonymized_cust_synth_1111"
    assert context_data["customer"]["historical_success_rate"] == 0.90
    assert context_data["customer"]["customer_tenure_days"] == 180

# --- 2. Test Success Path with Mocks ---

def test_ai_decision_success_mock(db: Session):
    """
    Tests that a valid JSON response from the LLM is parsed, schema-validated,
    persisted into AIDecision, case moves to ACTION_PROPOSED, and RecoveryAction status is PENDING.
    """
    from app.models.models import Merchant
    merchant = Merchant(id="mer_synth_001", name="Merchant")
    db.add(merchant)

    customer = Customer(id="cust_synth_2222", email="c22@example.com", name="C22", is_synthetic=True)
    payment = Payment(id="pay_synth_2222", amount=100000, status="failed", method="card", failure_reason="insufficient_funds", customer_id="cust_synth_2222", merchant_id="mer_synth_001", is_synthetic=True)
    db.add_all([customer, payment])
    db.commit()

    case = RevenueRiskCase(
        payment_id="pay_synth_2222",
        customer_id="cust_synth_2222",
        merchant_id="mer_synth_001",
        amount_at_risk=100000,
        event_type="FAILED_PAYMENT",
        current_state="NEW",
        failure_reason="insufficient_funds",
        recovery_strategy_group="AI",
        recovery_probability=0.70,  # Authoritative baseline probability
        prioritization_score=70000.0,
        is_synthetic=True
    )
    db.add(case)
    db.commit()

    mock_llm_response = json.dumps({
        "action": "RETRY_LATER",
        "delay_minutes": 240,
        "confidence": 0.88,
        "reason": "Card insufficient funds failure. Scheduling retry after delay.",
        "expected_recovery_probability": 0.65
    })

    with patch("app.services.llm_provider.LLMProvider.generate_decision", return_value=mock_llm_response) as mock_generate:
        action = AIDecisionService.execute_ai_step(db, case)
        db.commit()

        mock_generate.assert_called_once()

        # Check case state and action proposal
        assert case.current_state == "ACTION_PROPOSED"
        assert action.action_type == "RETRY_LATER"
        assert action.parameters == {"delay_minutes": 240}
        assert action.status == "PENDING"  # Strict boundary check: recommendations are proposed (PENDING) only

        # Verify AIDecision table records
        decision = db.query(AIDecision).filter(AIDecision.case_id == case.id).first()
        assert decision is not None
        assert decision.recommended_action == "RETRY_LATER"
        assert decision.confidence == 0.88
        assert decision.expected_recovery_probability == 0.65
        assert decision.expected_recovered_amount == 65000  # 100000 * 0.65

        # Check that case deterministic probability was NOT overwritten by LLM
        assert case.recovery_probability == 0.70
        assert case.prioritization_score == 70000.0

        # Audit log verification
        audit = db.query(AuditLog).filter(AuditLog.case_id == case.id).first()
        assert audit is not None
        assert audit.event_name == "AI_DECISION_SUCCESS"

# --- 3. Test Fallback Path on Malformed JSON Output ---

def test_ai_decision_malformed_json_fallback(db: Session):
    """
    Tests that if the LLM returns invalid/malformed JSON text, the engine
    seamlessly falls back to proposing the deterministic baseline action.
    """
    from app.models.models import Merchant
    merchant = Merchant(id="mer_synth_001", name="Merchant")
    db.add(merchant)

    customer = Customer(id="cust_synth_3333", email="c33@example.com", name="C33", is_synthetic=True)
    payment = Payment(
        id="pay_synth_3333", amount=100000, status="failed", method="card",
        failure_reason="expired_card", customer_id="cust_synth_3333", merchant_id="mer_synth_001", is_synthetic=True
    )
    db.add_all([customer, payment])
    db.commit()

    case = RevenueRiskCase(
        payment_id="pay_synth_3333",
        customer_id="cust_synth_3333",
        merchant_id="mer_synth_001",
        amount_at_risk=100000,
        event_type="FAILED_PAYMENT",
        current_state="NEW",
        failure_reason="expired_card",
        recovery_strategy_group="AI",
        recovery_probability=0.20,
        is_synthetic=True
    )
    db.add(case)
    db.commit()

    malformed_response = "Not a valid JSON response from LLM!"

    with patch("app.services.llm_provider.LLMProvider.generate_decision", return_value=malformed_response):
        action = AIDecisionService.execute_ai_step(db, case)
        db.commit()

        # Should fallback to baseline strategy for 'expired_card' (which is PAYMENT_LINK)
        assert case.current_state == "ACTION_PROPOSED"
        assert action.action_type == "PAYMENT_LINK"
        assert action.status == "PENDING"

        # Verify fallback metadata logged in AIDecision table
        decision = db.query(AIDecision).filter(AIDecision.case_id == case.id).first()
        assert decision is not None
        assert decision.recommended_action == "PAYMENT_LINK"
        assert decision.confidence == 0.0
        assert "fallback" in decision.reason.lower()

        # Audit log verification
        audit = db.query(AuditLog).filter(AuditLog.case_id == case.id, AuditLog.event_name == "AI_DECISION_FALLBACK").first()
        assert audit is not None

# --- 4. Test Fallback Path on Out-of-Bounds Validation Errors ---

def test_ai_decision_validation_bounds_fallback(db: Session):
    """
    Tests that if the LLM output JSON contains out-of-bounds parameters (e.g. confidence = 2.5),
    the validation parser fails and baseline fallback is proposed.
    """
    from app.models.models import Merchant
    merchant = Merchant(id="mer_synth_001", name="Merchant")
    db.add(merchant)

    customer = Customer(id="cust_synth_4444", email="c44@example.com", name="C44", is_synthetic=True)
    payment = Payment(
        id="pay_synth_4444", amount=50000, status="failed", method="upi",
        failure_reason="bank_timeout", customer_id="cust_synth_4444", merchant_id="mer_synth_001", is_synthetic=True
    )
    db.add_all([customer, payment])
    db.commit()

    case = RevenueRiskCase(
        payment_id="pay_synth_4444",
        customer_id="cust_synth_4444",
        merchant_id="mer_synth_001",
        amount_at_risk=50000,
        event_type="FAILED_PAYMENT",
        current_state="NEW",
        failure_reason="bank_timeout",
        recovery_strategy_group="AI",
        is_synthetic=True
    )
    db.add(case)
    db.commit()

    invalid_bounds_response = json.dumps({
        "action": "RETRY_NOW",
        "delay_minutes": None,
        "confidence": 1.85,  # Invalid: must be <= 1.0
        "reason": "Timeout issue, retry now.",
        "expected_recovery_probability": 0.95
    })

    with patch("app.services.llm_provider.LLMProvider.generate_decision", return_value=invalid_bounds_response):
        action = AIDecisionService.execute_ai_step(db, case)
        db.commit()

        # Should trigger fallback to baseline strategy for 'bank_timeout' (which is RETRY_NOW)
        assert case.current_state == "ACTION_PROPOSED"
        assert action.action_type == "RETRY_NOW"
        assert action.status == "PENDING"

        decision = db.query(AIDecision).filter(AIDecision.case_id == case.id).first()
        assert decision.confidence == 0.0
        assert "fallback" in decision.reason.lower()

# --- 5. Test Fallback Path on Provider Exception (Timeouts/Errors) ---

def test_ai_decision_provider_timeout_fallback(db: Session):
    """
    Tests that if the LLM provider API times out or raises connection errors,
    the application catches the exception and proposes baseline fallback.
    """
    from app.models.models import Merchant
    merchant = Merchant(id="mer_synth_001", name="Merchant")
    db.add(merchant)

    customer = Customer(id="cust_synth_5555", email="c55@example.com", name="C55", is_synthetic=True)
    payment = Payment(
        id="pay_synth_5555", amount=50000, status="failed", method="upi",
        failure_reason="bank_timeout", customer_id="cust_synth_5555", merchant_id="mer_synth_001", is_synthetic=True
    )
    db.add_all([customer, payment])
    db.commit()

    case = RevenueRiskCase(
        payment_id="pay_synth_5555",
        customer_id="cust_synth_5555",
        merchant_id="mer_synth_001",
        amount_at_risk=50000,
        event_type="FAILED_PAYMENT",
        current_state="NEW",
        failure_reason="bank_timeout",
        recovery_strategy_group="AI",
        is_synthetic=True
    )
    db.add(case)
    db.commit()

    with patch("app.services.llm_provider.LLMProvider.generate_decision", side_effect=LLMProviderException("Gemini connection timed out after 5.0 seconds.")):
        action = AIDecisionService.execute_ai_step(db, case)
        db.commit()

        # Verify fallback triggered
        assert case.current_state == "ACTION_PROPOSED"
        assert action.action_type == "RETRY_NOW"
        assert action.status == "PENDING"

        decision = db.query(AIDecision).filter(AIDecision.case_id == case.id).first()
        assert decision.confidence == 0.0
        assert "fallback" in decision.reason.lower()
        assert "timed out" in decision.raw_decision_output["error"]

