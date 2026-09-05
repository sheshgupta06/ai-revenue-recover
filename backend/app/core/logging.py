import logging
import sys
import structlog
from app.core.config import settings

def setup_logging() -> None:
    """
    Configures structured logging for the application.
    Uses JSON formatting in production environments and colorized console logs in development.
    """
    log_level_str = settings.LOG_LEVEL.upper()
    # Map string log level to standard logging constants
    log_level = getattr(logging, log_level_str, logging.INFO)

    # Configure standard library logging base
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Define standard processors for structlog
    shared_processors = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.ENV.lower() == "production":
        # Production: Output raw JSON to stdout
        processors = shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Development: Colorized and formatted terminal output
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Silence verbose default loggers if needed
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)

# Run logging configuration at module level import
setup_logging()
logger = structlog.get_logger("backend")

