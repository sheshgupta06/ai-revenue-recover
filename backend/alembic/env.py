import sys
from logging.config import fileConfig
from pathlib import Path
from sqlalchemy import pool, create_engine
from alembic import context

# Prepend the root workspace directory to sys.path so backend imports work seamlessly
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.core.config import settings
from app.core.database import Base
# Import all models to ensure they are registered on the Base metadata for autogenerate detection
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

# Access values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set the metadata of the declarative base for migration detection
target_metadata = Base.metadata

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.
    Configures the context with the dynamic DATABASE_URL from Settings.
    """
    url = settings.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Run migrations in 'online' mode.
    Creates an engine from the dynamic DATABASE_URL from Settings.
    """
    connectable = create_engine(
        settings.DATABASE_URL,
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()


