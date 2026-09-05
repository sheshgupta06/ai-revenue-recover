"""
Phase 10: Reliability & Failure Testing
========================================
Verifies the AI Revenue Recovery Orchestrator fails safely under all injected
failure conditions. No fake payment outcomes, no fabricated recovery, no silent
state corruption under failures.

Coverage:
  1. AI/LLM failures: timeout, HTTP 500, malformed JSON, bad schema, missing key
  2. Razorpay failures: missing credentials, API error, timeout, invalid response
  3. Webhook: duplicate idempotency, already-recovered guard, failed-event guard
  4. Policy safety invariants: all block conditions enforced
  5. Database integrity: no RECOVERED without verified evidence
  6. Dashboard reliability: empty dataset, real aggregation, synthetic exclusion
"""

import pytest
import json
from datetime import datetime
from unittest.mock import patch, MagicMock, PropertyMock
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.models.models import (
    Customer, Merchant, Payment, RevenueRiskCase,
    RecoveryAction, RecoveryOutcome, AIDecision, AuditLog, WebhookEvent,
)
from app.services.razorpay_service import (
    RazorpayService, RazorpayConfigError, RazorpayAPIError,
)
from app.services.llm_provider import LLMProviderException
from app.services.ai_decision import AIDecisionService, AIDecisionValidationSchema
from app.services.action_executor import ActionExecutorService
from app.services.policy_engine import PolicyEngineService
from app.services.outcome_verification import OutcomeVerificationService
from app.core.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_customer(db, cid, opted_out=False):
    meta = {"opted_out": True} if opted_out else None
    c = Customer(id=cid, email=f"{cid}@test.com", name=cid,
                 is_synthetic=True, metadata_json=meta)
    db.add(c)
    db.commit()
    return c


def _make_case(db, customer_id, amount=50000, state="NEW",
               failure_reason="bank_timeout", event_type="FAILED_PAYMENT",
               merchant_id=None, payment_id=None, strategy="AI"):
    case = RevenueRiskCase(
        customer_id=customer_id, merchant_id=merchant_id, payment_id=payment_id,
        amount_at_risk=amount, event_type=event_type, current_state=state,
        failure_reason=failure_reason, recovery_strategy_group=strategy,
        is_synthetic=True,
    )
    db.add(case)
    db.commit()
    return case


# ===========================================================================
# SECTION 1 — AI / LLM Reliability
# ===========================================================================

