"""Application configuration management using Pydantic Settings."""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
SAFE_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
INSECURE_JWT_SECRETS = {
    "dev-secret-key-change-in-production",
    "your-secret-key-change-in-production",
    "your-secret-key-here",
}


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    environment: Literal["development", "staging", "production", "test"] = "development"
    app_env: str = "development"
    debug: bool = False
    api_title: str = "LeadTriage API"
    api_version: str = "0.1.0"
    log_level: str = "INFO"
    request_max_bytes: int = 32768

    # Supabase
    supabase_url: str
    supabase_key: str | None = None
    supabase_service_role_key: str

    # OpenAI
    openai_api_key: str
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = DEFAULT_OPENAI_MODEL
    ai_request_timeout_seconds: int = 20

    # API
    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:8000"]
    queue_metrics_token: str | None = None

    # Deduplication
    dedup_window_days: int = 7
    dedup_strategy: Literal["email", "phone", "both"] = "email"

    # Rate Limiting
    rate_limit_per_minute: int = 60
    rate_limit_per_hour: int = 1000

    # JWT
    jwt_secret: str = "dev-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24

    @field_validator(
        "supabase_url",
        "supabase_service_role_key",
        "openai_api_key",
        "openai_base_url",
        "openai_model",
        "jwt_secret",
    )
    @classmethod
    def required_strings_must_not_be_blank(cls, value: str) -> str:
        """Reject blank strings for required runtime settings."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("required setting must not be blank")
        return cleaned

    @field_validator("log_level")
    @classmethod
    def log_level_must_be_supported(cls, value: str) -> str:
        """Normalize and validate log level names."""
        cleaned = value.strip().upper()
        if cleaned not in SAFE_LOG_LEVELS:
            raise ValueError("LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")
        return cleaned

    @field_validator("queue_metrics_token")
    @classmethod
    def optional_token_must_not_be_blank(cls, value: str | None) -> str | None:
        """Treat blank optional monitoring tokens as absent outside production."""
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        return cleaned

    @model_validator(mode="after")
    def production_settings_must_be_safe(self) -> "Settings":
        """Fail fast on unsafe production configuration."""
        if self.environment != "production":
            return self

        if self.debug:
            raise ValueError("DEBUG must be false in production")

        if self.jwt_secret in INSECURE_JWT_SECRETS or len(self.jwt_secret) < 32:
            raise ValueError(
                "JWT_SECRET must be a generated secret with at least 32 characters "
                "in production"
            )

        if self.queue_metrics_token is None or len(self.queue_metrics_token) < 24:
            raise ValueError(
                "QUEUE_METRICS_TOKEN must be configured with at least 24 characters "
                "in production"
            )

        if not self.allowed_origins:
            raise ValueError("ALLOWED_ORIGINS must not be empty in production")

        insecure_origins = {"*", "http://localhost:3000", "http://localhost:8000"}
        for origin in self.allowed_origins:
            normalized_origin = origin.strip().rstrip("/")
            if (
                normalized_origin in insecure_origins
                or normalized_origin.startswith("http://127.0.0.1")
                or normalized_origin.startswith("http://localhost")
            ):
                raise ValueError(
                    "ALLOWED_ORIGINS must use explicit production origins in production"
                )

        return self


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
