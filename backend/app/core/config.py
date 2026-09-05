import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator

# Dynamic resolution of workspace root directory to load the root .env file
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
ENV_FILE_PATH = BASE_DIR / ".env"

class Settings(BaseSettings):
    ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    DATABASE_URL: str = Field(...)
    
    # Razorpay Credentials (required in future integration phases)
    RAZORPAY_KEY_ID: str | None = None
    RAZORPAY_KEY_SECRET: str | None = None
    RAZORPAY_WEBHOOK_SECRET: str | None = None
    
    # AI API Credentials (required in future reasoning phases)
    AI_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None
    
    # Phase 5 AI Decision Engine Config
    LLM_PROVIDER: str = "gemini"
    LLM_MODEL_NAME: str = "gemini-3.5-flash"
    LLM_API_URL: str = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    LLM_TIMEOUT_SECONDS: float = 5.0

    @property
    def effective_ai_key(self) -> str | None:
        key = self.AI_API_KEY
        if not key or "placeholder" in key:
            key = self.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY")
        return key

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE_PATH),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            raise ValueError("DATABASE_URL must be a non-empty string.")
        
        # Strip potential whitespace
        cleaned = v.strip()
        
        # Accept postgresql://, postgresql+psycopg2://, or postgres://
        if cleaned.startswith("postgres://"):
            # Rewrite postgres:// to postgresql:// for SQLAlchemy compatibility
            cleaned = cleaned.replace("postgres://", "postgresql://", 1)
        
        if not (cleaned.startswith("postgresql://") or cleaned.startswith("postgresql+psycopg2://")):
            raise ValueError(
                "DATABASE_URL must be a valid PostgreSQL connection string starting with "
                "'postgresql://', 'postgresql+psycopg2://', or 'postgres://'."
            )
        return cleaned

# Singleton settings instance
try:
    settings = Settings()
except Exception as e:
    # Explicit logging or custom message print for configuration failure
    print(f"Configuration Error: Failed to initialize settings. {e}")
    raise
