from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.models import RevenueRiskCase, RecoveryOutcome

router = APIRouter()

@router.get("/dashboard/metrics")
def get_dashboard_metrics(
    db: Session = Depends(get_db),
    include_synthetic: bool = False
) -> dict:
    """
    Computes executive metrics for the orchestrator dashboard.
    By default excludes synthetic/demo cases (include_synthetic=False).
    Set include_synthetic=true for simulation/demo metrics.
    """
    try:
        # 1. Total cases count
        base_q = db.query(RevenueRiskCase)
        if not include_synthetic:
            base_q = base_q.filter(RevenueRiskCase.is_synthetic == False)

        total_cases = base_q.count()
        active_cases = base_q.filter(
            ~RevenueRiskCase.current_state.in_(["RECOVERED", "STOPPED", "NOT_RECOVERED", "ACTION_BLOCKED"])
        ).count()
        recovered_cases = base_q.filter(
            RevenueRiskCase.current_state == "RECOVERED"
        ).count()

        recovery_rate = float(recovered_cases / total_cases) if total_cases > 0 else 0.0

        # 2. Revenue calculations (in paisa)
        rev_q = db.query(func.sum(RevenueRiskCase.amount_at_risk))
        if not include_synthetic:
            rev_q = rev_q.filter(RevenueRiskCase.is_synthetic == False)
        total_revenue_at_risk = rev_q.scalar() or 0

        outcome_q = db.query(func.sum(RecoveryOutcome.recovered_amount)).filter(
            RecoveryOutcome.is_recovered == True
        )
        if not include_synthetic:
            outcome_q = outcome_q.join(RevenueRiskCase).filter(
                RevenueRiskCase.is_synthetic == False,
                RecoveryOutcome.verification_source.in_(["WEBHOOK", "API_CHECK"])
            )
        total_recovered_revenue = outcome_q.scalar() or 0

        recovery_revenue_rate = float(total_recovered_revenue / total_revenue_at_risk) if total_revenue_at_risk > 0 else 0.0

        # 3. Strategy breakdowns (AI vs BASELINE), excluding live-demo fixtures.
        comparison_q = base_q.filter(RevenueRiskCase.dataset_type != "DEMO")
        ai_base_q = comparison_q.filter(RevenueRiskCase.recovery_strategy_group == "AI")
        ai_total = ai_base_q.count()
        ai_recovered = ai_base_q.filter(RevenueRiskCase.current_state == "RECOVERED").count()

        baseline_base_q = comparison_q.filter(RevenueRiskCase.recovery_strategy_group == "BASELINE")
        base_total = baseline_base_q.count()
        base_recovered = baseline_base_q.filter(RevenueRiskCase.current_state == "RECOVERED").count()

        ai_rate = float(ai_recovered / ai_total) if ai_total > 0 else 0.0
        base_rate = float(base_recovered / base_total) if base_total > 0 else 0.0
        uplift = float((ai_rate - base_rate) / base_rate) if base_rate > 0 else 0.0

        return {
            "total_cases": total_cases,
            "active_cases": active_cases,
            "recovered_cases": recovered_cases,
            "recovery_rate": round(recovery_rate, 4),
            "total_revenue_at_risk": total_revenue_at_risk,
            "total_recovered_revenue": total_recovered_revenue,
            "recovery_revenue_rate": round(recovery_revenue_rate, 4),
            "ai_group": {
                "total_cases": ai_total,
                "recovered_cases": ai_recovered,
                "recovery_rate": round(ai_rate, 4)
            },
            "baseline_group": {
                "total_cases": base_total,
                "recovered_cases": base_recovered,
                "recovery_rate": round(base_rate, 4)
            },
            "uplift": round(uplift, 4)
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to calculate dashboard metrics: {e}"
        )

