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
    def test_blank_admin_token_is_treated_as_unconfigured(self, token):
        """Test local development can leave ADMIN_TOKEN blank."""
        settings = build_settings(admin_token=token)

        assert settings.admin_token is None

    @pytest.mark.unit
    def test_admin_token_is_trimmed(self):
        """Test configured admin token is normalized before use."""
        settings = build_settings(admin_token="  admin-token  ")

        assert settings.admin_token == "admin-token"

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

    @pytest.mark.unit
    def test_admin_and_queue_tokens_must_be_distinct(self):
        """Test admin and monitoring credentials cannot be reused."""
        with pytest.raises(ValidationError, match="must be distinct"):
            build_settings(
                admin_token="shared-token",
                queue_metrics_token="shared-token",
            )

    @pytest.mark.unit
    def test_trusted_proxy_cidrs_are_normalized(self):
        """Test trusted proxy configuration stores canonical networks."""
        settings = build_settings(
            trusted_proxy_cidrs=["10.1.2.3/8", "2001:db8::1/64"]
        )

        assert settings.trusted_proxy_cidrs == [
            "10.0.0.0/8",
            "2001:db8::/64",
        ]

    @pytest.mark.unit
    @pytest.mark.parametrize("network", ["", "not-a-network", "10.0.0.1/99"])
    def test_trusted_proxy_cidrs_reject_invalid_networks(self, network):
        """Test malformed trust boundaries fail during configuration."""
        with pytest.raises(
            ValidationError,
            match="must contain valid IP networks",
        ):
            build_settings(trusted_proxy_cidrs=[network])

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "overrides",
        [
            {"crm_webhook_url": "https://crm.example.test/hook"},
            {
                "crm_webhook_secret": (
                    "crm-test-signing-secret-with-enough-entropy"
                )
            },
        ],
    )
    def test_crm_url_and_secret_must_be_configured_together(self, overrides):
        """Test partial webhook configuration fails during startup validation."""
        with pytest.raises(ValidationError, match="must be configured together"):
            build_settings(**overrides)

    @pytest.mark.unit
    def test_crm_webhook_requires_https(self):
        """Test CRM lead payloads cannot be configured over plaintext HTTP."""
        with pytest.raises(ValidationError, match="absolute HTTPS URL"):
            build_settings(
                crm_webhook_url="http://crm.example.test/hook",
                crm_webhook_secret="crm-test-signing-secret-with-enough-entropy",
            )

    @pytest.mark.unit
    def test_crm_webhook_requires_strong_signing_secret(self):
        """Test short HMAC secrets are rejected."""
        with pytest.raises(ValidationError, match="at least 32 characters"):
            build_settings(
                crm_webhook_url="https://crm.example.test/hook",
                crm_webhook_secret="too-short",
            )

    @pytest.mark.unit
    def test_hubspot_requires_an_explicit_access_token(self):
        """Test selecting HubSpot fails closed until a token is supplied."""
        with pytest.raises(ValidationError, match="HUBSPOT_ACCESS_TOKEN is required"):
            build_settings(crm_provider="hubspot")

    @pytest.mark.unit
    def test_hubspot_rejects_webhook_settings(self):
        """Test provider selection cannot leave two outbound paths configured."""
        with pytest.raises(ValidationError, match="must be unset"):
            build_settings(
                crm_provider="hubspot",
                hubspot_access_token="hubspot-private-app-token",
                crm_webhook_url="https://crm.example.test/hook",
                crm_webhook_secret="crm-test-signing-secret-with-enough-entropy",
            )

    @pytest.mark.unit
    def test_hubspot_accepts_explicit_custom_property_mapping(self):
        """Test only configured HubSpot custom fields supplement standard mappings."""
        settings = build_settings(
            crm_provider="hubspot",
            hubspot_access_token="hubspot-private-app-token",
            hubspot_property_map={"source": "lead_source", "urgency": "lead_urgency"},
        )

        assert settings.hubspot_property_map == {
            "source": "lead_source",
            "urgency": "lead_urgency",
        }

    @pytest.mark.unit
    def test_hubspot_property_map_loads_from_environment_settings(self, monkeypatch):
        """Test the JSON environment setting is parsed and normalized by Pydantic."""
        monkeypatch.setenv("CRM_PROVIDER", "hubspot")
        monkeypatch.setenv("HUBSPOT_ACCESS_TOKEN", "hubspot-private-app-token")
        monkeypatch.setenv(
            "HUBSPOT_PROPERTY_MAP",
            '{"source":" lead_source ","urgency":"lead_urgency"}',
        )

        settings = Settings(
            _env_file=None,
            supabase_url="https://example.supabase.co",
            supabase_service_role_key="test-service-role-key",
            openai_api_key="test-openai-key",
        )

        assert settings.hubspot_property_map == {
            "source": "lead_source",
            "urgency": "lead_urgency",
        }

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "property_map, message",
        [
            ({"unknown": "lead_source"}, "unsupported lead field"),
            ({"source": "email"}, "must not overwrite"),
            ({"source": "LeadSource"}, "internal property names"),
            (
                {"source": "lead_source", "urgency": "lead_source"},
                "must be unique",
            ),
        ],
    )
    def test_hubspot_rejects_unsafe_custom_property_maps(self, property_map, message):
        """Test custom mappings cannot target unknown or conflicting HubSpot fields."""
        with pytest.raises(ValidationError, match=message):
            build_settings(
                crm_provider="hubspot",
                hubspot_access_token="hubspot-private-app-token",
                hubspot_property_map=property_map,
            )

    @pytest.mark.unit
    def test_alert_webhook_requires_https_and_complete_configuration(self):
        """Test alert delivery cannot be partially or insecurely configured."""
        with pytest.raises(ValidationError, match="must be configured together"):
            build_settings(
                alert_webhook_secret=(
                    "alert-test-signing-secret-with-enough-entropy"
                )
            )
        with pytest.raises(ValidationError, match="absolute HTTPS URL"):
            build_settings(
                alert_webhook_url="http://alerts.example.test/hook",
                alert_webhook_secret=(
                    "alert-test-signing-secret-with-enough-entropy"
                ),
            )

    @pytest.mark.unit
    def test_alert_and_crm_signing_secrets_must_be_distinct(self):
        """Test a compromise cannot reuse one key across both webhook purposes."""
        shared_secret = "shared-test-signing-secret-with-enough-entropy"
        with pytest.raises(ValidationError, match="must be distinct"):
            build_settings(
                crm_webhook_url="https://crm.example.test/hook",
                crm_webhook_secret=shared_secret,
                alert_webhook_url="https://alerts.example.test/hook",
                alert_webhook_secret=shared_secret,
            )


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
            admin_token="admin-token-with-enough-entropy",
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
                admin_token="admin-token-with-enough-entropy",
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
                admin_token="admin-token-with-enough-entropy",
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
                admin_token="admin-token-with-enough-entropy",
                queue_metrics_token=token,
            )

    @pytest.mark.unit
    @pytest.mark.parametrize("token", [None, "", "short-token"])
    def test_production_requires_admin_token(self, token):
        """Test production admin routes require a strong distinct credential."""
        with pytest.raises(
            ValidationError,
            match="ADMIN_TOKEN must be configured",
        ):
            build_settings(
                environment="production",
                allowed_origins=["https://app.example.com"],
                jwt_secret="production-secret-value-with-enough-entropy",
                admin_token=token,
                queue_metrics_token="queue-metrics-token-with-enough-entropy",
            )
