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
        with pytest.raises(ValidationError, match="OPENAI_MODEL must not be blank"):
            build_settings(openai_model=model)

    @pytest.mark.unit
    def test_openai_model_is_trimmed(self):
        """Test configured model names are normalized before use."""
        settings = build_settings(openai_model="  gpt-test  ")

        assert settings.openai_model == "gpt-test"
