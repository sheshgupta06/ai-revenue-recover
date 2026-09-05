from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from pathlib import Path
from typing import List, Optional, Dict, Any
from app.core.database import get_db
from app.core.logging import logger
from app.models.models import RevenueRiskCase, Payment
from app.services.synthetic_generator import (
    seed_synthetic_dataset,
    seed_evaluation_and_demo_dataset,
    seed_csv_demo_dataset,
)
from app.services.risk_engine import RiskEngineService
from app.services.baseline_strategy import BaselineStrategyService

router = APIRouter()

# --- Request/Response Pydantic Models ---

class GenerateSyntheticRequest(BaseModel):
    num_customers: int = Field(default=120, ge=1, le=500)
    seed: int = Field(default=42)
    strategy_group: str = Field(default="MIXED", pattern="^(MIXED|BASELINE|AI)$")
    demo_payment_link: bool = True

class TriggerFailedPaymentRequest(BaseModel):
    payment_id: str = Field(
        ...,
        description="ID of an existing failed payment in the payments table. "
                    "Generate synthetic data first via POST /synthetic/generate, "
                    "then use one of the returned payment IDs.",
        examples=["pay_synth_0001"]
    )
    strategy_group: str = Field(default="BASELINE", pattern="^(BASELINE|AI)$")

    model_config = {
        "json_schema_extra": {
            "example": {
                "payment_id": "pay_synth_0001",
                "strategy_group": "AI"
            }
        }
    }

class TriggerCheckoutAbandonmentRequest(BaseModel):
    customer_id: str
    amount: int = Field(..., gt=0)  # in paisa
    failure_reason: str = "checkout_abandoned"
    strategy_group: str = Field(default="BASELINE", pattern="^(BASELINE|AI)$")

class CaseDetailResponse(BaseModel):
    id: int
    payment_id: Optional[str]
    customer_id: Optional[str]
    merchant_id: Optional[str]
    amount_at_risk: int
    event_type: str
    current_state: str
    failure_reason: Optional[str]
    risk_level: Optional[str]
    loss_risk_score: float
    recovery_probability: float
    prioritization_score: float
    recovery_strategy_group: str
    recovery_attempts: int
    is_synthetic: bool
    dataset_type: str

    class Config:
        from_attributes = True

# --- API Endpoints ---

@router.post("/synthetic/generate", status_code=status.HTTP_201_CREATED)
def generate_synthetic_data(
    request: GenerateSyntheticRequest,
    db: Session = Depends(get_db)
) -> dict:
    """
    Clears all existing synthetic records and seeds a fresh, reproducible synthetic dataset.
    Automatically ingests active failed payments and checkout abandonments as active cases.
    By default, generate an AI-only dataset and include a checkout-abandonment
    case for the payment-link demo.
    """
    try:
        if request.strategy_group == "MIXED":
            counts = seed_evaluation_and_demo_dataset(
                db,
                evaluation_count=max(100, request.num_customers),
                seed=request.seed,
                include_demo=request.demo_payment_link,
            )
            return {
                "status": "success",
                "message": "Added matched evaluation profiles and dedicated Razorpay Test Mode demo cases.",
                "seeded_records": counts,
            }

        counts = seed_synthetic_dataset(
            db, request.num_customers, request.seed,
            demo_payment_link=request.demo_payment_link,
        )
        
        # Automatically trigger ingestion for the newly seeded active payments/abandonments
        # Active payments have IDs starting with pay_synth_act_
        # Active customers have IDs starting with cust_synth_
        for i in range(1, request.num_customers + 1):
            cust_id = f"cust_synth_{i:04d}"
            pay_id = f"pay_synth_act_{i:04d}"
            
            # Retrieve the payment from DB
            payment = db.query(Payment).filter(Payment.id == pay_id).first()
            if payment:
                if payment.status == "failed":
                    strategy = (
                        request.strategy_group
                        if request.strategy_group != "MIXED"
                        else ("AI" if i % 2 == 1 else "BASELINE")
                    )
                    RiskEngineService.create_or_update_recovery_case(
                        db=db,
                        payment_id=payment.id,
                        event_type="FAILED_PAYMENT",
                        strategy_group=strategy
                    )
                elif payment.status == "created" and payment.failure_reason == "checkout_abandoned":
                    # Ingest checkout abandonment case
                    strategy = (
                        request.strategy_group
                        if request.strategy_group != "MIXED"
                        else ("AI" if i % 2 == 1 else "BASELINE")
                    )
                    RiskEngineService.create_or_update_recovery_case(
                        db=db,
                        payment_id=None,
                        event_type="CHECKOUT_ABANDONMENT",
                        customer_id=payment.customer_id,
                        amount=payment.amount,
                        failure_reason=payment.failure_reason,
                        is_synthetic=True,
                        strategy_group=strategy
                    )
                    
        db.commit()
        return {
            "status": "success",
            "message": "Seeded synthetic dataset and initialized active recovery cases successfully.",
            "seeded_records": counts
        }
    except Exception as e:
        db.rollback()
        logger.error("synthetic_generation_endpoint_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Synthetic dataset generation failed: {e}"
        )


