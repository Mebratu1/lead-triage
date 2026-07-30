"""Configuration safety tests."""

import pytest
from pydantic import ValidationError

from app.config import DEFAULT_OPENAI_MODEL, Settings


def build_settings(**overrides) -> Settings:
    """Build settings with required secrets replaced by test values."""
    values = {
        "supabase_url": "https://example.supabase.co",
        "supabase_service_role_key": "test-service-role-key",
        "openai_api_key": "test-openai-key",
    }
    values.update(overrides)
    return Settings(**values)


class TestOpenAISettings:
    """OpenAI runtime configuration tests."""

    @pytest.mark.unit
    def test_default_openai_model_matches_verified_runtime(self):
        """Test default model is the live-verified OpenAI model."""
        settings = build_settings()

        assert settings.openai_model == "gpt-4.1-mini"
        assert settings.openai_model == DEFAULT_OPENAI_MODEL

    @pytest.mark.unit
    @pytest.mark.parametrize("model", ["", "   "])
    def test_openai_model_rejects_blank_values(self, model):
        """Test blank model names fail during settings validation."""
        with pytest.raises(ValidationError, match="required setting must not be blank"):
            build_settings(openai_model=model)

    @pytest.mark.unit
    def test_openai_model_is_trimmed(self):
        """Test configured model names are normalized before use."""
        settings = build_settings(openai_model="  gpt-test  ")

        assert settings.openai_model == "gpt-test"


class TestRuntimeSettings:
    """General runtime configuration tests."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "field_name",
        ["supabase_url", "supabase_service_role_key", "openai_api_key"],
    )
    def test_required_runtime_strings_reject_blank_values(self, field_name):
        """Test required secret/config strings cannot be blank."""
        with pytest.raises(ValidationError, match="required setting must not be blank"):
            build_settings(**{field_name: "   "})

    @pytest.mark.unit
    def test_log_level_is_normalized(self):
        """Test lowercase log levels are accepted and normalized."""
        settings = build_settings(log_level="warning")

        assert settings.log_level == "WARNING"

    @pytest.mark.unit
    def test_log_level_rejects_unknown_values(self):
        """Test unsupported log levels fail during settings validation."""
        with pytest.raises(ValidationError, match="LOG_LEVEL must be one of"):
            build_settings(log_level="verbose")

    @pytest.mark.unit
    @pytest.mark.parametrize("token", ["", "   "])
    def test_blank_queue_metrics_token_is_treated_as_unconfigured(self, token):
        """Test local development can leave QUEUE_METRICS_TOKEN blank."""
        settings = build_settings(queue_metrics_token=token)

        assert settings.queue_metrics_token is None

    @pytest.mark.unit
    def test_queue_metrics_token_is_trimmed(self):
        """Test configured monitoring token is normalized before use."""
        settings = build_settings(queue_metrics_token="  monitor-token  ")

        assert settings.queue_metrics_token == "monitor-token"


class TestProductionSettings:
    """Production startup safety tests."""

    @pytest.mark.unit
    def test_production_settings_accept_safe_values(self):
        """Test production config accepts explicit secure values."""
        settings = build_settings(
            environment="production",
            debug=False,
            allowed_origins=["https://app.example.com"],
            jwt_secret="production-secret-value-with-enough-entropy",
            queue_metrics_token="queue-metrics-token-with-enough-entropy",
        )

        assert settings.environment == "production"
        assert settings.debug is False

    @pytest.mark.unit
    def test_production_rejects_debug_mode(self):
        """Test DEBUG cannot be enabled in production."""
        with pytest.raises(ValidationError, match="DEBUG must be false in production"):
            build_settings(
                environment="production",
                debug=True,
                allowed_origins=["https://app.example.com"],
                jwt_secret="production-secret-value-with-enough-entropy",
                queue_metrics_token="queue-metrics-token-with-enough-entropy",
            )

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "origin",
        [
            "*",
            "http://localhost:3000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ],
    )
    def test_production_rejects_local_or_wildcard_origins(self, origin):
        """Test production CORS origins must be explicit deploy origins."""
        with pytest.raises(
            ValidationError,
            match="ALLOWED_ORIGINS must use explicit production origins",
        ):
            build_settings(
                environment="production",
                allowed_origins=[origin],
                jwt_secret="production-secret-value-with-enough-entropy",
                queue_metrics_token="queue-metrics-token-with-enough-entropy",
            )

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "jwt_secret",
        [
            "dev-secret-key-change-in-production",
            "your-secret-key-change-in-production",
            "your-secret-key-here",
            "too-short",
        ],
    )
    def test_production_rejects_placeholder_or_short_jwt_secret(self, jwt_secret):
        """Test production JWT secret must be generated and long enough."""
        with pytest.raises(
            ValidationError,
            match="JWT_SECRET must be a generated secret",
        ):
            build_settings(
                environment="production",
                allowed_origins=["https://app.example.com"],
                jwt_secret=jwt_secret,
                queue_metrics_token="queue-metrics-token-with-enough-entropy",
            )

    @pytest.mark.unit
    @pytest.mark.parametrize("token", [None, "", "short-token"])
    def test_production_requires_queue_metrics_token(self, token):
        """Test production queue metrics endpoint must be protected."""
        with pytest.raises(
            ValidationError,
            match="QUEUE_METRICS_TOKEN must be configured",
        ):
            build_settings(
                environment="production",
                allowed_origins=["https://app.example.com"],
                jwt_secret="production-secret-value-with-enough-entropy",
                queue_metrics_token=token,
            )