class TestAILLMReliability:
    """All failures must trigger deterministic baseline fallback."""

    def test_gemini_timeout_triggers_baseline_fallback(self, db: Session):
        """Gemini timeout: fall back to baseline, confidence=0, AI_DECISION_FALLBACK audit."""
        _make_customer(db, "cust_r001")
        case = _make_case(db, "cust_r001", failure_reason="bank_timeout")

        with patch(
            "app.services.llm_provider.LLMProvider._generate_gemini_response",
            side_effect=LLMProviderException("Gemini timeout simulated"),
        ), patch.object(settings, "LLM_PROVIDER", "gemini"):
            action = AIDecisionService.execute_ai_step(db, case)
            db.commit()

        assert action.status == "PENDING"
        assert action.action_type is not None
        assert case.current_state == "ACTION_PROPOSED"

        event_names = [a.event_name for a in db.query(AuditLog).filter(AuditLog.case_id == case.id).all()]
        assert "AI_DECISION_FALLBACK" in event_names
        assert "AI_DECISION_SUCCESS" not in event_names

        ai_dec = db.query(AIDecision).filter(AIDecision.case_id == case.id).first()
        assert ai_dec is not None and ai_dec.confidence == 0.0

    def test_gemini_http_500_triggers_baseline_fallback(self, db: Session):
        """Gemini HTTP 500 simulated via top-level generate_decision: fall back to baseline."""
        _make_customer(db, "cust_r002")
        case = _make_case(db, "cust_r002", failure_reason="insufficient_funds")

        # Patch generate_decision (the router called regardless of provider)
        # so this test works whether LLM_PROVIDER is mock or gemini.
        with patch(
            "app.services.llm_provider.LLMProvider.generate_decision",
            side_effect=LLMProviderException("Gemini API returned status 500"),
        ):
            action = AIDecisionService.execute_ai_step(db, case)
            db.commit()

        assert action.status == "PENDING"
        assert case.current_state == "ACTION_PROPOSED"
        ai_dec = db.query(AIDecision).filter(AIDecision.case_id == case.id).first()
        assert ai_dec.confidence == 0.0

    def test_gemini_malformed_json_triggers_baseline_fallback(self, db: Session):
        """Malformed JSON from Gemini: fallback triggered, fallback audit written."""
        _make_customer(db, "cust_r003")
        case = _make_case(db, "cust_r003", failure_reason="expired_card")

        with patch(
            "app.services.llm_provider.LLMProvider.generate_decision",
            return_value="{bad_json: true",
        ):
            action = AIDecisionService.execute_ai_step(db, case)
            db.commit()

        assert action.status == "PENDING"
        assert case.current_state == "ACTION_PROPOSED"
        audit = db.query(AuditLog).filter(
            AuditLog.case_id == case.id,
            AuditLog.event_name == "AI_DECISION_FALLBACK"
        ).first()
        assert audit is not None

    def test_ai_invalid_action_in_schema_triggers_fallback(self, db: Session):
        """Valid JSON with unknown action enum: fallback must reject the bad action."""
        _make_customer(db, "cust_r004")
        case = _make_case(db, "cust_r004", failure_reason="expired_card")

        bad_output = json.dumps({
            "action": "FABRICATE_PAYMENT",
            "confidence": 0.99,
            "reason": "Invented",
            "expected_recovery_probability": 0.99,
        })
        with patch(
            "app.services.llm_provider.LLMProvider.generate_decision",
            return_value=bad_output,
        ):
            action = AIDecisionService.execute_ai_step(db, case)
            db.commit()

        assert action.status == "PENDING"
        assert action.action_type != "FABRICATE_PAYMENT"

    def test_missing_ai_api_key_triggers_baseline_fallback(self, db: Session):
        """Missing API key: exception triggers fallback, case gets valid PENDING action."""
        _make_customer(db, "cust_r005")
        case = _make_case(db, "cust_r005", failure_reason="network_failure")

        with patch(
            "app.services.llm_provider.LLMProvider.generate_decision",
            side_effect=LLMProviderException("Gemini API key not configured."),
        ):
            action = AIDecisionService.execute_ai_step(db, case)
            db.commit()

        assert action.status == "PENDING"
        assert action.action_type is not None
        assert case.current_state == "ACTION_PROPOSED"

    def test_ai_schema_validation_rejects_invalid_fields(self):
        """Pydantic schema must accept valid and reject invalid AI output."""
        valid = AIDecisionValidationSchema(
            action="PAYMENT_LINK", confidence=0.85,
            reason="Card expired.", expected_recovery_probability=0.60,
        )
        assert valid.action == "PAYMENT_LINK"

        with pytest.raises(Exception):
            AIDecisionValidationSchema(
                action="INJECT_SQL", confidence=0.99,
                reason="Bad", expected_recovery_probability=0.99,
            )

        with pytest.raises(Exception):
            AIDecisionValidationSchema(
                action="REMINDER", confidence=2.0,
                reason="Valid", expected_recovery_probability=0.5,
            )

        with pytest.raises(Exception):
            AIDecisionValidationSchema(
                action="REMINDER", confidence=0.8,
                reason="Valid", expected_recovery_probability=-0.5,
            )


# ===========================================================================
# SECTION 2 — Razorpay Reliability
# ===========================================================================

