"""Application configuration management using Pydantic Settings."""

from functools import lru_cache
from ipaddress import ip_network
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
SAFE_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
INSECURE_JWT_SECRETS = {
    "dev-secret-key-change-in-production",
    "your-secret-key-change-in-production",
    "your-secret-key-here",
}
HUBSPOT_CUSTOM_PROPERTY_SOURCES = {
    "id",
    "source",
    "name",
    "email",
    "phone",
    "message",
    "urgency",
    "summary",
    "created_at",
}
HUBSPOT_RESERVED_PROPERTIES = {
    "email",
    "firstname",
    "lastname",
    "phone",
    "lifecyclestage",
}
HUBSPOT_PROPERTY_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


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
    admin_token: str | None = None
    queue_metrics_token: str | None = None
    trusted_proxy_cidrs: list[str] = []

    # Outbound CRM integration
    crm_provider: Literal["signed_webhook", "hubspot"] = "signed_webhook"
    crm_webhook_url: str | None = None
    crm_webhook_secret: str | None = None
    crm_webhook_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    hubspot_access_token: str | None = None
    hubspot_property_map: dict[str, str] = Field(default_factory=dict)
    crm_retry_base_seconds: int = Field(default=60, ge=1)
    crm_retry_max_seconds: int = Field(default=3600, ge=1)
    crm_retry_max_attempts: int = Field(default=5, ge=1, le=100)
    crm_retry_claim_timeout_seconds: int = Field(default=300, ge=1)
    crm_retry_batch_size: int = Field(default=10, ge=1, le=100)
    crm_retry_poll_seconds: int = Field(default=30, ge=1)

    # Worker alert routing
    alert_webhook_url: str | None = None
    alert_webhook_secret: str | None = None
    alert_webhook_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    alert_stalled_queue_iterations: int = Field(default=3, ge=1)
    alert_high_error_rate_threshold: float = Field(default=0.5, gt=0, le=1)
    alert_min_error_sample_size: int = Field(default=5, ge=1)
    alert_repeated_crash_count: int = Field(default=3, ge=1)
    alert_cooldown_seconds: int = Field(default=900, ge=1)

    # Deduplication
    dedup_window_days: int = 7
    dedup_strategy: Literal["email", "phone", "both"] = "email"

    # Rate Limiting
    rate_limit_per_minute: int = Field(default=60, ge=1)
    rate_limit_per_hour: int = Field(default=1000, ge=1)

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

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def trusted_proxy_cidrs_must_be_valid(cls, values: list[str]) -> list[str]:
        """Normalize explicitly trusted reverse-proxy address ranges."""
        if len(values) > 32:
            raise ValueError("TRUSTED_PROXY_CIDRS must contain at most 32 ranges")

        normalized: list[str] = []
        for value in values:
            try:
                network = ip_network(value.strip(), strict=False)
            except ValueError as exc:
                raise ValueError(
                    "TRUSTED_PROXY_CIDRS must contain valid IP networks"
                ) from exc
            normalized.append(str(network))
        return normalized

    @field_validator(
        "admin_token",
        "queue_metrics_token",
        "crm_webhook_url",
        "crm_webhook_secret",
        "hubspot_access_token",
        "alert_webhook_url",
        "alert_webhook_secret",
    )
    @classmethod
    def optional_token_must_not_be_blank(cls, value: str | None) -> str | None:
        """Treat blank optional secrets and URLs as absent."""
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        return cleaned

    @field_validator("crm_webhook_url")
    @classmethod
    def crm_webhook_url_must_use_https(cls, value: str | None) -> str | None:
        """Require authenticated CRM traffic to use HTTPS."""
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("CRM_WEBHOOK_URL must be an absolute HTTPS URL")
        return value

    @field_validator("crm_webhook_secret")
    @classmethod
    def crm_webhook_secret_must_be_strong(cls, value: str | None) -> str | None:
        """Require enough key material for HMAC-SHA256 signing."""
        if value is not None and len(value) < 32:
            raise ValueError("CRM_WEBHOOK_SECRET must be at least 32 characters")
        return value

    @field_validator("hubspot_property_map")
    @classmethod
    def hubspot_property_map_must_be_safe(
        cls, value: dict[str, str]
    ) -> dict[str, str]:
        """Validate explicit mappings to existing HubSpot custom properties."""
        normalized: dict[str, str] = {}
        used_properties: set[str] = set()
        for source, property_name in value.items():
            if source not in HUBSPOT_CUSTOM_PROPERTY_SOURCES:
                raise ValueError(
                    "HUBSPOT_PROPERTY_MAP contains an unsupported lead field"
                )
            cleaned_property_name = property_name.strip()
            if not HUBSPOT_PROPERTY_NAME_PATTERN.fullmatch(cleaned_property_name):
                raise ValueError(
                    "HUBSPOT_PROPERTY_MAP values must be HubSpot internal property names"
                )
            if cleaned_property_name in HUBSPOT_RESERVED_PROPERTIES:
                raise ValueError(
                    "HUBSPOT_PROPERTY_MAP must not overwrite standard contact properties"
                )
            if cleaned_property_name in used_properties:
                raise ValueError(
                    "HUBSPOT_PROPERTY_MAP values must be unique"
                )
            normalized[source] = cleaned_property_name
            used_properties.add(cleaned_property_name)
        return normalized

    @field_validator("alert_webhook_url")
    @classmethod
    def alert_webhook_url_must_use_https(cls, value: str | None) -> str | None:
        """Require worker notifications to use HTTPS."""
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("ALERT_WEBHOOK_URL must be an absolute HTTPS URL")
        return value

    @field_validator("alert_webhook_secret")
    @classmethod
    def alert_webhook_secret_must_be_strong(cls, value: str | None) -> str | None:
        """Require enough key material for alert HMAC-SHA256 signing."""
        if value is not None and len(value) < 32:
            raise ValueError("ALERT_WEBHOOK_SECRET must be at least 32 characters")
        return value

    @model_validator(mode="after")
    def production_settings_must_be_safe(self) -> "Settings":
        """Fail fast on unsafe production configuration."""
        if (
            self.admin_token is not None
            and self.admin_token == self.queue_metrics_token
        ):
            raise ValueError(
                "ADMIN_TOKEN must be distinct from QUEUE_METRICS_TOKEN"
            )

        if self.crm_provider == "signed_webhook":
            if (self.crm_webhook_url is None) != (self.crm_webhook_secret is None):
                raise ValueError(
                    "CRM_WEBHOOK_URL and CRM_WEBHOOK_SECRET must be configured together"
                )
            if self.hubspot_access_token is not None or self.hubspot_property_map:
                raise ValueError(
                    "HubSpot settings require CRM_PROVIDER=hubspot"
                )
        else:
            if self.hubspot_access_token is None:
                raise ValueError(
                    "HUBSPOT_ACCESS_TOKEN is required when CRM_PROVIDER=hubspot"
                )
            if self.crm_webhook_url is not None or self.crm_webhook_secret is not None:
                raise ValueError(
                    "CRM_WEBHOOK_URL and CRM_WEBHOOK_SECRET must be unset when "
                    "CRM_PROVIDER=hubspot"
                )

        if self.crm_retry_max_seconds < self.crm_retry_base_seconds:
            raise ValueError(
                "CRM_RETRY_MAX_SECONDS must be greater than or equal to "
                "CRM_RETRY_BASE_SECONDS"
            )

        if (self.alert_webhook_url is None) != (self.alert_webhook_secret is None):
            raise ValueError(
                "ALERT_WEBHOOK_URL and ALERT_WEBHOOK_SECRET must be configured together"
            )

        if (
            self.alert_webhook_secret is not None
            and self.alert_webhook_secret == self.crm_webhook_secret
        ):
            raise ValueError(
                "ALERT_WEBHOOK_SECRET must be distinct from CRM_WEBHOOK_SECRET"
            )

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

        if self.admin_token is None or len(self.admin_token) < 24:
            raise ValueError(
                "ADMIN_TOKEN must be configured with at least 24 characters "
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
