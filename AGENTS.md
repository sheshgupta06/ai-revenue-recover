# AGENTS.md — Development Rules

## Before Every Task

Before making any code or architecture change, read:

1. `brain.md`
2. `IMPLEMENTATION_PLAN.md`
3. `ARCHITECTURE.md`

Follow the current development phase only.

Do not jump to later phases without confirmation.

---

## Working Style

* Inspect existing files before modifying them.
* Make small, testable changes.
* Do not rewrite working code unnecessarily.
* Keep frontend, backend, data, scripts, and documentation responsibilities separated.
* Run relevant tests after every meaningful implementation.
* Report what was changed and what was tested.
* Preserve existing working functionality.

---

## AI Development Rules

AI must be used only where contextual reasoning provides value.

AI may handle:

* Context interpretation.
* Root-cause reasoning.
* Recovery strategy selection.
* Revenue-risk prioritization.
* Next-best-action reasoning.
* Human-readable explanations.

Do NOT use an LLM for deterministic operations such as:

* Payment status verification.
* Amount calculations.
* Retry limits.
* Authentication.
* Authorization.
* Webhook verification.
* Idempotency.
* Safety rules.
* Database writes.

---

## Structured AI Output

AI decisions must use a structured schema.

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

The backend must validate AI output before any action is executed.

Never execute arbitrary text returned by an LLM.

---

## Security Rules

NEVER:

* expose API keys
* expose passwords
* expose authentication tokens
* commit `.env`
* put secrets inside source code
* use real customer/payment data
* upload private credentials to GitHub

Use environment variables.

Use `.env.example` only for variable names and safe placeholders.

Use Razorpay Test Mode during development.

---

## Razorpay Rules

* Never invent Razorpay APIs.
* Verify the actual API/documentation before implementation.
* Use Test Mode for development.
* Verify webhook signatures.
* Make webhook processing idempotent.
* Handle API failures and timeouts.
* Never use real money during development.
* Do not claim a Razorpay capability without verification.

---

## Safety / Policy Rules

The AI does not have unlimited authority.

The backend policy layer must enforce:

* Maximum recovery attempts.
* Maximum recovery window.
* Customer STOP/opt-out.
* Successful payment → STOP.
* High-value/risky case → human approval when configured.
* Invalid AI action → reject.
* AI unavailable → deterministic fallback.
* Repeated ineffective action → stop or escalate.

The AI cannot bypass these policies.

---

## Auditability

Every important AI decision should record:

* Case ID.
* Recommended action.
* Reason.
* Confidence.
* Expected recovery probability.
* Expected recovered amount.
* Actual action.
* Outcome.
* Timestamp.
* Policy result.

The system must be able to explain why an action happened.

---

## Data Integrity

Never fabricate:

* Revenue recovered.
* Recovery rate.
* AI accuracy.
* Incremental recovery.
* Customer behavior.
* Payment outcomes.

Synthetic data and simulated experiments must be clearly identified.

Metrics must be reproducible.

---

## Failure Handling

The system must gracefully handle:

* Razorpay API failure.
* API timeout.
* Network failure.
* Duplicate webhook.
* Out-of-order webhook.
* Already recovered payment.
* Invalid AI output.
* AI provider outage.
* Database failure.
* Retry exhaustion.
* Customer opt-out.
* Recovery action failure.

Every important failure should have a safe fallback or terminal state.

---

## Do Not Build

Do NOT turn the project into:

* A generic chatbot.
* A simple payment retry script.
* A UI-only demo.
* A fake AI dashboard.
* A clone of existing Razorpay recovery functionality.
* An unnecessarily complex multi-agent system.

Complexity must have a clear engineering reason.

---

## Antigravity Execution Rules

Before starting implementation:

1. Read the project documentation.
2. Identify the current phase.
3. State the intended changes.
4. Implement only the requested phase/task.
5. Run tests.
6. Verify the result.
7. Report failures clearly.

Do not:

* start long-running servers unnecessarily
* install random dependencies
* delete files without reason
* change architecture silently
* bypass safety checks
* fabricate successful results

---

## Definition of Done

A feature is complete only when:

* It works.
* It has been tested.
* Failure behavior is considered.
* Security is maintained.
* AI output is validated where applicable.
* Relevant logs/audit information exists.
* Documentation is updated when necessary.
* Existing functionality still works.