class TestRazorpayReliability:
    """Every Razorpay failure must produce a clean FAILED action, no fake URLs."""

    def _setup(self, db, cid):
        merchant = Merchant(id=f"mer_{cid}", name="Merchant")
        customer = Customer(id=cid, email=f"{cid}@test.com", name=cid, is_synthetic=True)
        payment = Payment(
            id=f"pay_{cid}", amount=50000, status="failed", method="card",
            failure_reason="expired_card", customer_id=cid,
            merchant_id=f"mer_{cid}", is_synthetic=True,
        )
        db.add_all([merchant, customer, payment])
        db.commit()
        case = RevenueRiskCase(
            customer_id=cid, merchant_id=f"mer_{cid}", payment_id=f"pay_{cid}",
            amount_at_risk=50000, event_type="FAILED_PAYMENT",
            current_state="ACTION_PROPOSED", failure_reason="expired_card",
            is_synthetic=True,
        )
        db.add(case)
        db.commit()
        action = RecoveryAction(case_id=case.id, action_type="PAYMENT_LINK", status="PENDING")
        db.add(action)
        db.commit()
        return case, action

    def test_missing_credentials_fails_cleanly_no_fake_url(self, db: Session):
        """Missing credentials: RAZORPAY_NOT_CONFIGURED, no payment_link_url fabricated."""
        case, action = self._setup(db, "cust_r101")

        with patch.object(RazorpayService, "client", new_callable=PropertyMock) as p:
            p.side_effect = RazorpayConfigError("Missing credentials")
            ActionExecutorService.execute_approved_action(db, case, action)
            db.commit()

        assert action.status == "FAILED"
        assert action.parameters["failure_reason"] == "RAZORPAY_NOT_CONFIGURED"
        assert "payment_link_url" not in (action.parameters or {})
        assert case.current_state == "ACTION_FAILED"

    def test_api_error_fails_cleanly(self, db: Session):
        """Razorpay API error: RAZORPAY_API_ERROR, case ACTION_FAILED."""
        case, action = self._setup(db, "cust_r102")

        with patch.object(RazorpayService, "create_payment_link",
                          side_effect=RazorpayAPIError("API call failed")):
            ActionExecutorService.execute_approved_action(db, case, action)
            db.commit()

        assert action.status == "FAILED"
        assert action.parameters["failure_reason"] == "RAZORPAY_API_ERROR"
        assert case.current_state == "ACTION_FAILED"

    def test_network_timeout_fails_cleanly_no_fake_url(self, db: Session):
        """Network timeout: action FAILED, no payment_link_url produced."""
        case, action = self._setup(db, "cust_r103")

        with patch.object(RazorpayService, "create_payment_link",
                          side_effect=Exception("Connection timed out")):
            ActionExecutorService.execute_approved_action(db, case, action)
            db.commit()

        assert action.status == "FAILED"
        assert "payment_link_url" not in (action.parameters or {})
        assert case.current_state == "ACTION_FAILED"

    def test_failure_writes_audit_log(self, db: Session):
        """Any Razorpay failure must produce ACTION_EXECUTION_FAILED audit entry."""
        case, action = self._setup(db, "cust_r104")

        with patch.object(RazorpayService, "client", new_callable=PropertyMock) as p:
            p.side_effect = RazorpayConfigError("No credentials")
            ActionExecutorService.execute_approved_action(db, case, action)
            db.commit()

        audit = db.query(AuditLog).filter(
            AuditLog.case_id == case.id,
            AuditLog.event_name == "ACTION_EXECUTION_FAILED"
        ).first()
        assert audit is not None

    def test_get_payment_details_surfaces_as_api_error(self):
        """SDK payment.fetch exception wrapped as RazorpayAPIError."""
        service = RazorpayService()
        with patch.object(RazorpayService, "client", new_callable=PropertyMock) as p:
            mc = MagicMock()
            mc.payment.fetch.side_effect = Exception("httpx timeout")
            p.return_value = mc
            with pytest.raises(RazorpayAPIError):
                service.get_payment_details("pay_fake")

    def test_create_link_incomplete_response_raises_api_error(self):
        """SDK response missing short_url raises RazorpayAPIError, not KeyError."""
        service = RazorpayService()
        with patch.object(RazorpayService, "client", new_callable=PropertyMock) as p:
            mc = MagicMock()
            mc.payment_link.create.return_value = {"id": "plink_incomplete"}
            p.return_value = mc
            with pytest.raises(RazorpayAPIError):
                service.create_payment_link(50000, "Test", "ref_001")

    def test_no_fake_recovery_on_razorpay_failure(self, db: Session):
        """INVARIANT: case must never reach RECOVERED on Razorpay failure."""
        case, action = self._setup(db, "cust_r105")

        with patch.object(RazorpayService, "client", new_callable=PropertyMock) as p:
            p.side_effect = RazorpayConfigError("No creds")
            ActionExecutorService.execute_approved_action(db, case, action)
            db.commit()

        assert case.current_state != "RECOVERED"
        outcome = db.query(RecoveryOutcome).filter(RecoveryOutcome.case_id == case.id).first()
        assert outcome is None


