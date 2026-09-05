import random
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy.orm import Session
from app.models.models import Customer, Payment, Merchant
from app.core.logging import logger

# --- 1. Pydantic Validation Schemas ---

class SyntheticCustomerSchema(BaseModel):
    id: str = Field(..., pattern=r"^cust_synth_[a-zA-Z0-9_]+$")
    email: EmailStr
    phone: Optional[str]
    name: str
    is_synthetic: bool = True
    metadata_json: Dict[str, Any]

class SyntheticPaymentSchema(BaseModel):
    id: str = Field(..., pattern=r"^(pay_synth_|demo_payment_link_)[a-zA-Z0-9_]+$")
    amount: int = Field(..., gt=0)  # in paisa
    currency: str = "INR"
    status: str
    method: str
    failure_reason: Optional[str] = None
    customer_id: str
    is_synthetic: bool = True
    dataset_type: str = "EVALUATION"
    metadata_json: Dict[str, Any]
    created_at: datetime

# --- 2. Deterministic Seeding and Generator logic ---

def seed_random(seed_val: int = 42) -> None:
    """Sets static seeds for reproducibility across runs."""
    random.seed(seed_val)

def generate_customer_profile(index: int) -> dict:
    """Generates customer demographics and behavioral profiles."""
    customer_id = f"cust_synth_{index:04d}"
    
    # 4 distinct segments (highly success history to poor/none)
    segment_roll = random.random()
    if segment_roll < 0.35:
        # High value/good history
        success_rate = random.uniform(0.92, 1.0)
        tenure = random.randint(180, 730)
        subscribed = True
    elif segment_roll < 0.70:
        # Average history
        success_rate = random.uniform(0.70, 0.90)
        tenure = random.randint(30, 179)
        subscribed = random.choice([True, False])
    elif segment_roll < 0.90:
        # Poor history
        success_rate = random.uniform(0.10, 0.40)
        tenure = random.randint(5, 29)
        subscribed = False
    else:
        # New customer (default/no history)
        success_rate = 0.80
        tenure = random.randint(1, 4)
        subscribed = False

    return {
        "id": customer_id,
        "email": f"cust_synth_{index:04d}@example.com",
        "phone": f"+9199000{index:05d}",
        "name": f"Synthetic Customer {index:04d}",
        "is_synthetic": True,
        "metadata_json": {
            "historical_success_rate": round(success_rate, 2),
            "customer_tenure_days": tenure,
            "is_subscribed": subscribed
        }
    }

def generate_payments_for_customer(
    customer_profile: dict,
    num_payments: int,
    force_checkout_abandonment: bool = False,
) -> List[dict]:
    """Generates a history of payments plus one active failed payment/checkout abandonment."""
    payments = []
    cust_id = customer_profile["id"]
    success_rate = customer_profile["metadata_json"]["historical_success_rate"]
    tenure = customer_profile["metadata_json"]["customer_tenure_days"]

    methods = ["upi", "card", "netbanking", "wallet"]
    failure_reasons = {
        "upi": ["bank_timeout", "insufficient_funds"],
        "card": ["expired_card", "insufficient_funds", "bank_timeout"],
        "netbanking": ["bank_timeout"],
        "wallet": ["insufficient_funds"]
    }

    # Generate historical payments (tenure in past)
    start_date = datetime.utcnow() - timedelta(days=tenure)
    
    for i in range(num_payments):
        pay_id = f"pay_synth_hist_{cust_id[11:]}_{i:02d}"
        amount = random.randint(500, 15000) * 100  # in paisa (₹500 to ₹15000)
        method = random.choice(methods)
        
        # Determine status based on historical success rate
        is_success = random.random() < success_rate
        status = "captured" if is_success else "failed"
        reason = None if is_success else random.choice(failure_reasons[method])
        
        pay_time = start_date + timedelta(days=random.randint(0, max(1, tenure - 1)))
        
        payments.append({
            "id": pay_id,
            "amount": amount,
            "currency": "INR",
            "status": status,
            "method": method,
            "failure_reason": reason,
            "customer_id": cust_id,
            "is_synthetic": True,
            "metadata_json": {"payment_attempt_number": 1},
            "created_at": pay_time
        })

    # Generate the current active failed payment or checkout abandonment
    active_pay_id = f"pay_synth_act_{cust_id[11:]}"
    active_amount = random.randint(1000, 20000) * 100
    active_method = random.choice(methods)
    
    # Check if checkout abandonment
    is_abandonment = force_checkout_abandonment or random.random() < 0.20
    status = "created" if is_abandonment else "failed"
    reason = "checkout_abandoned" if is_abandonment else random.choice(failure_reasons[active_method])
    
    # Expired card can only happen on card payments
    if not is_abandonment and active_method == "card" and random.random() < 0.30:
        reason = "expired_card"

    payments.append({
        "id": active_pay_id,
        "amount": active_amount,
        "currency": "INR",
        "status": status,
        "method": active_method,
        "failure_reason": reason,
        "customer_id": cust_id,
        "is_synthetic": True,
        "metadata_json": {"payment_attempt_number": 1},
        "created_at": datetime.utcnow()
    })

    return payments

