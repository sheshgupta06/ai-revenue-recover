from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from app.core.config import settings
from app.core.database import get_db
from app.main import app

def test_health_check_healthy(client: TestClient) -> None:
    """
    Verifies that GET /health returns 200 OK and "healthy" status under normal test db conditions.
    """
    response = client.get("/health")
    assert response.status_code == 200
    
    payload = response.json()
    assert payload["status"] == "healthy"
    assert payload["environment"] == settings.ENV
    assert payload["components"]["database"] == "healthy"
    assert payload["components"]["api"] == "healthy"

def test_health_check_unhealthy(client: TestClient) -> None:
    """
    Verifies that GET /health returns 503 Service Unavailable and "unhealthy" status when database queries fail.
    """
    # Create mock session that raises an exception on any database call
    mock_session = MagicMock()
    mock_session.execute.side_effect = Exception("Simulated PostgreSQL connection failure")

    # Override get_db explicitly for this test case
    app.dependency_overrides[get_db] = lambda: mock_session

    try:
        response = client.get("/health")
        assert response.status_code == 503
        
        payload = response.json()
        assert payload["status"] == "unhealthy"
        assert payload["components"]["database"] == "unhealthy"
        assert payload["components"]["api"] == "healthy"
        assert "Simulated PostgreSQL connection failure" in payload["error"]
    finally:
        # Clean up dependency override
        app.dependency_overrides.clear()

def test_settings_loading() -> None:
    """
    Verifies that settings load configuration parameters correctly with expected fallbacks.
    """
    assert settings.ENV is not None
    assert settings.LOG_LEVEL is not None
    assert settings.DATABASE_URL is not None

