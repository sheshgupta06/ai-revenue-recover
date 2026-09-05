from collections import Counter
from unittest.mock import patch

from app.models.models import Payment, RevenueRiskCase, RecoveryAction
from app.services.policy_engine import PolicyEngineService
from app.services.synthetic_generator import (
    EVALUATION_PROFILE_TYPES,
    generate_demo_profiles,
    generate_evaluation_profiles,
    seed_evaluation_and_demo_dataset,
)


def test_evaluation_dataset_has_120_reproducible_diverse_profiles():
    first = generate_evaluation_profiles(120, seed=2026)
    second = generate_evaluation_profiles(120, seed=2026)

    assert first == second
    assert len(first) == 120
    assert Counter(profile["metadata_json"]["profile_type"] for profile in first) == Counter({
        profile_type: 24 for profile_type in EVALUATION_PROFILE_TYPES
    })
    assert all(profile["dataset_type"] == "EVALUATION" for profile in first)


def test_seed_creates_matched_evaluation_cases_and_separate_demo_cases(db):
    counts = seed_evaluation_and_demo_dataset(db, evaluation_count=120, seed=42)

    assert counts["evaluation_profiles"] == 120
    assert counts["evaluation_cases"] == 240
    assert counts["demo_profiles"] == 5
    assert counts["demo_cases"] == 5

    evaluation_cases = db.query(RevenueRiskCase).filter(
        RevenueRiskCase.dataset_type == "EVALUATION"
    ).all()
    demo_cases = db.query(RevenueRiskCase).filter(
        RevenueRiskCase.dataset_type == "DEMO"
    ).all()
    assert len(evaluation_cases) == 240
    assert len(demo_cases) == 5

    grouped = {}
    for case in evaluation_cases:
        payment = db.query(Payment).filter(Payment.id == case.payment_id).one()
        grouped.setdefault(payment.id, []).append(case)
    assert len(grouped) == 120
    assert all({case.recovery_strategy_group for case in cases} == {"AI", "BASELINE"} for cases in grouped.values())
    assert all(
        cases[0].amount_at_risk == cases[1].amount_at_risk
        and cases[0].failure_reason == cases[1].failure_reason
        and cases[0].customer_id == cases[1].customer_id
        for cases in grouped.values()
    )


def test_demo_ids_are_fixed_payment_link_ready_and_policy_approved(db):
    seed_evaluation_and_demo_dataset(db, evaluation_count=120, seed=42)
    expected_ids = [f"demo_payment_link_{index:03d}" for index in range(1, 6)]
    demo_payments = db.query(Payment).filter(Payment.dataset_type == "DEMO").order_by(Payment.id).all()
    demo_cases = db.query(RevenueRiskCase).filter(RevenueRiskCase.dataset_type == "DEMO").order_by(RevenueRiskCase.id).all()

    assert [payment.id for payment in demo_payments] == expected_ids
    assert [payment.amount for payment in demo_payments] == [249900, 150000, 300000, 7500000, 200000]
    assert [payment.metadata_json["profile_type"] for payment in demo_payments] == [
        "Consumer Failed E-commerce Payment",
        "Checkout Abandonment",
        "Consumer Failed E-commerce Payment",
        "B2B Invoice Overdue",
        "Checkout Abandonment",
    ]
    assert len(demo_cases) == 5
    assert all(case.current_state == "NEW" for case in demo_cases)
    assert all(payment.status in {"failed", "created"} for payment in demo_payments)
    assert all(payment.metadata_json["expected_action"] == "PAYMENT_LINK" for payment in demo_payments)

    for case in demo_cases:
        action = RecoveryAction(case_id=case.id, action_type="PAYMENT_LINK", status="PENDING")
        db.add(action)
        db.flush()
        approved, reason = PolicyEngineService.validate_action(db, case, action)
        assert approved is True
        assert reason == "APPROVED"
        assert action.parameters is None


def test_ai_recommends_payment_link_for_every_demo_case(db):
    from app.services.ai_decision import AIDecisionService

    seed_evaluation_and_demo_dataset(db, evaluation_count=120, seed=42)
    demo_cases = db.query(RevenueRiskCase).filter(
        RevenueRiskCase.dataset_type == "DEMO"
    ).order_by(RevenueRiskCase.id).all()

    for case in demo_cases:
        action = AIDecisionService.execute_ai_step(db, case)
        approved, reason = PolicyEngineService.validate_action(db, case, action)
        assert action.action_type == "PAYMENT_LINK"
        assert approved is True
        assert reason == "APPROVED"


def test_demo_data_is_excluded_from_evaluation_case_selection(db):
    seed_evaluation_and_demo_dataset(db, evaluation_count=120, seed=42)
    evaluation_cases = db.query(RevenueRiskCase).filter(
        RevenueRiskCase.dataset_type == "EVALUATION",
        RevenueRiskCase.recovery_strategy_group.in_(["AI", "BASELINE"]),
    ).all()
    demo_cases = db.query(RevenueRiskCase).filter(RevenueRiskCase.dataset_type == "DEMO").all()

    assert len(evaluation_cases) == 240
    assert len(demo_cases) == 5
    assert not set(case.id for case in evaluation_cases) & set(case.id for case in demo_cases)


def test_default_generation_endpoint_adds_full_batch_and_demo_cases(client, db):
    response = client.post("/api/v1/synthetic/generate", json={"seed": 42})

    assert response.status_code == 201, response.text
    body = response.json()["seeded_records"]
    assert body["evaluation_profiles"] == 120
    assert body["evaluation_cases"] == 240
    assert body["demo_profiles"] == 5
    assert db.query(RevenueRiskCase).filter(RevenueRiskCase.dataset_type == "DEMO").count() == 5


def test_demo_payment_link_execution_does_not_fabricate_link_on_provider_failure(db):
    from app.services.action_executor import ActionExecutorService
    from app.services.razorpay_service import RazorpayAPIError

    seed_evaluation_and_demo_dataset(db, evaluation_count=120, seed=42)
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.dataset_type == "DEMO").first()
    action = RecoveryAction(case_id=case.id, action_type="PAYMENT_LINK", status="PENDING")
    db.add(action)
    db.flush()

    with patch("app.services.action_executor.RazorpayService.create_payment_link", side_effect=RazorpayAPIError("test outage")):
        result = ActionExecutorService.execute_approved_action(db, case, action)

    assert result.status == "FAILED"
    assert "payment_link_id" not in (result.parameters or {})
    assert "payment_link_url" not in (result.parameters or {})
    assert case.current_state == "ACTION_FAILED"