# --- 3. Database Seeding Script ---

def seed_synthetic_dataset(
    db: Session,
    num_customers: int = 40,
    seed: int = 42,
    demo_payment_link: bool = False,
) -> Dict[str, int]:
    """
    Clears all existing synthetic customers/payments and seeds a fresh, reproducible batch.
    Returns counts of seeded records.
    """
    logger.info("synthetic_seeding_started", num_customers=num_customers, seed=seed)
    seed_random(seed)

    # 1. Clean up old synthetic records
    # Deleting cases, outcomes, actions, payments, and customers with is_synthetic=True
    # To avoid foreign key constraint issues, we delete in reverse order
    from app.models.models import RevenueRiskCase, RecoveryAction, RecoveryOutcome, AuditLog, AIDecision
    
    db.query(RecoveryOutcome).filter(RecoveryOutcome.case_id.in_(
        db.query(RevenueRiskCase.id).filter(RevenueRiskCase.is_synthetic == True)
    )).delete(synchronize_session=False)
    
    db.query(RecoveryAction).filter(RecoveryAction.case_id.in_(
        db.query(RevenueRiskCase.id).filter(RevenueRiskCase.is_synthetic == True)
    )).delete(synchronize_session=False)

    db.query(AIDecision).filter(AIDecision.case_id.in_(
        db.query(RevenueRiskCase.id).filter(RevenueRiskCase.is_synthetic == True)
    )).delete(synchronize_session=False)
    
    db.query(AuditLog).filter(AuditLog.case_id.in_(
        db.query(RevenueRiskCase.id).filter(RevenueRiskCase.is_synthetic == True)
    )).delete(synchronize_session=False)
    
    db.query(RevenueRiskCase).filter(RevenueRiskCase.is_synthetic == True).delete(synchronize_session=False)
    db.query(Payment).filter(Payment.is_synthetic == True).delete(synchronize_session=False)
    db.query(Customer).filter(Customer.is_synthetic == True).delete(synchronize_session=False)
    db.commit()

    # Ensure a default synthetic merchant exists
    merchant = db.query(Merchant).filter(Merchant.id == "mer_synth_001").first()
    if not merchant:
        merchant = Merchant(id="mer_synth_001", name="Synthetic Merchant Store")
        db.add(merchant)
        db.commit()

    seeded_customers = 0
    seeded_payments = 0

    for i in range(1, num_customers + 1):
        cust_profile = generate_customer_profile(i)
        
        # Pydantic Validation
        validated_customer = SyntheticCustomerSchema(**cust_profile)
        
        db_customer = Customer(
            id=validated_customer.id,
            email=validated_customer.email,
            phone=validated_customer.phone,
            name=validated_customer.name,
            is_synthetic=validated_customer.is_synthetic,
            dataset_type="EVALUATION",
            metadata_json=validated_customer.metadata_json
        )
        db.add(db_customer)
        seeded_customers += 1

        # Generate between 2 and 8 historical payments + 1 active
        num_hist = random.randint(2, 8)
        payment_profiles = generate_payments_for_customer(
            cust_profile,
            num_hist,
            force_checkout_abandonment=demo_payment_link and i == 1,
        )
        
        for pay_profile in payment_profiles:
            # Pydantic Validation
            validated_payment = SyntheticPaymentSchema(**pay_profile)
            
            db_payment = Payment(
                id=validated_payment.id,
                amount=validated_payment.amount,
                currency=validated_payment.currency,
                status=validated_payment.status,
                method=validated_payment.method,
                failure_reason=validated_payment.failure_reason,
                customer_id=validated_payment.customer_id,
                merchant_id=merchant.id,
                is_synthetic=validated_payment.is_synthetic,
                dataset_type=validated_payment.dataset_type,
                metadata_json=validated_payment.metadata_json,
                created_at=validated_payment.created_at
            )
            db.add(db_payment)
            seeded_payments += 1

    db.commit()
    logger.info("synthetic_seeding_complete", customers=seeded_customers, payments=seeded_payments)
    return {"customers": seeded_customers, "payments": seeded_payments}