@router.post("/synthetic/demo-csv/import", status_code=status.HTTP_201_CREATED)
def import_demo_csv(db: Session = Depends(get_db)) -> dict:
    """Import the repository's supplemental CSV rows as active DEMO_REFERENCE cases."""
    csv_path = Path(__file__).resolve().parents[3] / "payment_link_demo_dataset.csv"
    if not csv_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Demo CSV dataset not found.")
    try:
        counts = seed_csv_demo_dataset(db, str(csv_path))
        return {"status": "success", "message": "CSV demo cases are active.", "seeded_records": counts}
    except Exception as e:
        db.rollback()
        logger.error("demo_csv_import_failed", error=str(e))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.post("/cases/trigger-failed-payment", response_model=CaseDetailResponse)
def trigger_failed_payment_case(
    request: TriggerFailedPaymentRequest,
    db: Session = Depends(get_db)
):
    """
    Manually triggers case ingestion and risk scoring evaluation for an existing failed payment.

    **Before calling this endpoint**, generate synthetic data via:
    `POST /api/v1/synthetic/generate`

    Then pass one of the `pay_synth_*` payment IDs returned in the synthetic payments list.
    Providing a non-existent `payment_id` returns HTTP 400.
    """
    # Validate payment_id exists BEFORE attempting any DB write
    payment = db.query(Payment).filter(Payment.id == request.payment_id).first()
    if not payment:
        logger.warning(
            "trigger_failed_payment_not_found",
            payment_id=request.payment_id
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Payment not found: '{request.payment_id}'. "
                "Provide an existing payment_id. "
                "Generate synthetic data first via POST /api/v1/synthetic/generate "
                "and use one of the pay_synth_* IDs."
            )
        )

    try:
        case = RiskEngineService.create_or_update_recovery_case(
            db=db,
            payment_id=request.payment_id,
            event_type="FAILED_PAYMENT",
            strategy_group=request.strategy_group
        )
        db.commit()
        return case
    except Exception as e:
        db.rollback()
        logger.error("trigger_failed_payment_endpoint_failed", payment_id=request.payment_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Case ingestion failed: {e}"
        )

@router.post("/cases/trigger-checkout-abandonment", response_model=CaseDetailResponse)
def trigger_checkout_abandonment_case(
    request: TriggerCheckoutAbandonmentRequest,
    db: Session = Depends(get_db)
):
    """
    Manually triggers cart abandonment evaluation and case creation.
    """
    try:
        case = RiskEngineService.create_or_update_recovery_case(
            db=db,
            payment_id=None,
            event_type="CHECKOUT_ABANDONMENT",
            customer_id=request.customer_id,
            amount=request.amount,
            failure_reason=request.failure_reason,
            is_synthetic=True,  # Abandonment is synthetic in Phase 4
            strategy_group=request.strategy_group
        )
        db.commit()
        return case
    except Exception as e:
        db.rollback()
        logger.error("trigger_checkout_abandonment_endpoint_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"In-gestion failed: {e}"
        )

@router.get("/cases/", response_model=List[CaseDetailResponse])
def list_all_cases(
    db: Session = Depends(get_db),
    state: str = None,
    strategy_group: str = None,
    is_synthetic: bool = None,
    dataset_type: str = None,
    limit: int = 100
):
    """
    Returns all recovery cases with optional filters.
    Supports: state, strategy_group, is_synthetic, dataset_type, limit.
    """
    try:
        q = db.query(RevenueRiskCase)
        if state:
            q = q.filter(RevenueRiskCase.current_state == state)
        if strategy_group:
            q = q.filter(RevenueRiskCase.recovery_strategy_group == strategy_group)
        if is_synthetic is not None:
            q = q.filter(RevenueRiskCase.is_synthetic == is_synthetic)
        if dataset_type:
            q = q.filter(RevenueRiskCase.dataset_type == dataset_type.upper())
        cases = q.order_by(RevenueRiskCase.prioritization_score.desc()).limit(limit).all()
        return cases
    except Exception as e:
        logger.error("list_all_cases_endpoint_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query cases: {e}"
        )

