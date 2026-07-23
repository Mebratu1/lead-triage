"""Application configuration management using Pydantic Settings."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    supabase_key: str
    supabase_service_role_key: str

    # OpenAI
    openai_api_key: str
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4-turbo-preview"
    ai_request_timeout_seconds: int = 20

    # API
    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:8000"]

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


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
