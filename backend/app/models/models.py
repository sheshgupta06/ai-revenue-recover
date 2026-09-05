from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    Float,
    DateTime,
    ForeignKey,
    Text,
    JSON,
    BigInteger,
)
from sqlalchemy.orm import relationship
from app.core.database import Base

class Customer(Base):
    __tablename__ = "customers"

    id = Column(String, primary_key=True, index=True)  # E.g. cust_123 or merchant customer id
    email = Column(String, index=True, nullable=True)
    phone = Column(String, nullable=True)
    name = Column(String, nullable=True)
    is_synthetic = Column(Boolean, default=False, nullable=False, index=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    payments = relationship("Payment", back_populates="customer")
    cases = relationship("RevenueRiskCase", back_populates="customer")

class Merchant(Base):
    __tablename__ = "merchants"

    id = Column(String, primary_key=True, index=True)  # E.g. acc_123
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    payments = relationship("Payment", back_populates="merchant")
    cases = relationship("RevenueRiskCase", back_populates="merchant")

class Payment(Base):
    __tablename__ = "payments"

    id = Column(String, primary_key=True, index=True)  # Razorpay payment ID (e.g., pay_123)
    amount = Column(Integer, nullable=False)           # Amount in paisa
    currency = Column(String(3), default="INR", nullable=False)
    status = Column(String, index=True, nullable=False)  # created, authorized, captured, refunded, failed
    method = Column(String, nullable=True)             # card, netbanking, wallet, upi
    failure_reason = Column(String, nullable=True)
    customer_id = Column(String, ForeignKey("customers.id"), nullable=False)
    merchant_id = Column(String, ForeignKey("merchants.id"), nullable=True)
    is_synthetic = Column(Boolean, default=False, nullable=False, index=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    customer = relationship("Customer", back_populates="payments")
    merchant = relationship("Merchant", back_populates="payments")
    cases = relationship("RevenueRiskCase", back_populates="payment")

class RevenueRiskCase(Base):
    __tablename__ = "revenue_risk_cases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    payment_id = Column(String, ForeignKey("payments.id"), nullable=True)  # Nullable for checkout abandonment
    customer_id = Column(String, ForeignKey("customers.id"), nullable=True)
    merchant_id = Column(String, ForeignKey("merchants.id"), nullable=True)
    amount_at_risk = Column(Integer, nullable=False)                       # Amount in paisa
    event_type = Column(String, nullable=False)                            # FAILED_PAYMENT, CHECKOUT_ABANDONMENT, etc.
    current_state = Column(String, index=True, default="NEW", nullable=False)  # NEW, ANALYZING, RECOVERED, STOPPED, etc.
    failure_reason = Column(String, nullable=True)
    risk_level = Column(String, nullable=True)                             # LOW, MEDIUM, HIGH
    loss_risk_score = Column(Float, default=0.0, nullable=False)
    recovery_probability = Column(Float, default=0.0, nullable=False)
    prioritization_score = Column(Float, default=0.0, nullable=False)
    recovery_strategy_group = Column(String, default="BASELINE", nullable=False)
    recovery_attempts = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=3, nullable=False)
    is_synthetic = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    customer = relationship("Customer", back_populates="cases")
    merchant = relationship("Merchant", back_populates="cases")
    payment = relationship("Payment", back_populates="cases")

    ai_decisions = relationship("AIDecision", back_populates="case")
    recovery_actions = relationship("RecoveryAction", back_populates="case")
    outcomes = relationship("RecoveryOutcome", back_populates="case")
    audit_logs = relationship("AuditLog", back_populates="case")

class AIDecision(Base):
    __tablename__ = "ai_decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(Integer, ForeignKey("revenue_risk_cases.id"), nullable=False)
    recommended_action = Column(String, nullable=False)                    # RETRY_NOW, RETRY_LATER, STOP, etc.
    confidence = Column(Float, nullable=False)
    reason = Column(Text, nullable=False)
    expected_recovery_probability = Column(Float, nullable=False)
    expected_recovered_amount = Column(Integer, nullable=False)            # Amount in paisa
    raw_decision_output = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    case = relationship("RevenueRiskCase", back_populates="ai_decisions")
    recovery_actions = relationship("RecoveryAction", back_populates="ai_decision")

class RecoveryAction(Base):
    __tablename__ = "recovery_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(Integer, ForeignKey("revenue_risk_cases.id"), nullable=False)
    ai_decision_id = Column(Integer, ForeignKey("ai_decisions.id"), nullable=True)  # Null if baseline / non-AI fallback
    action_type = Column(String, nullable=False)                           # RETRY_NOW, ALTERNATE_PAYMENT, etc.
    parameters = Column(JSON, nullable=True)                               # delay, links, etc.
    status = Column(String, index=True, default="PENDING", nullable=False)  # PENDING, SCHEDULED, EXECUTED, FAILED
    executed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    case = relationship("RevenueRiskCase", back_populates="recovery_actions")
    ai_decision = relationship("AIDecision", back_populates="recovery_actions")
    outcomes = relationship("RecoveryOutcome", back_populates="action")

class RecoveryOutcome(Base):
    __tablename__ = "recovery_outcomes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(Integer, ForeignKey("revenue_risk_cases.id"), nullable=False)
    action_id = Column(Integer, ForeignKey("recovery_actions.id"), nullable=False)
    recovered_amount = Column(Integer, nullable=False)                     # In paisa (0 if failed)
    is_recovered = Column(Boolean, default=False, nullable=False)
    verification_source = Column(String, nullable=False)                   # WEBHOOK, API_CHECK
    raw_verification_data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    case = relationship("RevenueRiskCase", back_populates="outcomes")
    action = relationship("RecoveryAction", back_populates="outcomes")

class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String, unique=True, index=True, nullable=False)     # E.g. evt_123 (ensures idempotency)
    event_type = Column(String, index=True, nullable=False)                 # E.g. payment.failed
    payload = Column(JSON, nullable=False)
    processed = Column(Boolean, default=False, nullable=False)
    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(Integer, ForeignKey("revenue_risk_cases.id"), nullable=True)
    event_name = Column(String, index=True, nullable=False)
    description = Column(Text, nullable=False)
    metadata_json = Column(JSON, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)

    case = relationship("RevenueRiskCase", back_populates="audit_logs")