@router.get("/cases/synthetic", response_model=List[CaseDetailResponse])
def list_synthetic_cases(
    db: Session = Depends(get_db)
):
    """
    Returns active synthetic cases sorted by prioritization score descending.
    """
    try:
        cases = db.query(RevenueRiskCase)\
                  .filter(RevenueRiskCase.is_synthetic == True)\
                  .order_by(RevenueRiskCase.prioritization_score.desc())\
                  .all()
        return cases
    except Exception as e:
        logger.error("list_synthetic_cases_endpoint_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query cases: {e}"
        )

@router.post("/cases/{case_id}/baseline-step")
def execute_case_baseline_step(
    case_id: int,
    db: Session = Depends(get_db)
) -> dict:
    """
    Executes a single step of the deterministic baseline strategy on the case file.
    """
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.id == case_id).first()
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"RevenueRiskCase with ID {case_id} not found."
        )

    if case.recovery_strategy_group != "BASELINE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Case strategy group is {case.recovery_strategy_group}, expected BASELINE."
        )

    try:
        action = BaselineStrategyService.execute_baseline_step(db, case)
        db.commit()
        return {
            "status": "success",
            "action_executed": action.action_type,
            "parameters": action.parameters,
            "case_new_state": case.current_state,
            "recovery_attempts": case.recovery_attempts,
            "new_prioritization_score": case.prioritization_score
        }
    except Exception as e:
        db.rollback()
        logger.error("execute_baseline_step_endpoint_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Baseline step execution failed: {e}"
        )

@router.post("/cases/{case_id}/ai-step")
def execute_case_ai_step(
    case_id: int,
    db: Session = Depends(get_db)
) -> dict:
    """
    Executes a single step of the AI Decision Engine on the case file to propose next recovery action.
    """
    from app.services.ai_decision import AIDecisionService
    
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.id == case_id).first()
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"RevenueRiskCase with ID {case_id} not found."
        )

    if case.recovery_strategy_group != "AI":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Case strategy group is {case.recovery_strategy_group}, expected AI."
        )

    try:
        action = AIDecisionService.execute_ai_step(db, case)
        db.commit()
        return {
            "status": "success",
            "action_proposed": action.action_type,
            "parameters": action.parameters,
            "case_new_state": case.current_state,
            "recovery_attempts": case.recovery_attempts,
            "prioritization_score": case.prioritization_score,
            "ai_decision_id": action.ai_decision_id
        }
    except Exception as e:
        db.rollback()
        logger.error("execute_ai_step_endpoint_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI step execution failed: {e}"
        )

