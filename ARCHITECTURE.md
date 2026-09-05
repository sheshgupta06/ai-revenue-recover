# ARCHITECTURE.md — AI Revenue Recovery Orchestrator

## 1. System Overview

The AI Revenue Recovery Orchestrator is a closed-loop system that detects revenue at risk, analyzes the context, selects a permitted recovery strategy, executes the action, verifies the outcome, and measures recovered revenue.

The architecture must prioritize:

* Reliability.
* Security.
* Explainability.
* Measurable recovery.
* Bounded AI autonomy.
* Failure recovery.

---

# 2. High-Level Architecture

```text
                         RAZORPAY TEST MODE
                                │
                                │
                    Payment / Revenue Events
                                │
                                ▼
                       ┌─────────────────┐
                       │  FastAPI API    │
                       │    Backend      │
                       └────────┬────────┘
                                │
              ┌─────────────────┼──────────────────┐
              │                 │                  │
              ▼                 ▼                  ▼
       Webhook Handler     Revenue Risk       Database
                            Engine
              │                 │
              └────────┬────────┘
                       │
                       ▼
                Context Builder
                       │
                       ▼
               AI Decision Engine
                       │
                       ▼
              Policy / Safety Engine
                       │
              ┌────────┴─────────┐
              │                  │
              ▼                  ▼
       Action Executor       Human Review
              │
              ▼
      Controlled Recovery Action
              │
              ▼
       Razorpay Test APIs
              │
              ▼
       Payment / Event Update
              │
              ▼
      Outcome Verification
              │
        ┌─────┴─────┐
        │           │
        ▼           ▼
    Recovered    Not Recovered
        │           │
        ▼           ▼
       STOP    Next Action / STOP
        │           │
        └─────┬─────┘
              ▼
       Metrics + Audit Log
              │
              ▼
          Dashboard
```

---

# 3. Main Components

## 3.1 Frontend

The frontend provides the visual interface for merchants/judges.

### Responsibilities

* Revenue-at-risk overview.
* Revenue recovered.
* Recovery rate.
* Incremental recovery.
* Active recovery cases.
* Case details.
* AI decisions.
* AI explanations.
* Recovery timeline.
* Audit logs.
* Baseline vs AI comparison.

The frontend must not contain secret credentials or business-critical safety logic.

---

# 3.2 FastAPI Backend

The backend is the central application layer.

### Responsibilities

* REST APIs.
* Business logic.
* Orchestration.
* Authentication/authorization where required.
* Database access.
* AI service integration.
* Razorpay integration.
* Webhook processing.
* Policy enforcement.
* Recovery workflows.
* Metrics calculation.
* Audit logging.

The backend is the final authority for safety and action execution.

---

# 4. Revenue Risk Engine

The Revenue Risk Engine identifies cases where money may be lost.

### Possible revenue-risk events

* Failed payment.
* Checkout abandonment.
* Subscription payment failure.
* Overdue invoice.
* Repeated payment failure.
* Payment route degradation.
* Other configured revenue-risk events.

### Output

The engine creates or updates a recovery case containing:

* Case ID.
* Customer ID.
* Merchant ID.
* Amount.
* Event type.
* Failure reason.
* Context.
* Risk level.
* Current state.

---

# 5. Context Builder

The Context Builder gathers relevant information before AI reasoning.

### Possible inputs

* Transaction amount.
* Payment method.
* Payment route/bank.
* Failure reason.
* Timestamp.
* Customer history.
* Previous payment success rate.
* Previous recovery attempts.
* Subscription status.
* Checkout status.
* Merchant context.
* Previous actions.
* Current case state.

The Context Builder must avoid unnecessary personal data.

---

# 6. AI Decision Engine

The AI Decision Engine performs reasoning-heavy tasks.

### Responsibilities

1. Interpret the case.
2. Understand likely root cause.
3. Estimate recovery opportunity.
4. Select the best permitted recovery action.
5. Explain the decision.
6. Recommend a next action when appropriate.

---

## Possible Actions

```text
RETRY_NOW
RETRY_LATER
ALTERNATE_PAYMENT
PAYMENT_LINK
REMINDER
HUMAN_ESCALATION
STOP
```

The actual available actions must depend on supported APIs and implemented workflows.

---

# 7. AI Output Contract

The AI must return structured data.

Example:

```json
{
  "action": "RETRY_LATER",
  "delay_minutes": 360,
  "confidence": 0.84,
  "reason": "Temporary bank timeout with strong payment history",
  "expected_recovery_probability": 0.64,
  "expected_recovered_amount": 3200
}
```

The backend must validate every field.

### Validation must check:

* Action is allowed.
* Confidence is within valid range.
* Probability is within valid range.
* Amount is valid.
* Delay is within permitted limits.
* Required fields exist.

Invalid output must be rejected.

---

# 8. Policy / Safety Engine

The Policy Engine sits between AI decisions and action execution.

```text
AI Decision
     │
     ▼
Policy Validation
     │
 ┌───┴────┐
 │        │
Valid    Invalid
 │        │
 ▼        ▼
Execute  Fallback
```

## Policy examples

### Successful payment

```text
Payment recovered
→ STOP
```

### Customer opt-out

```text
Customer says STOP
→ STOP immediately
```

### Retry limit

```text
Maximum attempts reached
→ STOP or ESCALATE
```

### High-value transaction

```text
High-value/risky case
→ HUMAN REVIEW
```

### AI unavailable

