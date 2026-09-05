import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, field_validator
from app.models.models import RevenueRiskCase, AIDecision, RecoveryAction, AuditLog
from app.core.config import settings
from app.core.logging import logger
from app.services.prompts import PROMPT_VERSION, SYSTEM_INSTRUCTIONS, get_case_analysis_prompt
from app.services.llm_provider import LLMProvider, LLMProviderException
from app.services.context_builder import ContextBuilder
from app.services.baseline_strategy import BaselineStrategyService

# --- Pydantic Schema for AI Output Validation ---

class AIDecisionValidationSchema(BaseModel):
    action: str
    delay_minutes: Optional[int] = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason: str
    expected_recovery_probability: float = Field(..., ge=0.0, le=1.0)

    @field_validator("action")
    @classmethod
    def validate_action_enum(cls, v: str) -> str:
        allowed = ["RETRY_NOW", "RETRY_LATER", "ALTERNATE_PAYMENT", "PAYMENT_LINK", "REMINDER", "HUMAN_ESCALATION", "STOP"]
        if v not in allowed:
            raise ValueError(f"Action must be one of {allowed}")
        return v

class AIDecisionService:
    @staticmethod
    def execute_ai_step(db: Session, case: RevenueRiskCase) -> RecoveryAction:
        """
        Orchestrates Context building, LLM query, Pydantic validation, 
        fallback execution, and creates PENDING recovery recommendations.
        Does NOT execute or schedule recovery actions.
        """
        logger.info("ai_decision_engine_triggered", case_id=case.id, group=case.recovery_strategy_group)

        if case.recovery_strategy_group != "AI":
            raise ValueError(f"Case strategy group is {case.recovery_strategy_group}, expected AI.")

        # 1. Build context & prompt
        context_json_str = ContextBuilder.build_case_context(db, case)
        prompt = get_case_analysis_prompt(context_json_str)

        raw_llm_output = ""
        decision_record = None

        try:
            # 2. Call LLM provider
            raw_llm_output = LLMProvider.generate_decision(prompt, SYSTEM_INSTRUCTIONS)
            logger.info("ai_decision_raw_output_received", case_id=case.id, output=raw_llm_output)

            # Cleanup potential markdown wrapper blocks
            cleaned_output = raw_llm_output.strip()
            if cleaned_output.startswith("```"):
                # Remove starting markdown fence
                lines = cleaned_output.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].startswith("```"):
                    lines = lines[:-1]
                cleaned_output = "\n".join(lines).strip()

            # 3. Parse JSON
            parsed_data = json.loads(cleaned_output)

            # 4. Schema validation
            validated_data = AIDecisionValidationSchema(**parsed_data)

            # 5. Success Path: Persist structured decision recommendation
            expected_recovered_amt = int(case.amount_at_risk * validated_data.expected_recovery_probability)
            
            decision_record = AIDecision(
                case_id=case.id,
                recommended_action=validated_data.action,
                confidence=validated_data.confidence,
                reason=validated_data.reason,
                expected_recovery_probability=validated_data.expected_recovery_probability,
                expected_recovered_amount=expected_recovered_amt,
                raw_decision_output=parsed_data,
                created_at=datetime.utcnow()
            )
            # Add prompts versioning and model settings to decision metadata
            decision_record.raw_decision_output["metadata_json"] = {
                "prompt_version": PROMPT_VERSION,
                "model_name": settings.LLM_MODEL_NAME,
                "provider": settings.LLM_PROVIDER
            }
            db.add(decision_record)
            db.flush()

            # Create PENDING RecoveryAction recommendation
            action = RecoveryAction(
                case_id=case.id,
                ai_decision_id=decision_record.id,
                action_type=validated_data.action,
                parameters={"delay_minutes": validated_data.delay_minutes} if validated_data.delay_minutes is not None else None,
                status="PENDING",  # Strict boundary: recommendations are generated as proposed (PENDING) only
                created_at=datetime.utcnow()
            )
            db.add(action)

            # Transition case status to ACTION_PROPOSED
            case.current_state = "ACTION_PROPOSED"
            case.updated_at = datetime.utcnow()

            # Write event audit trail
            audit = AuditLog(
                case_id=case.id,
                event_name="AI_DECISION_SUCCESS",
                description=f"AI Decision generated. Action proposed: {validated_data.action}. Reason: {validated_data.reason}",
                metadata_json={
                    "recommended_action": validated_data.action,
                    "confidence": validated_data.confidence,
                    "expected_recovery_probability": validated_data.expected_recovery_probability,
                    "expected_recovered_amount": expected_recovered_amt
                },
                timestamp=datetime.utcnow()
            )
            db.add(audit)
            db.flush()

            logger.info("ai_decision_completed", case_id=case.id, action=action.action_type)
            return action

        except Exception as e:
            # 6. Fallback Path: execute deterministic baseline recovery action proposal
            logger.error("ai_decision_failed_triggering_fallback", case_id=case.id, error=str(e))

            # Query baseline fallback recommendation
            fallback_action, fallback_params = BaselineStrategyService.determine_next_action(
                case.failure_reason, 
                case.recovery_attempts
            )

            # Store the fallback decision details
            decision_record = AIDecision(
                case_id=case.id,
                recommended_action=fallback_action,
                confidence=0.0,
                reason=f"Deterministic fallback triggered due to AI error: {e}",
                expected_recovery_probability=case.recovery_probability,  # Retain case deterministic probability
                expected_recovered_amount=int(case.amount_at_risk * case.recovery_probability),
                raw_decision_output={
                    "error": str(e),
                    "raw_output_attempted": raw_llm_output,
                    "metadata_json": {
                        "prompt_version": PROMPT_VERSION,
                        "model_name": "none",
                        "provider": "FALLBACK"
                    }
                },
                created_at=datetime.utcnow()
            )
            db.add(decision_record)
            db.flush()

            # Create PENDING RecoveryAction recommendation based on baseline
            action = RecoveryAction(
                case_id=case.id,
                ai_decision_id=decision_record.id,
                action_type=fallback_action,
                parameters=fallback_params,
                status="PENDING",  # Proposed status only
                created_at=datetime.utcnow()
            )
            db.add(action)

            # Transition case status to ACTION_PROPOSED
            case.current_state = "ACTION_PROPOSED"
            case.updated_at = datetime.utcnow()

            # Write event audit trail
            audit = AuditLog(
                case_id=case.id,
                event_name="AI_DECISION_FALLBACK",
                description=f"AI unavailable → Baseline fallback. Proposed baseline action: {fallback_action}",
                metadata_json={
                    "error": str(e),
                    "fallback_action": fallback_action,
                    "parameters": fallback_params
                },
                timestamp=datetime.utcnow()
            )
            db.add(audit)
            db.flush()

            logger.info("ai_decision_fallback_completed", case_id=case.id, action=action.action_type)
            return action