# ===========================================================================
# SECTION 3 — Webhook Reliability and Idempotency
# ===========================================================================

class TestWebhookReliability:

    def _setup_webhook_case(self, db, cid, plink_id, amount=50000):
        merchant = Merchant(id=f"mer_{cid}", name="Merchant")
        customer = Customer(id=cid, email=f"{cid}@test.com", name=cid, is_synthetic=True)
        payment = Payment(
            id=f"pay_{cid}", amount=amount, status="failed", method="upi",
            customer_id=cid, merchant_id=f"mer_{cid}", is_synthetic=True,
        )
        db.add_all([merchant, customer, payment])
        db.commit()
        case = RevenueRiskCase(
            customer_id=cid, merchant_id=f"mer_{cid}", payment_id=f"pay_{cid}",
            amount_at_risk=amount, event_type="FAILED_PAYMENT",
            current_state="ACTION_EXECUTED", failure_reason="expired_card",
            is_synthetic=True,
        )
        db.add(case)
        db.commit()
        action = RecoveryAction(
            case_id=case.id, action_type="PAYMENT_LINK", status="EXECUTED",
            parameters={"payment_link_id": plink_id,
                        "payment_link_url": f"https://rzp.io/i/{plink_id}"},
            executed_at=datetime.utcnow(),
        )
        db.add(action)
        db.commit()
        return case, action

    def test_duplicate_webhook_idempotency_via_event_id(self, client: TestClient, db: Session):
        """Same X-Razorpay-Event-Id delivered twice must not produce duplicate WebhookEvent rows."""
        event_id = "evt_idempotency_test_001"
        payload = {
            "event": "payment.captured",
            "payload": {
                "payment": {"entity": {
                    "id": "pay_dup_test", "amount": 50000,
                    "currency": "INR", "status": "captured",
                }}
            },
        }
        headers = {"X-Razorpay-Event-Id": event_id, "X-Razorpay-Signature": "test_sig"}

        client.post("/api/v1/webhooks/razorpay", json=payload, headers=headers)
        client.post("/api/v1/webhooks/razorpay", json=payload, headers=headers)

        rows = db.query(WebhookEvent).filter(WebhookEvent.event_id == event_id).all()
        assert len(rows) <= 1, (
            f"Idempotency violated: {len(rows)} WebhookEvent rows for same event_id"
        )

    def test_already_recovered_case_not_double_recovered(self, db: Session):
        """INVARIANT: second success webhook must not create a second RecoveryOutcome."""
        case, action = self._setup_webhook_case(db, "cust_r201", "plink_r201")
        case.current_state = "RECOVERED"
        db.add(RecoveryOutcome(
            case_id=case.id, action_id=action.id, recovered_amount=50000,
            is_recovered=True, verification_source="WEBHOOK",
            raw_verification_data={"payment_id": "pay_r201_first"},
        ))
        db.commit()

        webhook_payload = {
            "event": "payment.captured",
            "payload": {
                "payment": {"entity": {
                    "id": "pay_r201_second", "amount": 50000,
                    "currency": "INR", "status": "captured",
                    "payment_link_id": "plink_r201",
                }}
            },
        }
        OutcomeVerificationService.verify_outcome_from_webhook(
            db, "payment.captured", webhook_payload
        )
        db.commit()

        outcomes = db.query(RecoveryOutcome).filter(RecoveryOutcome.case_id == case.id).all()
        assert len(outcomes) == 1
        assert outcomes[0].raw_verification_data.get("payment_id") == "pay_r201_first"

    def test_payment_failed_event_never_marks_recovered(self, db: Session):
        """INVARIANT: payment.failed webhook must NEVER set case state to RECOVERED."""
        case, action = self._setup_webhook_case(db, "cust_r202", "plink_r202")

        webhook_payload = {
            "event": "payment.failed",
            "payload": {
                "payment": {"entity": {
                    "id": "pay_r202", "amount": 50000,
                    "currency": "INR", "status": "failed",
                    "payment_link_id": "plink_r202",
                }}
            },
        }
        outcome = OutcomeVerificationService.verify_outcome_from_webhook(
            db, "payment.failed", webhook_payload
        )
        db.commit()

        assert case.current_state != "RECOVERED"
        if outcome is not None:
            assert outcome.is_recovered is False

    def test_webhook_unknown_payment_link_does_not_crash(self, db: Session):
        """Webhook for unknown payment_link_id: handled gracefully, returns None, no crash."""
        webhook_payload = {
            "event": "payment_link.paid",
            "payload": {
                "payment_link": {"entity": {
                    "id": "plink_unknown_xyz", "amount_paid": 50000,
                    "currency": "INR", "status": "paid",
                }},
                "payment": {"entity": {
                    "id": "pay_unknown_xyz", "amount": 50000,
                    "currency": "INR", "status": "captured",
                    "payment_link_id": "plink_unknown_xyz",
                }},
            },
        }
        try:
            result = OutcomeVerificationService.verify_outcome_from_webhook(
                db, "payment_link.paid", webhook_payload
            )
            db.commit()
            assert result is None
        except Exception as exc:
            pytest.fail(f"Unhandled exception for unknown payment link: {exc}")