class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    random_seed = Column(Integer, nullable=False)
    simulation_config = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    metrics = relationship("EvaluationMetric", back_populates="run", cascade="all, delete-orphan")

class EvaluationMetric(Base):
    __tablename__ = "evaluation_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("evaluation_runs.id"), nullable=False)
    strategy_group = Column(String, nullable=False)
    
    total_cases = Column(Integer, nullable=False)
    total_revenue_at_risk = Column(BigInteger, nullable=False)
    recovered_cases = Column(Integer, nullable=False)
    recovery_rate = Column(Float, nullable=False)
    total_recovered_revenue = Column(BigInteger, nullable=False)
    recovery_revenue_rate = Column(Float, nullable=False)
    average_recovered_amount = Column(Float, nullable=False)
    avg_time_to_recovery = Column(Float, nullable=True)
    median_time_to_recovery = Column(Float, nullable=True)
    
    total_attempts = Column(Integer, nullable=False)
    blocked_actions = Column(Integer, nullable=False)
    fallback_actions = Column(Integer, nullable=False)
    ai_decision_success_rate = Column(Float, nullable=True)
    ai_fallback_rate = Column(Float, nullable=True)
    policy_block_rate = Column(Float, nullable=False)
    expected_recovered_revenue = Column(BigInteger, nullable=False)
    actual_recovered_revenue = Column(BigInteger, nullable=False)
    
    confidence_interval_low = Column(Float, nullable=False)
    confidence_interval_high = Column(Float, nullable=False)
    
    paired_metrics = Column(JSON, nullable=True)

    run = relationship("EvaluationRun", back_populates="metrics")
    breakdowns = relationship("EvaluationBreakdown", back_populates="metric", cascade="all, delete-orphan")

class EvaluationBreakdown(Base):
    __tablename__ = "evaluation_breakdowns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    metric_id = Column(Integer, ForeignKey("evaluation_metrics.id"), nullable=False)
    breakdown_type = Column(String, nullable=False)
    key = Column(String, nullable=False)
    total_cases = Column(Integer, nullable=False)
    recovered_cases = Column(Integer, nullable=False)
    recovered_revenue = Column(BigInteger, nullable=False)
    expected_recovered_revenue = Column(BigInteger, nullable=False)

    metric = relationship("EvaluationMetric", back_populates="breakdowns")

