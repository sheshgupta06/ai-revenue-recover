import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Prepend root workspace directory to sys.path so tests can import from backend
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.core.database import Base, get_db
from app.core.config import settings
from app.main import app

@pytest.fixture(scope="session", autouse=True)
def force_mock_llm_provider():
    settings.LLM_PROVIDER = "mock"
# Import models to register them on Base metadata for SQLite test schema generation
from app.models.models import (
    Customer,
    Merchant,
    Payment,
    RevenueRiskCase,
    AIDecision,
    RecoveryAction,
    RecoveryOutcome,
    WebhookEvent,
    AuditLog,
)

from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

# Isolated in-memory SQLite URL for unit testing
TEST_DATABASE_URL = "sqlite:///:memory:"

@pytest.fixture(scope="session")
def test_engine():
    """
    Creates an isolated in-memory SQLite engine and creates all tables.
    Includes event listeners to fix SQLite savepoint autocommit/rollback bugs.
    """
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},  # Needed for SQLite in multithreaded test environments
        poolclass=StaticPool,                       # Shares connection state across all sessions
    )
    
    # Disable pysqlite's automatic BEGIN/COMMIT behavior to allow savepoint rollbacks
    @event.listens_for(engine, "connect")
    def do_connect(dbapi_connection, connection_record):
        dbapi_connection.isolation_level = None

    # Emit explicit BEGIN to ensure transaction block is open
    @event.listens_for(engine, "begin")
    def do_begin(conn):
        conn.exec_driver_sql("BEGIN")

    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def db(test_engine):
    """
    Creates a new database session for a test.
    Ensures complete database state isolation by clearing all tables after each test run.
    """
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        # Truncate all tables to guarantee test state isolation
        connection = test_engine.raw_connection()
        try:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys = OFF;")
            for table in reversed(Base.metadata.sorted_tables):
                cursor.execute(f"DELETE FROM {table.name};")
            cursor.execute("PRAGMA foreign_keys = ON;")
            connection.commit()
            cursor.close()
        finally:
            connection.close()

@pytest.fixture
def client(db):
    """
    FastAPI TestClient fixture that overrides get_db dependency to point to the test session.
    """
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    # Clear overrides after test completion
    app.dependency_overrides.clear()