```text
AI unavailable
→ Deterministic fallback
```

AI must never bypass this layer.

---

# 9. Action Executor

The Action Executor performs approved recovery actions.

Possible actions include:

* Schedule retry.
* Execute permitted retry.
* Generate/use supported payment recovery flow.
* Trigger reminder workflow.
* Create human-review case.

The executor must never execute arbitrary LLM output.

---

# 10. Outcome Verification

After an action, the system observes the resulting payment/event state.

### Example

```text
Recovery Action
      │
      ▼
Wait / Observe
      │
      ▼
Payment Status
      │
 ┌────┴────┐
 │         │
Success   Failure
 │         │
 ▼         ▼
STOP    Next Action
```

The system must verify recovery using trusted payment/status information rather than assuming an action succeeded.

---

# 11. State Machine

A recovery case can move through these states:

```text
NEW
 │
 ▼
ANALYZING
 │
 ▼
ACTION_PROPOSED
 │
 ▼
POLICY_CHECK
 │
 ├───────────────┐
 │               │
 ▼               ▼
APPROVED       REJECTED
 │               │
 ▼               ▼
ACTION_        FALLBACK/
SCHEDULED      STOP
 │
 ▼
ACTION_EXECUTED
 │
 ▼
WAITING_FOR_OUTCOME
 │
 ├───────────────┐
 │               │
 ▼               ▼
RECOVERED     NOT_RECOVERED
 │               │
 ▼               ▼
STOP        NEXT_ACTION
                │
                ▼
          POLICY_CHECK
```

Terminal states:

```text
RECOVERED
STOPPED
ESCALATED
EXPIRED
```

---

# 12. Database Architecture

The database should store the complete recovery lifecycle.

### Core entities

```text
customers
merchants
payments
revenue_risk_cases
ai_decisions
recovery_actions
recovery_outcomes
webhook_events
audit_logs
```

---

## Important Relationships

```text
Customer
   │
   └── Payments
          │
          └── Revenue Risk Case
                    │
                    ├── AI Decisions
                    │
                    ├── Recovery Actions
                    │
                    ├── Recovery Outcomes
                    │
                    └── Audit Logs
```

---

# 13. Webhook Architecture

Webhook processing must be reliable.

```text
Razorpay
   │
   ▼
Webhook Endpoint
   │
   ▼
Signature Verification
   │
   ▼
Idempotency Check
   │
   ▼
Persist Event
   │
   ▼
Process Event
   │
   ▼
Update Recovery Case
```

### Requirements

* Verify signature.
* Detect duplicate events.
* Safely handle repeated delivery.
* Handle out-of-order events.
* Persist raw/structured event information appropriate for debugging.
* Never trust an unverified event.

---

# 14. AI vs Deterministic Architecture

## AI Layer

Use AI for:

```text
Context understanding
Root-cause reasoning
Recovery strategy
Prioritization
Next-best action
Explanation
```

## Deterministic Layer

Use normal backend logic for:

```text
Payment verification
Webhook verification
Idempotency
Amount calculations
Safety rules
Retry limits
STOP conditions
Authorization
Database writes
Audit logging
```

This boundary is intentional.

---

# 15. Baseline vs AI Evaluation

The system must support two strategies.

```text
                 Same Case Batch
                       │
              ┌────────┴────────┐
              │                 │
              ▼                 ▼
          Baseline          AI Strategy
              │                 │
              ▼                 ▼
       Recovered Money    Recovered Money
              │                 │
              └────────┬────────┘
                       ▼
                 Compare Results
```

### Metrics

* Revenue at Risk.
* Baseline Revenue Recovered.
* AI Revenue Recovered.
* Incremental Revenue Recovered.
* Recovery Rate.
* Average Recovery Time.
* Intervention Count.
* Unnecessary Interventions.
* Escalation Rate.
* Stop Rate.

No fabricated metrics.

---

# 16. Failure Recovery Architecture

Every external dependency can fail.

### AI failure

```text
AI unavailable
→ deterministic fallback
→ log failure
→ continue safely
```

### Razorpay API failure

```text
API failure
→ bounded retry
→ timeout
→ fallback/escalation
→ audit log
```

### Duplicate webhook

```text
Duplicate event
→ idempotency check
→ do not duplicate business action
```

### Invalid AI response

```text
Invalid response
→ reject
→ fallback
→ log
```

### Already recovered payment

```text
Payment already recovered
→ do not retry
→ mark RECOVERED
→ STOP
```

---

# 17. Security Architecture

Secrets must remain outside source code.

Use environment variables:

```text
RAZORPAY_KEY_ID
RAZORPAY_KEY_SECRET
AI_API_KEY
DATABASE_URL
```

Never commit actual values.

Use `.env.example` only as a template.

---

# 18. Deployment Architecture

Initial target:

```text
Browser
   │
   ▼
Frontend
   │
   ▼
Backend API
   │
 ┌─┴───────────────┐
 ▼                 ▼
PostgreSQL      AI Provider
   │
   ▼
Razorpay Test APIs
```

The exact hosting providers will be selected after testing and should not be hard-coded into the architecture until deployment begins.

---

# 19. Architecture Principle

The architecture should remain as simple as possible while satisfying the project requirements.

Do not create multiple autonomous agents merely for appearance.

The system should demonstrate:

> **Detect → Reason → Decide → Validate → Act → Verify → Learn/Next Action → Stop**

with measurable business outcomes and safe engineering boundaries.
