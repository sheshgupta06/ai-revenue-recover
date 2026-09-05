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
    id: str = Field(..., pattern=r"^pay_synth_[a-zA-Z0-9_]+$")
    amount: int = Field(..., gt=0)  # in paisa
    currency: str = "INR"
    status: str
    method: str
    failure_reason: Optional[str] = None
    customer_id: str
    is_synthetic: bool = True
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
                metadata_json=validated_payment.metadata_json,
                created_at=validated_payment.created_at
            )
            db.add(db_payment)
            seeded_payments += 1

    db.commit()
    logger.info("synthetic_seeding_complete", customers=seeded_customers, payments=seeded_payments)
    return {"customers": seeded_customers, "payments": seeded_payments}
