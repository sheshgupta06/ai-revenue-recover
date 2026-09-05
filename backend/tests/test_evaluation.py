import pytest
from unittest.mock import patch
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from app.models.models import EvaluationRun, RecoveryOutcome, RevenueRiskCase
from app.services.evaluation_service import EvaluationService

# --- 1. Test: No Strategy Bias ---

def test_evaluation_no_strategy_bias(db: Session):
    """
    Verifies that under identical action decisions, the conversion probability model
    yields identical recovery outcomes (no strategy group bias).
    """
    # Mock AI recommended action to always return the exact same baseline action type
    # so both Baseline and AI strategies are mathematically identical.
    def mock_ai_rec(case, attempt, sim_rand):
        from app.services.baseline_strategy import BaselineStrategyService
        action, _ = BaselineStrategyService.determine_next_action(case.failure_reason, case.recovery_attempts)
        return action

    with patch.object(EvaluationService, "_simulate_ai_recommendation", side_effect=mock_ai_rec):
        run = EvaluationService.run_offline_evaluation(db, name="Bias Test Run", random_seed=101, sample_size=20)
        db.commit()

        # Fetch metrics for BASELINE and AI
        metrics = {m.strategy_group: m for m in run.metrics}
        assert "BASELINE" in metrics
        assert "AI" in metrics

        m_base = metrics["BASELINE"]
        m_ai = metrics["AI"]

        # Assert identical results
        assert m_base.total_cases == m_ai.total_cases
        assert m_base.recovered_cases == m_ai.recovered_cases
        assert m_base.total_recovered_revenue == m_ai.total_recovered_revenue
        assert m_base.avg_time_to_recovery == m_ai.avg_time_to_recovery
        assert m_base.expected_recovered_revenue == m_ai.expected_recovered_revenue
        assert m_base.actual_recovered_revenue == m_ai.actual_recovered_revenue

        # Paired metrics checks: no discordant cells
        paired = m_ai.paired_metrics
        assert paired["ai_only_recovered"] == 0
        assert paired["baseline_only_recovered"] == 0
        assert paired["both_recovered"] == m_base.recovered_cases
        assert paired["neither_recovered"] == m_base.total_cases - m_base.recovered_cases
        assert paired["p_value"] == 1.0

# --- 2. Test: Reproducibility ---

def test_evaluation_reproducibility(db: Session):
    """Asserts that identical seeds produce identical evaluation metrics and matched runs."""
    run1 = EvaluationService.run_offline_evaluation(db, name="Rep Run 1", random_seed=555, sample_size=15)
    db.commit()
    metrics1 = {m.strategy_group: m for m in run1.metrics}

    run2 = EvaluationService.run_offline_evaluation(db, name="Rep Run 2", random_seed=555, sample_size=15)
    db.commit()
    metrics2 = {m.strategy_group: m for m in run2.metrics}

    for group in ["BASELINE", "AI"]:
        m1 = metrics1[group]
        m2 = metrics2[group]
        assert m1.recovered_cases == m2.recovered_cases
        assert m1.total_recovered_revenue == m2.total_recovered_revenue
        assert m1.average_recovered_amount == m2.average_recovered_amount
        assert m1.expected_recovered_revenue == m2.expected_recovered_revenue
        assert m1.actual_recovered_revenue == m2.actual_recovered_revenue

# --- 3. Test: Binomial Exact paired statistical calculation ---

def test_binomial_exact_paired_calculation():
    """Validates binomial exact probability test calculations for small sample sets."""
    # Complete symmetry (10 AI-only wins, 10 Baseline-only wins) -> p = 1.0
    p1 = EvaluationService._binomial_exact_p_value(10, 10)
    assert p1 == 1.0

    # Extreme asymmetry (15 AI-only wins, 0 Baseline-only wins) -> very small p-value
    p2 = EvaluationService._binomial_exact_p_value(15, 0)
    assert p2 < 0.001

    # Mismatched check (0, 0) -> p = 1.0
    p3 = EvaluationService._binomial_exact_p_value(0, 0)
    assert p3 == 1.0

# --- 4. Test: Low Sample Power Gating ---

def test_low_sample_power_warning(db: Session):
    """Asserts that small profile sets trigger statistical power warnings and log caution tags."""
    run = EvaluationService.run_offline_evaluation(db, name="Low Sample Test", random_seed=42, sample_size=5)
    db.commit()

    metrics = {m.strategy_group: m for m in run.metrics}
    paired = metrics["AI"].paired_metrics
    assert paired["statistical_warning"] is True

# --- 5. Test: Outcome Separation Gating ---

def test_outcome_source_separation(db: Session):
    """Asserts that simulated outcomes are saved as OFFLINE_SIMULATION and do not contaminate production runs."""
    run = EvaluationService.run_offline_evaluation(db, name="Separation Test", random_seed=999, sample_size=10)
    db.commit()

    # Query DB outcomes
    outcomes = db.query(RecoveryOutcome).filter(RecoveryOutcome.verification_source == "OFFLINE_SIMULATION").all()
    assert len(outcomes) > 0

    # Assert no simulated outcomes have production source labels
    production_outcomes = db.query(RecoveryOutcome).filter(
        RecoveryOutcome.verification_source.in_(["WEBHOOK", "API_CHECK"])
    ).all()
    assert len(production_outcomes) == 0

# --- 6. Test: API endpoints routing ---

def test_evaluation_api_endpoints(client: TestClient):
    """Verifies that API POST/GET routes execute and serialize correctly."""
    # POST execution
    payload = {
        "name": "API Matched Test Run",
        "random_seed": 777,
        "sample_size": 25
    }
    response = client.post("/api/v1/evaluation/run", json=payload)
    assert response.status_code == 201
    
    data = response.json()
    assert data["status"] == "success"
    assert "evaluation_id" in data
    eval_id = data["evaluation_id"]

    # GET metrics query
    get_response = client.get(f"/api/v1/evaluation/{eval_id}")
    assert get_response.status_code == 200
    
    run_data = get_response.json()
    assert run_data["id"] == eval_id
    assert run_data["name"] == "API Matched Test Run"
    assert len(run_data["metrics"]) == 2

    # Check breakdown categories presence
    for metric in run_data["metrics"]:
        assert len(metric["breakdowns"]) > 0
        breakdown_types = [b["breakdown_type"] for b in metric["breakdowns"]]
        assert "failure_reason" in breakdown_types
        assert "payment_method" in breakdown_types
        assert "transaction_value_bucket" in breakdown_types