@router.get("/cases/{case_id}")
def get_case_details(
    case_id: int,
    db: Session = Depends(get_db)
) -> dict:
    """
    Returns detailed case information including nested relationships
    for the sliding details drawer UI.
    """
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.id == case_id).first()
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"RevenueRiskCase with ID {case_id} not found."
        )

    return {
        "id": case.id,
        "payment_id": case.payment_id,
        "customer_id": case.customer_id,
        "merchant_id": case.merchant_id,
        "amount_at_risk": case.amount_at_risk,
        "event_type": case.event_type,
        "current_state": case.current_state,
        "failure_reason": case.failure_reason,
        "risk_level": case.risk_level,
        "loss_risk_score": case.loss_risk_score,
        "recovery_probability": case.recovery_probability,
        "prioritization_score": case.prioritization_score,
        "recovery_strategy_group": case.recovery_strategy_group,
        "recovery_attempts": case.recovery_attempts,
        "max_attempts": case.max_attempts,
        "is_synthetic": case.is_synthetic,
        "created_at": case.created_at.isoformat(),
        "updated_at": case.updated_at.isoformat(),
        "customer": {
            "id": case.customer.id,
            "email": case.customer.email,
            "name": case.customer.name,
            "is_subscribed": case.customer.metadata_json.get("is_subscribed") is True if case.customer and case.customer.metadata_json else False
        } if case.customer else None,
        "payment": {
            "id": case.payment.id,
            "amount": case.payment.amount,
            "currency": case.payment.currency,
            "status": case.payment.status,
            "method": case.payment.method,
            "failure_reason": case.payment.failure_reason,
        } if case.payment else None,
        "ai_decisions": [
            {
                "id": dec.id,
                "case_id": dec.case_id,
                "recommended_action": dec.recommended_action,
                "confidence": dec.confidence,
                "reason": dec.reason,
                "expected_recovery_probability": dec.expected_recovery_probability,
                "expected_recovered_amount": dec.expected_recovered_amount,
                "raw_decision_output": dec.raw_decision_output,
                "created_at": dec.created_at.isoformat()
            }
            for dec in case.ai_decisions
        ],
        "recovery_actions": [
            {
                "id": act.id,
                "case_id": act.case_id,
                "action_type": act.action_type,
                "status": act.status,
                "parameters": act.parameters,
                "executed_at": act.executed_at.isoformat() if act.executed_at else None,
                "created_at": act.created_at.isoformat()
            }
            for act in case.recovery_actions
        ],
        "outcomes": [
            {
                "id": out.id,
                "case_id": out.case_id,
                "action_id": out.action_id,
                "recovered_amount": out.recovered_amount,
                "is_recovered": out.is_recovered,
                "verification_source": out.verification_source,
                "raw_verification_data": out.raw_verification_data,
                "created_at": out.created_at.isoformat()
            }
            for out in case.outcomes
        ],
        "audit_logs": [
            {
                "id": log.id,
                "case_id": log.case_id,
                "event_name": log.event_name,
                "description": log.description,
                "timestamp": log.timestamp.isoformat()
            }
            for log in case.audit_logs
        ]
    }

