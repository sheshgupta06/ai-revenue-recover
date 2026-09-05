from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.config import settings
from app.core.logging import logger

router = APIRouter()

@router.get("/health")
def health_check(response: Response, db: Session = Depends(get_db)) -> dict:
    """
    Liveness and readiness check.
    Verifies that the API backend is running and the PostgreSQL database connection is operational.
    """
    database_status = "healthy"
    error_message = None

    try:
        # Execute simple query to verify database connection liveness
        db.execute(text("SELECT 1"))
    except Exception as e:
        database_status = "unhealthy"
        error_message = str(e)
        logger.error("health_check_db_query_failed", error=error_message)

    if database_status == "unhealthy":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "unhealthy",
            "environment": settings.ENV,
            "components": {
                "database": "unhealthy",
                "api": "healthy"
            },
            "error": error_message
        }

    return {
        "status": "healthy",
        "environment": settings.ENV,
        "components": {
            "database": "healthy",
            "api": "healthy"
        }
    }

