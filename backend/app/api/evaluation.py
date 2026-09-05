from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from app.core.database import get_db
from app.core.logging import logger
from app.services.evaluation_service import EvaluationService
from app.models.models import EvaluationRun, EvaluationMetric, EvaluationBreakdown

router = APIRouter()

# --- Pydantic Schemas for Requests and Responses ---

class EvaluationRequest(BaseModel):
    name: str = Field(..., description="Description / name of this evaluation run")
    random_seed: int = Field(42, description="Seeding used for synthetic simulation reproducibility")
    sample_size: int = Field(50, gt=0, le=500, description="Number of matched customer profile pairs to run")

class BreakdownResponse(BaseModel):
    breakdown_type: str
    key: str
    total_cases: int
    recovered_cases: int
    recovered_revenue: int
    expected_recovered_revenue: int

    class Config:
        from_attributes = True

class MetricResponse(BaseModel):
    strategy_group: str
    total_cases: int
    total_revenue_at_risk: int
    recovered_cases: int
    recovery_rate: float
    total_recovered_revenue: int
    recovery_revenue_rate: float
    average_recovered_amount: float
    avg_time_to_recovery: float
    median_time_to_recovery: float
    total_attempts: int
    blocked_actions: int
    fallback_actions: int
    ai_decision_success_rate: float
    ai_fallback_rate: float
    policy_block_rate: float
    expected_recovered_revenue: int
    actual_recovered_revenue: int
    confidence_interval_low: float
    confidence_interval_high: float
    paired_metrics: Optional[Dict[str, Any]] = None
    breakdowns: List[BreakdownResponse] = []

    class Config:
        from_attributes = True

class EvaluationRunResponse(BaseModel):
    id: int
    name: str
    random_seed: int
    simulation_config: Dict[str, Any]
    created_at: str
    metrics: List[MetricResponse] = []

class EvaluationListResponse(BaseModel):
    id: int
    name: str
    random_seed: int
    created_at: str

    class Config:
        from_attributes = True

# --- API Endpoints ---

@router.get("/evaluation", response_model=List[EvaluationListResponse])
def list_evaluation_runs(db: Session = Depends(get_db)) -> List[dict]:
    """
    Returns a list of all completed offline evaluation runs.
    """
    try:
        runs = db.query(EvaluationRun).order_by(EvaluationRun.created_at.desc()).all()
        return [
            {
                "id": r.id,
                "name": r.name,
                "random_seed": r.random_seed,
                "created_at": r.created_at.isoformat()
            }
            for r in runs
        ]
    except Exception as e:
        logger.warning("evaluation_runs_table_unavailable", error=str(e))
        return []

@router.post("/evaluation/run", response_model=dict, status_code=status.HTTP_201_CREATED)
def run_evaluation(
    request: EvaluationRequest,
    db: Session = Depends(get_db)
) -> dict:
    """
    Executes a matched paired simulation between BASELINE and AI recovery groups.
    Persists metrics, confidence boundaries, and McNemar test significance results.
    """
    try:
        run = EvaluationService.run_offline_evaluation(
            db=db,
            name=request.name,
            random_seed=request.random_seed,
            sample_size=request.sample_size
        )
        return {
            "status": "success",
            "message": "Offline matched-pair evaluation completed successfully.",
            "evaluation_id": run.id,
            "random_seed": run.random_seed,
            "created_at": run.created_at.isoformat()
        }
    except Exception as e:
        logger.error("api_evaluation_run_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute evaluation run: {e}"
        )

@router.get("/evaluation/{evaluation_id}", response_model=EvaluationRunResponse)
def get_evaluation_run(
    evaluation_id: int,
    db: Session = Depends(get_db)
) -> EvaluationRun:
    """
    Fetches the persistent evaluation metrics and breakdowns for a given run ID.
    """
    run = db.query(EvaluationRun).filter(EvaluationRun.id == evaluation_id).first()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"EvaluationRun with ID {evaluation_id} not found."
        )

    # Format created_at to string representation for Pydantic schema validation
    # (since class response requires a string format or custom converter)
    # We can serialize using standard attributes mapping
    # Let's map it explicitly to prevent parsing glitches
    metrics_response = []
    for metric in run.metrics:
        # Query breakdowns for this specific metric
        breakdowns = db.query(EvaluationBreakdown).filter(EvaluationBreakdown.metric_id == metric.id).all()
        breakdown_objs = [
            BreakdownResponse(
                breakdown_type=b.breakdown_type,
                key=b.key,
                total_cases=b.total_cases,
                recovered_cases=b.recovered_cases,
                recovered_revenue=b.recovered_revenue,
                expected_recovered_revenue=b.expected_recovered_revenue
            )
            for b in breakdowns
        ]

        metrics_response.append(
            MetricResponse(
                strategy_group=metric.strategy_group,
                total_cases=metric.total_cases,
                total_revenue_at_risk=metric.total_revenue_at_risk,
                recovered_cases=metric.recovered_cases,
                recovery_rate=metric.recovery_rate,
                total_recovered_revenue=metric.total_recovered_revenue,
                recovery_revenue_rate=metric.recovery_revenue_rate,
                average_recovered_amount=metric.average_recovered_amount,
                avg_time_to_recovery=metric.avg_time_to_recovery or 0.0,
                median_time_to_recovery=metric.median_time_to_recovery or 0.0,
                total_attempts=metric.total_attempts,
                blocked_actions=metric.blocked_actions,
                fallback_actions=metric.fallback_actions,
                ai_decision_success_rate=metric.ai_decision_success_rate or 0.0,
                ai_fallback_rate=metric.ai_fallback_rate or 0.0,
                policy_block_rate=metric.policy_block_rate,
                expected_recovered_revenue=metric.expected_recovered_revenue,
                actual_recovered_revenue=metric.actual_recovered_revenue,
                confidence_interval_low=metric.confidence_interval_low,
                confidence_interval_high=metric.confidence_interval_high,
                paired_metrics=metric.paired_metrics,
                breakdowns=breakdown_objs
            )
        )

    return EvaluationRunResponse(
        id=run.id,
        name=run.name,
        random_seed=run.random_seed,
        simulation_config=run.simulation_config,
        created_at=run.created_at.isoformat(),
        metrics=metrics_response
    )

