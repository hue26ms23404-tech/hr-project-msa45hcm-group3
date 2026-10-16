from typing import List
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Load dotenv files independently of the process working directory.
        # backend/.env overrides repo-root .env; deployed environment wins both.
        env_file=(
            Path(__file__).resolve().parents[3] / ".env",
            Path(__file__).resolve().parents[2] / ".env",
        ),
        case_sensitive=True,
        extra="allow"
    )

    PROJECT_NAME: str = "HR Management with AI"
    ENVIRONMENT: str = "development"
    API_V1_PREFIX: str = "/api/v1"

    # PostgreSQL config
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "hr_user"
    POSTGRES_PASSWORD: str = "hr_secret_password"
    POSTGRES_DB: str = "hr_management_db"

    # Database URLs
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://hr_user:hr_secret_password@localhost:5432/hr_management_db"
    )
    SYNC_DATABASE_URL: str = Field(
        default="postgresql+psycopg2://hr_user:hr_secret_password@localhost:5432/hr_management_db"
    )

    # JWT Security
    SECRET_KEY: str = "super_secret_jwt_key_should_be_changed_in_production_env_32_bytes_min"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8 hours
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Kiosk Web authorization token
    KIOSK_API_SECRET_KEY: str = "kiosk_secret_device_authorization_token_hr_group3"

    # Gemini is opt-in; configuring a key alone never enables paid/quota calls.
    GEMINI_ENABLED: bool = False
    # Each feature below sends user text to Google and is opt-in separately.
    GEMINI_SEMANTIC_ENABLED: bool = False
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL_NAME: str = "gemini-3.6-flash"
    GEMINI_INTENT_TIMEOUT_SECONDS: float = Field(default=15, ge=5, le=30)
    GEMINI_POLICY_TIMEOUT_SECONDS: float = Field(default=15, ge=5, le=30)
    # Small authorized policy corpora are read whole; 0 keeps lexical RAG only.
    # Character budget, not a token count. Oversized corpora use existing RAG.
    AI_POLICY_FULL_CONTEXT_MAX_CHARS: int = Field(default=120000, ge=0, le=200000)
    GEMINI_PUBLIC_SEARCH_ENABLED: bool = False
    # Upper bound kept at 45 so existing deployments still start; the shared
    # chat budget (budgeted_timeout) caps the effective value per request.
    GEMINI_PUBLIC_SEARCH_TIMEOUT_SECONDS: float = Field(default=10, ge=5, le=45)
    # Total wall-clock budget for one chat request, shared by all provider calls.
    AI_CHAT_TIMEOUT_SECONDS: float = Field(default=30, ge=10, le=60)
    AI_KNOWLEDGE_STORAGE_DIR: Path = Path(__file__).resolve().parents[2] / "storage" / "private" / "ai-knowledge"
    AI_KNOWLEDGE_MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024
    REPORT_STORAGE_DIR: Path = Path(__file__).resolve().parents[2] / "storage" / "private" / "reports"

    # Google Login
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"]


settings = Settings()