# ===========================================================================
# SECTION 4 — Policy Safety Invariants
# ===========================================================================

class TestPolicySafetyInvariants:

    def test_policy_blocks_recovered_case(self, db: Session):
        _make_customer(db, "cust_r301")
        case = _make_case(db, "cust_r301", state="RECOVERED")
        action = RecoveryAction(case_id=case.id, action_type="PAYMENT_LINK", status="PENDING")
        db.add(action); db.commit()

        approved, reason = PolicyEngineService.validate_action(db, case, action)
        assert not approved
        assert reason == "PAYMENT_ALREADY_RECOVERED"

    def test_policy_blocks_captured_payment(self, db: Session):
        merchant = Merchant(id="mer_r302", name="Merchant")
        customer = Customer(id="cust_r302", email="r302@test.com", name="r302", is_synthetic=True)
        payment = Payment(
            id="pay_r302", amount=50000, status="captured", method="upi",
            customer_id="cust_r302", merchant_id="mer_r302", is_synthetic=True,
        )
        db.add_all([merchant, customer, payment]); db.commit()
        case = _make_case(db, "cust_r302", state="ACTION_EXECUTED",
                          payment_id="pay_r302", merchant_id="mer_r302")
        action = RecoveryAction(case_id=case.id, action_type="REMINDER", status="PENDING")
        db.add(action); db.commit()

        approved, reason = PolicyEngineService.validate_action(db, case, action)
        assert not approved
        assert reason == "PAYMENT_ALREADY_RECOVERED"

    def test_policy_blocks_opted_out_customer(self, db: Session):
        _make_customer(db, "cust_r303", opted_out=True)
        case = _make_case(db, "cust_r303")
        action = RecoveryAction(case_id=case.id, action_type="REMINDER", status="PENDING")
        db.add(action); db.commit()

        approved, reason = PolicyEngineService.validate_action(db, case, action)
        assert not approved
        assert reason == "CUSTOMER_OPTED_OUT"

    def test_policy_blocks_attempt_limit_exceeded(self, db: Session):
        _make_customer(db, "cust_r304")
        case = _make_case(db, "cust_r304")
        case.recovery_attempts = 5; case.max_attempts = 5; db.commit()
        action = RecoveryAction(case_id=case.id, action_type="PAYMENT_LINK", status="PENDING")
        db.add(action); db.commit()

        approved, reason = PolicyEngineService.validate_action(db, case, action)
        assert not approved
        assert reason == "RECOVERY_ATTEMPTS_EXCEEDED"

    def test_policy_blocks_high_value(self, db: Session):
        _make_customer(db, "cust_r305")
        case = _make_case(db, "cust_r305", amount=15000000)
        action = RecoveryAction(case_id=case.id, action_type="PAYMENT_LINK", status="PENDING")
        db.add(action); db.commit()

        approved, reason = PolicyEngineService.validate_action(db, case, action)
        assert not approved
        assert reason == "HIGH_VALUE_REQUIRES_HUMAN_ESCALATION"

    def test_policy_blocks_retry_actions(self, db: Session):
        _make_customer(db, "cust_r306")
        case = _make_case(db, "cust_r306")
        for atype in ["RETRY_NOW", "RETRY_LATER"]:
            action = RecoveryAction(case_id=case.id, action_type=atype, status="PENDING")
            db.add(action); db.commit()
            approved, reason = PolicyEngineService.validate_action(db, case, action)
            assert not approved
            assert reason == "RETRIES_NOT_SUPPORTED_WITHOUT_RECURRING_CONSENT"

    def test_executor_skips_non_pending_action(self, db: Session):
        """Executor must skip already-EXECUTED actions (idempotency guard)."""
        _make_customer(db, "cust_r307")
        case = _make_case(db, "cust_r307")
        initial_state = case.current_state
        action = RecoveryAction(case_id=case.id, action_type="PAYMENT_LINK", status="EXECUTED")
        db.add(action); db.commit()

        ActionExecutorService.execute_approved_action(db, case, action)
        db.commit()

        assert action.status == "EXECUTED"
        assert case.current_state == initial_state


