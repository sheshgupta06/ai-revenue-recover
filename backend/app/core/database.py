from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.core.config import settings
from app.core.logging import logger

# SQLAlchemy Declarative Base class
Base = declarative_base()

try:
    # Initialize engine with pool connection checking
    engine = create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,  # Verifies liveness of connections retrieved from pool
        pool_recycle=1800,   # Recycle connections every 30 minutes
    )
    
    # Session factory for handling requests
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    logger.info("database_engine_initialized", url=settings.DATABASE_URL.split("@")[-1])  # Logs only safe host details
except Exception as e:
    logger.error("database_engine_initialization_failed", error=str(e))
    raise

def get_db() -> Generator:
    """
    FastAPI dependency that yields a database session and ensures it is closed after use.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

