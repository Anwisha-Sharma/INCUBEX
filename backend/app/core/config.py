from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    APP_NAME: str = "RescueMesh Core API"
    APP_VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = False

    # Server & security
    SECRET_KEY: str = "insecure-default-change-in-production"
    ALLOWED_ORIGINS: list[str] = Field(
        default=["*"],
        description="Allowed CORS origins for local PWA and network hub"
    )
    RATE_LIMIT_DEFAULT: str = "100/minute"

    # Offline and peer sync parameters
    MAX_EVENT_PAYLOAD_BYTES: int = 65536  # 64 KB cap on single event payload
    MAX_CLOCK_SKEW_MS: int = 7200000  # Max acceptable clock skew: 2 hours
    # Database (PostgreSQL 17 in staging/prod, SQLite async fallback for standalone offline development)
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./rescuemesh.db",
        description="SQLAlchemy async connection URL (PostgreSQL + psycopg or SQLite + aiosqlite)",
    )

    # Optional AI Grounding (Module D)
    GROQ_API_KEY: str | None = Field(default=None, description="Optional Groq API key")
    GROQ_MODEL: str = Field(default="openai/gpt-oss-120b", description="Groq model ID")
    ANTHROPIC_API_KEY: str | None = Field(default=None, description="Optional Anthropic API key")

settings = Settings()