# ===========================================================================
# SECTION 5 — Database Integrity
# ===========================================================================

class TestDatabaseIntegrity:

    def test_no_recovered_state_without_outcome_record(self, db: Session):
        """Razorpay failure must never set case to RECOVERED or create RecoveryOutcome."""
        _make_customer(db, "cust_r401")
        case = _make_case(db, "cust_r401", state="ACTION_EXECUTED")
        action = RecoveryAction(case_id=case.id, action_type="PAYMENT_LINK", status="PENDING")
        db.add(action); db.commit()

        with patch.object(RazorpayService, "client", new_callable=PropertyMock) as p:
            p.side_effect = RazorpayConfigError("No creds")
            ActionExecutorService.execute_approved_action(db, case, action)
            db.commit()

        assert case.current_state != "RECOVERED"
        outcome = db.query(RecoveryOutcome).filter(
            RecoveryOutcome.case_id == case.id,
            RecoveryOutcome.is_recovered == True,
        ).first()
        assert outcome is None

    def test_recovery_outcome_includes_payment_id_evidence(self, db: Session):
        """Verified outcome must preserve Razorpay payment_id as evidence."""
        merchant = Merchant(id="mer_r402", name="Merchant")
        customer = Customer(id="cust_r402", email="r402@test.com", name="r402", is_synthetic=True)
        payment = Payment(
            id="pay_r402", amount=75000, status="failed", method="card",
            customer_id="cust_r402", merchant_id="mer_r402", is_synthetic=True,
        )
        db.add_all([merchant, customer, payment]); db.commit()

        case = RevenueRiskCase(
            customer_id="cust_r402", merchant_id="mer_r402", payment_id="pay_r402",
            amount_at_risk=75000, event_type="FAILED_PAYMENT",
            current_state="ACTION_EXECUTED", failure_reason="bank_timeout",
            is_synthetic=True,
        )
        db.add(case); db.commit()

        action = RecoveryAction(
            case_id=case.id, action_type="PAYMENT_LINK", status="EXECUTED",
            parameters={"payment_link_id": "plink_r402"},
            executed_at=datetime.utcnow(),
        )
        db.add(action); db.commit()

        webhook_payload = {
            "event": "payment_link.paid",
            "created_at": int(datetime.utcnow().timestamp()),
            "payload": {
                "payment_link": {"entity": {
                    "id": "plink_r402", "amount_paid": 75000,
                    "currency": "INR", "status": "paid",
                }},
                "payment": {"entity": {
                    "id": "pay_captured_r402", "amount": 75000, "currency": "INR",
                    "status": "captured", "method": "card",
                    "payment_link_id": "plink_r402",
                }},
            },
        }

        outcome = OutcomeVerificationService.verify_outcome_from_webhook(
            db, "payment_link.paid", webhook_payload
        )
        db.commit()

        assert outcome is not None and outcome.is_recovered is True
        assert "payment_id" in outcome.raw_verification_data
        assert outcome.raw_verification_data["payment_id"] == "pay_captured_r402"
        assert "payment_link_id" in outcome.raw_verification_data

    def test_amount_mismatch_blocks_recovered_state(self, db: Session):
        """INVARIANT: webhook amount mismatch must never set case to RECOVERED."""
        customer = Customer(id="cust_r403", email="r403@test.com", name="r403", is_synthetic=True)
        db.add(customer); db.commit()

        case = RevenueRiskCase(
            customer_id="cust_r403", amount_at_risk=100000, event_type="FAILED_PAYMENT",
            current_state="ACTION_EXECUTED", failure_reason="expired_card",
            is_synthetic=True,
        )
        db.add(case); db.commit()

        action = RecoveryAction(
            case_id=case.id, action_type="PAYMENT_LINK", status="EXECUTED",
            parameters={"payment_link_id": "plink_r403"},
            executed_at=datetime.utcnow(),
        )
        db.add(action); db.commit()

        webhook_payload = {
            "event": "payment.captured",
            "payload": {
                "payment": {"entity": {
                    "id": "pay_r403", "amount": 25000,
                    "currency": "INR", "status": "captured",
                    "payment_link_id": "plink_r403",
                }}
            },
        }

        outcome = OutcomeVerificationService.verify_outcome_from_webhook(
            db, "payment.captured", webhook_payload
        )
        db.commit()

        assert outcome is None
        assert case.current_state != "RECOVERED"
        audit = db.query(AuditLog).filter(
            AuditLog.case_id == case.id,
            AuditLog.event_name == "RECOVERY_AMOUNT_MISMATCH",
        ).first()
        assert audit is not None