EVALUATION_PROFILE_TYPES = (
    "B2B Invoice Overdue",
    "Subscription Autopay Failure",
    "Consumer Failed E-commerce Payment",
    "Checkout Abandonment",
    "Bank/Network Failure",
)


def _rupees(value: int) -> int:
    return value * 100


def generate_evaluation_profiles(count: int = 120, seed: int = 42) -> List[dict]:
    """Create deterministic, diverse profiles used by matched AI/baseline cases."""
    if count < 100:
        raise ValueError("The evaluation dataset requires at least 100 profiles.")

    generator = random.Random(seed)
    profiles: List[dict] = []
    per_type = count // len(EVALUATION_PROFILE_TYPES)
    remainder = count % len(EVALUATION_PROFILE_TYPES)

    profile_index = 1
    for type_index, profile_type in enumerate(EVALUATION_PROFILE_TYPES):
        type_count = per_type + (1 if type_index < remainder else 0)
        for _ in range(type_count):
            if profile_type == "B2B Invoice Overdue":
                reason = "payment_terms_overdue"
                amount = generator.randint(50000, 200000)
                method = generator.choice(["netbanking", "bank_transfer"])
                metadata = {
                    "merchant_category": "b2b_services",
                    "payment_route": generator.choice(["hdfc_corporate", "icici_corporate", "razorpay_bank_transfer"]),
                    "historical_success_rate": round(generator.uniform(0.92, 0.99), 2),
                    "customer_tenure_days": generator.randint(365, 1825),
                    "is_subscribed": True,
                    "subscription_status": "active",
                    "checkout_abandoned": False,
                    "previous_recovery_actions": generator.choice([[], ["REMINDER"], ["PAYMENT_LINK"]]),
                    "customer_opted_out": False,
                    "within_recovery_window": True,
                    "recovery_window_days_remaining": generator.randint(7, 30),
                    "profile_type": profile_type,
                }
            elif profile_type == "Subscription Autopay Failure":
                reason = "insufficient_funds"
                amount = generator.randint(500, 3000)
                method = generator.choice(["card", "upi"])
                metadata = {
                    "merchant_category": generator.choice(["saas", "media_subscription", "consumer_subscription"]),
                    "payment_route": generator.choice(["card_recurring", "upi_autopay"]),
                    "historical_success_rate": round(generator.uniform(0.45, 0.90), 2),
                    "customer_tenure_days": generator.randint(45, 540),
                    "is_subscribed": True,
                    "subscription_status": "active",
                    "checkout_abandoned": False,
                    "previous_recovery_actions": generator.choice([[], ["RETRY_LATER"], ["REMINDER"]]),
                    "customer_opted_out": False,
                    "within_recovery_window": True,
                    "recovery_window_days_remaining": generator.randint(2, 7),
                    "profile_type": profile_type,
                }
            elif profile_type == "Consumer Failed E-commerce Payment":
                reason = generator.choice(["expired_card", "incorrect_pin", "insufficient_funds"])
                amount = generator.randint(1000, 10000)
                method = generator.choice(["card", "upi"])
                metadata = {
                    "merchant_category": generator.choice(["ecommerce", "electronics", "fashion"]),
                    "payment_route": generator.choice(["hdfc_card", "axis_card", "upi_intent", "upi_collect"]),
                    "historical_success_rate": round(generator.uniform(0.25, 0.92), 2),
                    "customer_tenure_days": generator.randint(5, 720),
                    "is_subscribed": False,
                    "subscription_status": "none",
                    "checkout_abandoned": False,
                    "previous_recovery_actions": generator.choice([[], ["RETRY_NOW"], ["PAYMENT_LINK"], ["REMINDER"]]),
                    "customer_opted_out": generator.random() < 0.05,
                    "within_recovery_window": True,
                    "recovery_window_days_remaining": generator.randint(1, 7),
                    "profile_type": profile_type,
                }
            elif profile_type == "Checkout Abandonment":
                reason = "checkout_abandoned"
                amount = generator.randint(1000, 15000)
                method = generator.choice(["upi", "wallet"])
                metadata = {
                    "merchant_category": generator.choice(["ecommerce", "travel", "food_delivery"]),
                    "payment_route": generator.choice(["upi_intent", "upi_collect", "paytm_wallet", "phonepe_wallet"]),
                    "historical_success_rate": round(generator.uniform(0.30, 0.78), 2),
                    "customer_tenure_days": generator.randint(1, 240),
                    "is_subscribed": False,
                    "subscription_status": "none",
                    "checkout_abandoned": True,
                    "previous_recovery_actions": generator.choice([[], ["REMINDER"]]),
                    "customer_opted_out": False,
                    "within_recovery_window": True,
                    "recovery_window_days_remaining": generator.randint(1, 2),
                    "profile_type": profile_type,
                }
            else:
                reason = generator.choice(["bank_timeout", "network_failure"])
                amount = generator.randint(1000, 25000)
                method = generator.choice(["card", "upi", "netbanking"])
                metadata = {
                    "merchant_category": generator.choice(["ecommerce", "utilities", "travel", "education"]),
                    "payment_route": generator.choice(["hdfc", "icici", "axis", "razorpay_upi"]),
                    "historical_success_rate": round(generator.uniform(0.55, 0.98), 2),
                    "customer_tenure_days": generator.randint(15, 900),
                    "is_subscribed": generator.random() < 0.35,
                    "subscription_status": generator.choice(["active", "none"]),
                    "checkout_abandoned": False,
                    "previous_recovery_actions": generator.choice([[], ["RETRY_NOW"]]),
                    "customer_opted_out": False,
                    "within_recovery_window": True,
                    "recovery_window_days_remaining": generator.randint(2, 7),
                    "profile_type": profile_type,
                }

            metadata["payment_attempt_number"] = generator.randint(1, 4)
            metadata["evaluation_profile_id"] = f"eval_profile_{profile_index:03d}"
            customer_id = f"cust_eval_{profile_index:04d}"
            profiles.append({
                "profile_id": metadata["evaluation_profile_id"],
                "customer_id": customer_id,
                "payment_id": f"pay_eval_{profile_index:04d}",
                "amount": _rupees(amount),
                "currency": "INR",
                "status": "created" if reason == "checkout_abandoned" else "failed",
                "method": method,
                "failure_reason": reason,
                "customer": {
                    "id": customer_id,
                    "email": f"eval_{profile_index:04d}@example.com",
                    "phone": f"+9198000{profile_index:05d}",
                    "name": f"Evaluation Customer {profile_index:04d}",
                    "is_synthetic": True,
                    "metadata_json": metadata,
                },
                "metadata_json": metadata,
                "dataset_type": "EVALUATION",
                "created_at": datetime(2026, 1, 1) + timedelta(days=profile_index),
            })
            profile_index += 1

    return profiles