@router.post("/cases/{case_id}/execute-pending")
def execute_case_pending_action(
    case_id: int,
    db: Session = Depends(get_db)
) -> dict:
    """
    Validates the latest PENDING recovery action recommendation against Policy Engine safety rules.
    If approved, executes it via Action Executor.
    If blocked, triggers the double policy fallback loop using deterministic baseline alternatives.
    """
    from datetime import datetime
    from app.services.policy_engine import PolicyEngineService
    from app.services.action_executor import ActionExecutorService
    from app.models.models import RecoveryAction, AuditLog
    
    # 1. Fetch case
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.id == case_id).first()
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"RevenueRiskCase with ID {case_id} not found."
        )

    # Acquire row lock for the case inside the transaction (Idempotency)
    # PENDING = proposed by AI/baseline, awaiting execute
    # SCHEDULED = baseline scheduled action (PAYMENT_LINK/REMINDER) also awaiting execute
    action = db.query(RecoveryAction)\
               .filter(
                   RecoveryAction.case_id == case.id,
                   RecoveryAction.status.in_(["PENDING", "SCHEDULED"])
               )\
               .order_by(RecoveryAction.created_at.desc())\
               .with_for_update()\
               .first()

    if not action:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending or scheduled recovery action found for this case."
        )

    try:
        # 2. Run Policy Validation
        is_approved, block_reason = PolicyEngineService.validate_action(db, case, action)

        if is_approved:
            logger.info("policy_engine_approved_recommendation", case_id=case.id, action_id=action.id)
            
            # Log approval in Audit
            audit_approve = AuditLog(
                case_id=case.id,
                event_name="POLICY_APPROVED",
                description=f"Proposed action {action.action_type} approved by policy engine.",
                timestamp=datetime.utcnow()
            )
            db.add(audit_approve)
            db.flush()

            # Execute action
            executed_act = ActionExecutorService.execute_approved_action(db, case, action)
            db.commit()

            return {
                "status": "success",
                "policy_approved": True,
                "action_executed": executed_act.action_type,
                "execution_status": executed_act.status,
                "parameters": executed_act.parameters,
                "case_new_state": case.current_state
            }
        else:
            logger.warning("policy_engine_blocked_recommendation", case_id=case.id, action_id=action.id, reason=block_reason)
            
            # Mark action as blocked
            action.status = "BLOCKED"
            action.parameters = {"block_reason": block_reason}
            db.add(action)

            # Log block in Audit
            audit_block = AuditLog(
                case_id=case.id,
                event_name="POLICY_BLOCKED",
                description=f"Proposed action {action.action_type} blocked by policy engine: {block_reason}.",
                metadata_json={"action_type": action.action_type, "block_reason": block_reason},
                timestamp=datetime.utcnow()
            )
            db.add(audit_block)
            db.flush()

            # Trigger Double Loop Fallback
            # 1. Select deterministic baseline alternative
            fallback_action, fallback_params = BaselineStrategyService.determine_next_action(
                case.failure_reason, 
                case.recovery_attempts
            )

            # 2. Create the PENDING fallback action
            fallback_act = RecoveryAction(
                case_id=case.id,
                ai_decision_id=None,
                action_type=fallback_action,
                parameters=fallback_params,
                status="PENDING",
                created_at=datetime.utcnow()
            )
            db.add(fallback_act)
            db.flush()

            # Log fallback creation in Audit
            audit_fb_create = AuditLog(
                case_id=case.id,
                event_name="FALLBACK_LOOP_TRIGGERED",
                description=f"Policy block occurred. Proposed baseline fallback action: {fallback_action}.",
                metadata_json={"fallback_action": fallback_action, "parameters": fallback_params},
                timestamp=datetime.utcnow()
            )
            db.add(audit_fb_create)
            db.flush()

            # 3. Validate baseline fallback action via Policy Engine
            fb_approved, fb_block_reason = PolicyEngineService.validate_action(db, case, fallback_act)

            if fb_approved:
                # Log fallback policy approval
                audit_fb_approve = AuditLog(
                    case_id=case.id,
                    event_name="POLICY_APPROVED",
                    description=f"Fallback action {fallback_action} approved by policy engine.",
                    timestamp=datetime.utcnow()
                )
                db.add(audit_fb_approve)
                db.flush()

                # Execute fallback
                executed_fb = ActionExecutorService.execute_approved_action(db, case, fallback_act)
                db.commit()

                return {
                    "status": "fallback_success",
                    "policy_approved": False,
                    "original_block_reason": block_reason,
                    "action_executed": executed_fb.action_type,
                    "execution_status": executed_fb.status,
                    "parameters": executed_fb.parameters,
                    "case_new_state": case.current_state
                }
            else:
                # Log fallback policy block
                fallback_act.status = "BLOCKED"
                fallback_act.parameters = {"block_reason": fb_block_reason}
                case.current_state = "STOPPED"  # Fallback also blocked -> terminal STOPPED
                case.updated_at = datetime.utcnow()

                audit_fb_block = AuditLog(
                    case_id=case.id,
                    event_name="POLICY_BLOCKED",
                    description=f"Fallback action {fallback_action} blocked by policy engine: {fb_block_reason}.",
                    metadata_json={"fallback_action": fallback_action, "block_reason": fb_block_reason},
                    timestamp=datetime.utcnow()
                )
                db.add(audit_fb_block)
                db.commit()

                return {
                    "status": "fallback_blocked",
                    "policy_approved": False,
                    "original_block_reason": block_reason,
                    "fallback_block_reason": fb_block_reason,
                    "action_executed": fallback_action,
                    "execution_status": "BLOCKED",
                    "case_new_state": case.current_state
                }

    except Exception as e:
        db.rollback()
        logger.error("execute_pending_action_endpoint_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pending action execution failed: {e}"
        )

@router.post("/cases/{case_id}/verify-outcome")
def verify_case_outcome(
    case_id: int,
    db: Session = Depends(get_db)
) -> dict:
    """
    Manually triggers polling status checks on Razorpay for executed actions on the case.
    Reconciles terminal transitions and records outcomes.
    """
    from app.services.outcome_verification import OutcomeVerificationService
    from app.models.models import RevenueRiskCase
    
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.id == case_id).first()
    if not case:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"RevenueRiskCase with ID {case_id} not found."
        )

    # Begin database transaction with a row lock on the case (Idempotency)
    # We do a SELECT FOR UPDATE to prevent concurrent polling/webhook writes
    db.query(RevenueRiskCase).filter(RevenueRiskCase.id == case_id).with_for_update().first()

    try:
        outcome = OutcomeVerificationService.poll_and_verify_action_outcome(db, case.id)
        if outcome:
            return {
                "status": "success",
                "is_resolved": True,
                "is_recovered": outcome.is_recovered,
                "recovered_amount": outcome.recovered_amount,
                "verification_source": outcome.verification_source,
                "case_new_state": case.current_state
            }
        else:
            return {
                "status": "success",
                "is_resolved": False,
                "description": "Payment verification is still pending or status is non-terminal.",
                "case_new_state": case.current_state
            }
    except Exception as e:
        logger.error("verify_case_outcome_endpoint_failed", case_id=case_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Manual outcome verification check failed: {e}"
        )