# ===========================================================================
# SECTION 6 — Dashboard Reliability
# ===========================================================================

class TestDashboardReliability:

    def test_dashboard_metrics_empty_dataset(self, client: TestClient):
        """Empty database returns zero values, not 500."""
        res = client.get("/api/v1/dashboard/metrics")
        assert res.status_code == 200
        data = res.json()
        assert data["total_cases"] == 0
        assert data["total_revenue_at_risk"] == 0
        assert data["total_recovered_revenue"] == 0
        assert data["recovery_rate"] == 0.0

    def test_dashboard_metrics_excludes_synthetic_cases(self, db: Session, client: TestClient):
        """Dashboard must count only is_synthetic=False cases."""
        cust_real = Customer(id="cust_dash_001", name="Real", email="real@test.com", is_synthetic=False)
        db.add(cust_real); db.flush()
        case_real = RevenueRiskCase(
            customer_id="cust_dash_001", amount_at_risk=200000, event_type="FAILED_PAYMENT",
            current_state="RECOVERED", recovery_strategy_group="AI", is_synthetic=False,
        )
        db.add(case_real); db.flush()
        action_real = RecoveryAction(case_id=case_real.id, action_type="PAYMENT_LINK", status="EXECUTED")
        db.add(action_real); db.flush()
        db.add(RecoveryOutcome(
            case_id=case_real.id, action_id=action_real.id, recovered_amount=200000,
            is_recovered=True, verification_source="WEBHOOK",
            raw_verification_data={"payment_id": "pay_real_dash_001"},
        ))

        cust_synth = Customer(id="cust_dash_002", name="Synth", email="synth@test.com", is_synthetic=True)
        db.add(cust_synth); db.flush()
        case_synth = RevenueRiskCase(
            customer_id="cust_dash_002", amount_at_risk=999999, event_type="FAILED_PAYMENT",
            current_state="RECOVERED", recovery_strategy_group="BASELINE", is_synthetic=True,
        )
        db.add(case_synth); db.flush()
        action_synth = RecoveryAction(case_id=case_synth.id, action_type="REMINDER", status="EXECUTED")
        db.add(action_synth); db.flush()
        db.add(RecoveryOutcome(
            case_id=case_synth.id, action_id=action_synth.id, recovered_amount=999999,
            is_recovered=True, verification_source="OFFLINE_SIMULATION",
            raw_verification_data={"simulation": True},
        ))
        db.commit()

        res = client.get("/api/v1/dashboard/metrics")
        assert res.status_code == 200
        data = res.json()
        assert data["total_cases"] == 1
        assert data["recovered_cases"] == 1
        assert data["total_revenue_at_risk"] == 200000
        assert data["total_recovered_revenue"] == 200000
        assert data["recovery_rate"] == 1.0

    def test_dashboard_recovery_rate_calculation(self, db: Session, client: TestClient):
        """Recovery rate must accurately reflect 2/4 = 0.5."""
        for i in range(4):
            cust = Customer(id=f"cust_rate_{i}", name=f"C{i}", email=f"c{i}@test.com", is_synthetic=False)
            db.add(cust); db.flush()
            case = RevenueRiskCase(
                customer_id=f"cust_rate_{i}", amount_at_risk=100000, event_type="FAILED_PAYMENT",
                current_state="RECOVERED" if i < 2 else "NOT_RECOVERED",
                recovery_strategy_group="BASELINE", is_synthetic=False,
            )
            db.add(case); db.flush()
            if i < 2:
                act = RecoveryAction(case_id=case.id, action_type="REMINDER", status="EXECUTED")
                db.add(act); db.flush()
                db.add(RecoveryOutcome(
                    case_id=case.id, action_id=act.id, recovered_amount=100000,
                    is_recovered=True, verification_source="WEBHOOK",
                    raw_verification_data={"payment_id": f"pay_rate_{i}"},
                ))
        db.commit()

        res = client.get("/api/v1/dashboard/metrics")
        assert res.status_code == 200
        data = res.json()
        assert data["total_cases"] == 4
        assert data["recovered_cases"] == 2
        assert abs(data["recovery_rate"] - 0.5) < 0.01

    def test_evaluation_list_returns_list(self, client: TestClient):
        """Evaluation endpoint must return a list (possibly empty)."""
        res = client.get("/api/v1/evaluation")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_dashboard_spa_serves_html(self, client: TestClient):
        """SPA route must serve a valid HTML document."""
        res = client.get("/dashboard/")
        assert res.status_code == 200
        assert "<!doctype html>" in res.text.lower()