def generate_demo_profiles() -> List[dict]:
    """Return the five stable, PAYMENT_LINK-eligible Razorpay Test Mode cases."""
    definitions = [
        ("expired_card", 2499, "card", "ecommerce", 0),
        ("checkout_abandoned", 3999, "upi", "travel", 0),
        ("expired_card", 1499, "card", "subscription", 0),
        ("payment_terms_overdue", 75000, "netbanking", "b2b_services", 1),
        ("checkout_abandoned", 8999, "wallet", "ecommerce", 0),
    ]
    profiles = []
    for index, (reason, amount, method, category, attempt) in enumerate(definitions, start=1):
        customer_id = f"cust_demo_{index:03d}"
        metadata = {
            "profile_type": "DEMO PAYMENT_LINK",
            "merchant_category": category,
            "payment_route": "razorpay_test_mode",
            "historical_success_rate": 0.95 if index in (1, 3) else 0.80,
            "customer_tenure_days": 365 if index == 4 else 120,
            "is_subscribed": index == 3,
            "subscription_status": "active" if index == 3 else "none",
            "checkout_abandoned": reason == "checkout_abandoned",
            "previous_recovery_actions": [],
            "customer_opted_out": False,
            "within_recovery_window": True,
            "recovery_window_days_remaining": 7 if reason != "checkout_abandoned" else 2,
            "payment_attempt_number": attempt + 1,
            "demo_ready": True,
            "expected_action": "PAYMENT_LINK",
        }
        profiles.append({
            "profile_id": f"demo_profile_{index:03d}",
            "customer_id": customer_id,
            "payment_id": f"demo_payment_link_{index:03d}",
            "amount": _rupees(amount),
            "currency": "INR",
            "status": "created" if reason == "checkout_abandoned" else "failed",
            "method": method,
            "failure_reason": reason,
            "customer": {
                "id": customer_id,
                "email": f"demo_{index:03d}@example.com",
                "phone": f"+9197000{index:05d}",
                "name": f"Demo Customer {index:03d}",
                "is_synthetic": True,
                "metadata_json": metadata,
            },
            "metadata_json": metadata,
            "dataset_type": "DEMO",
            "created_at": datetime.utcnow(),
        })
    return profiles


