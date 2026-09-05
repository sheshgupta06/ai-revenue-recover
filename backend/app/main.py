from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from app.api.router import router as api_router
from app.api.webhooks import router as webhook_router
from fastapi.staticfiles import StaticFiles
from app.api.cases import router as cases_router
from app.api.evaluation import router as evaluation_router
from app.api.dashboard_endpoints import router as dashboard_router
from app.core.config import settings
from app.core.logging import logger

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages application startup and shutdown lifecycles.
    """
    logger.info(
        "backend_application_started",
        env=settings.ENV,
        log_level=settings.LOG_LEVEL,
        title=app.title
    )
    yield
    logger.info("backend_application_stopped")

# Initialize the FastAPI application
app = FastAPI(
    title="AI Revenue Recovery Orchestrator",
    description="Intelligent Closed-Loop Revenue Recovery System built for Razorpay AI Builder Internship",
    version="1.0.0",
    lifespan=lifespan
)

# Include main routers
app.include_router(api_router)
app.include_router(webhook_router, prefix="/api/v1")
app.include_router(cases_router, prefix="/api/v1")
app.include_router(evaluation_router, prefix="/api/v1")
app.include_router(dashboard_router, prefix="/api/v1")

# Mount Static Dashboard SPA
# Frontend files live in the standalone frontend/ directory at project root.
# Served at /dashboard/* — URLs in HTML/JS remain unchanged.
frontend_path = Path(__file__).parent.parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/dashboard", StaticFiles(directory=str(frontend_path), html=True), name="static")
else:
    logger.warning("frontend_directory_not_found", path=str(frontend_path))
