# AI Revenue Recovery Orchestrator

An AI-assisted revenue recovery system for the Razorpay AI Builder Internship 2026, Track 3. It identifies revenue at risk, recommends a bounded recovery action, applies deterministic safety policies, verifies payment outcomes, and records an audit trail.

## Why It Matters

This is not a generic payment retry bot. The system makes a revenue-level decision:

> Which intervention should be used, when should it happen, and when should recovery stop?

It compares AI-assisted decisions with a deterministic baseline so recovery impact can be evaluated using reproducible data.

## Workflow

```text
Payment or revenue event
        -> Risk detection
        -> Context building
        -> Structured AI decision
        -> Policy and safety validation
        -> Controlled recovery action
        -> Outcome verification
        -> Metrics and audit trail
```

## Key Features

- FastAPI backend with PostgreSQL persistence and Alembic migrations.
- Synthetic revenue-at-risk case generation for repeatable evaluation.
- Risk scoring, case prioritization, baseline strategy, and AI decision engine.
- Structured AI output validation with deterministic fallback when AI is unavailable.
- Policy enforcement for retry limits, recovery windows, opt-out, escalation, and terminal STOP states.
- Razorpay Test Mode integration with webhook signature verification and idempotent processing.
- Static dashboard for revenue-at-risk, recovery, evaluation, cases, and audit information.
- Test coverage for AI decisions, policy execution, webhooks, reliability, risk scoring, and evaluation.

## AI and Safety Boundary

AI is used for contextual reasoning, root-cause interpretation, prioritization, strategy selection, and explanations. Deterministic backend code remains authoritative for authentication, payment verification, amount calculations, retry limits, webhook verification, policy enforcement, database writes, and audit logging.

The project uses synthetic data and Razorpay Test Mode only. Never use real customer data or real-money credentials during development.

## Technology

- Backend: Python, FastAPI, SQLAlchemy, Alembic
- Database: PostgreSQL
- Payments: Razorpay Test Mode
- AI: Structured LLM provider integration with Gemini-compatible configuration
- Frontend: Static HTML, CSS, and JavaScript dashboard
- Tests: pytest

## Project Layout

```text
backend/
  app/                 FastAPI application, services, models, and APIs
  alembic/             Database migration configuration
  tests/               Backend test suite
frontend/
  index.html           Dashboard page
  css/styles.css       Dashboard styles
  js/app.js            Dashboard behavior and API calls
run.ps1                Starts backend and frontend locally on Windows
```

## Demo Dataset

The repository also includes `payment_link_demo_dataset.csv` as a supplemental, reproducible demo-data reference. The canonical live Razorpay Test Mode fixtures remain the five database cases `demo_payment_link_001` through `demo_payment_link_005`; importing the CSV does not replace evaluation data or create duplicate live-demo cases.

## Local Setup

### 1. Configure the environment

Create a root `.env` file. It is ignored by Git and must not be committed.

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/revenue_recovery
ENV=development
LOG_LEVEL=INFO

# Optional for AI and Razorpay Test Mode
GEMINI_API_KEY=your_test_key
RAZORPAY_KEY_ID=your_test_key_id
RAZORPAY_KEY_SECRET=your_test_key_secret
RAZORPAY_WEBHOOK_SECRET=your_test_webhook_secret
```

### 2. Install dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
```

Make sure PostgreSQL is running and the database in `DATABASE_URL` exists.

### 3. Start the application

From the repository root:

```powershell
.\run.ps1
```

The script starts:

- Dashboard: http://localhost:3000
- Backend API: http://localhost:8000
- Swagger docs: http://localhost:8000/docs

To start services manually:

```powershell
cd backend
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

In a second terminal:

```powershell
cd frontend
python -m http.server 3000
```

## Run Tests

The test suite uses an in-memory SQLite database through the test configuration and does not require production credentials.

```powershell
cd backend
pytest -q
```

## Security Notes

- Use Razorpay Test Mode only.
- Keep API keys, passwords, tokens, and `.env` files out of Git.
- Do not use real customer or payment data.
- Never execute unvalidated text returned by an LLM.
- All AI recommendations must pass backend policy validation before an action is executed.

## Project Status

The repository contains the working backend, static dashboard, migrations, synthetic evaluation flow, Razorpay integration, safety policies, and reliability tests. Metrics shown by the application should be treated as synthetic or test-mode results unless explicitly backed by a reproducible experiment.