def seed_evaluation_and_demo_dataset(
    db: Session,
    evaluation_count: int = 120,
    seed: int = 42,
    include_demo: bool = True,
) -> Dict[str, int]:
    """Add a reproducible matched evaluation batch and optional live-demo cases.

    This function is intentionally additive: it never deletes existing synthetic,
    baseline, evaluation, or demo records.
    """
    from app.models.models import RevenueRiskCase
    from app.services.risk_engine import RiskEngineService

    profiles = generate_evaluation_profiles(evaluation_count, seed)
    if include_demo:
        profiles.extend(generate_demo_profiles())

    merchant = db.query(Merchant).filter(Merchant.id == "mer_synth_001").first()
    if not merchant:
        merchant = Merchant(id="mer_synth_001", name="Synthetic Merchant Store")
        db.add(merchant)
        db.flush()

    created_customers = created_payments = created_cases = 0
    evaluation_profiles = 0
    demo_profiles = 0
    for profile in profiles:
        customer = db.query(Customer).filter(Customer.id == profile["customer_id"]).first()
        if not customer:
            customer = Customer(**profile["customer"], dataset_type=profile["dataset_type"])
            db.add(customer)
            db.flush()
            created_customers += 1

        payment = db.query(Payment).filter(Payment.id == profile["payment_id"]).first()
        if not payment:
            payment = Payment(
                id=profile["payment_id"], amount=profile["amount"], currency=profile["currency"],
                status=profile["status"], method=profile["method"],
                failure_reason=profile["failure_reason"], customer_id=customer.id,
                merchant_id=merchant.id, is_synthetic=True,
                dataset_type=profile["dataset_type"], metadata_json=profile["metadata_json"],
                created_at=profile["created_at"],
            )
            db.add(payment)
            db.flush()
            created_payments += 1

        if profile["dataset_type"] == "EVALUATION":
            evaluation_profiles += 1
            for strategy in ("BASELINE", "AI"):
                existing = db.query(RevenueRiskCase).filter(
                    RevenueRiskCase.payment_id == payment.id,
                    RevenueRiskCase.recovery_strategy_group == strategy,
                    RevenueRiskCase.dataset_type == "EVALUATION",
                ).first()
                if existing:
                    continue
                case = RiskEngineService.create_or_update_recovery_case(
                    db=db, payment_id=payment.id, event_type=(
                        "CHECKOUT_ABANDONMENT" if profile["failure_reason"] == "checkout_abandoned" else "FAILED_PAYMENT"
                    ), strategy_group=strategy,
                )
                case.dataset_type = "EVALUATION"
                case.current_state = "NEW"
                db.flush()
                created_cases += 1
        else:
            demo_profiles += 1
            existing = db.query(RevenueRiskCase).filter(
                RevenueRiskCase.payment_id == payment.id,
                RevenueRiskCase.dataset_type == "DEMO",
            ).first()
            if not existing:
                case = RiskEngineService.create_or_update_recovery_case(
                    db=db, payment_id=payment.id,
                    event_type=("CHECKOUT_ABANDONMENT" if profile["failure_reason"] == "checkout_abandoned" else "FAILED_PAYMENT"),
                    strategy_group="AI",
                )
                case.dataset_type = "DEMO"
                case.current_state = "NEW"
                db.flush()
                created_cases += 1

    db.commit()
    return {
        "evaluation_profiles": evaluation_profiles,
        "evaluation_cases": evaluation_profiles * 2,
        "demo_profiles": demo_profiles,
        "demo_cases": demo_profiles,
        "customers_created": created_customers,
        "payments_created": created_payments,
        "cases_created": created_cases,
        "total_profiles": evaluation_profiles + demo_profiles,
    }
