# BRAIN.md — AI Revenue Recovery Orchestrator

## Project

* Hackathon: Razorpay AI Builder Internship 2026
* Track: Track 3 — AI Revenue Recovery
* Working Title: AI Revenue Recovery Orchestrator

---

## Core Problem

Revenue can be lost through failed payments, checkout abandonment, subscription failures, overdue receivables, and other recovery opportunities.

The system should identify revenue at risk and recover it intelligently.

---

## Core Solution

Build a closed-loop AI system that:

1. Detects revenue at risk.
2. Understands the case context and likely cause.
3. Selects the best permitted recovery intervention.
4. Executes a bounded recovery workflow.
5. Observes the outcome.
6. Verifies whether revenue was recovered.
7. Chooses a next action or stops.
8. Measures incremental recovery against a baseline.

---

## Differentiation

Do NOT build a generic failed-payment retry bot.

Razorpay already has capabilities around intelligent retries, subscription recovery, UPI Autopay/recovery, revenue protection, and agentic payment workflows.

Our differentiation is **revenue-level decision making**:

> Which intervention should be used, when should it happen, when should we stop, and which strategy maximizes measurable incremental recovered revenue with bounded customer friction and risk?

---

## AI Judgment

Use AI only where contextual reasoning adds value.

### AI handles:

* Root-cause/context interpretation.
* Recovery strategy selection.
* Revenue-risk prioritization.
* Next-best-action reasoning.
* Decision explanation.

### Deterministic code handles:

* Authentication and authorization.
* Payment status verification.
* Amount calculations.
* Retry limits.
* Customer STOP/opt-out.
* Webhook verification.
* Webhook idempotency.
* Safety policies.
* Database writes.
* Audit logging.

---

## Safety

The AI must never have unlimited authority.

Mandatory controls:

* Maximum recovery attempts.
* Maximum recovery window.
* Stop after successful recovery.
* Stop after customer opt-out.
* Human approval for configured high-value/risky cases.
* Safe fallback when AI is unavailable.
* Reject invalid AI output.
* Maintain an audit trail.

---

## Data Strategy

Never use real customer/payment data.

Use two sources:

### 1. Synthetic Dataset

Generate realistic revenue-at-risk cases for controlled experiments.

### 2. Razorpay Test Mode

Use Razorpay Test Mode to demonstrate actual payment workflows, events, and webhooks.

Synthetic data is for evaluation.

Razorpay Test Mode is for integration and workflow demonstration.

Never fabricate metrics.

Every reported metric must come from an actual experiment or be explicitly labelled as simulated.

---

## Success Metrics

Track:

* Revenue At Risk.
* Revenue Recovered.
* Recovery Rate.
* Baseline Recovery.
* AI Recovery.
* Incremental Recovery.
* Average Time to Recovery.
* Intervention Count.
* Unnecessary Interventions.
* Escalation Rate.
* Stop Rate.

---

## Core Principle

> Working software + measurable recovery + reliable engineering > unnecessary features.

Build a small reliable system first.

Then add intelligence.

Then add controlled autonomy.

Then add polish.

---

## Project Direction

The final project should demonstrate:

**Revenue at Risk**

↓

**AI Analysis**

↓

**Best Recovery Decision**

↓

**Safety / Policy Check**

↓

**Controlled Recovery Action**

↓

**Outcome Verification**

↓

**Next Action or STOP**

↓

**Measured Revenue Recovered**

↓

**Audit Trail**

The goal is not to make the most complicated AI system.

The goal is to build a trustworthy system that can demonstrate measurable incremental revenue recovery.
