"""Tests for isolated lead classification contracts and OpenAI wrapper."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.models.classification import (
    LeadClassificationStatus,
    LeadUrgency,
)
from app.services.lead_classification import (
    build_classification_messages,
    classify_raw_message,
    parse_classification_response,
    validate_prompt_messages,
)
from app.services.openai_client import (
    OpenAIClassificationError,
    OpenAILeadClassificationClient,
)


class FakeClassificationClient:
    """Mock classification client that returns a fixed model payload."""

    def __init__(self, response: str):
        self.response = response
        self.received_message: str | None = None

    async def classify(self, raw_message: str) -> str:
        self.received_message = raw_message
        return self.response


class FakeOpenAICompletions:
    """Mock OpenAI chat completions endpoint."""

    def __init__(self, content: str | None):
        self.content = content
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.content),
                )
            ]
        )


def valid_payload(**overrides) -> str:
    """Build valid model JSON with optional field overrides."""
    payload = {
        "customer_name": "Jane Doe",
        "email": "jane@example.com",
        "phone": "301-555-0144",
        "requested_service": "emergency plumbing",
        "urgency": "hot",
        "lead_score": 92,
        "ai_summary": "Customer needs emergency plumbing service today.",
    }
    payload.update(overrides)

    import json

    return json.dumps(payload)


class TestLeadClassificationParser:
    """Parser and contract validation tests."""

    @pytest.mark.unit
    def test_parse_successful_structured_payload(self):
        """Test valid model JSON becomes a classified lead."""
        result = parse_classification_response(valid_payload())

        assert result.classification_status == LeadClassificationStatus.CLASSIFIED
        assert result.customer_name == "Jane Doe"
        assert result.email == "jane@example.com"
        assert result.phone == "301-555-0144"
        assert result.requested_service == "emergency plumbing"
        assert result.urgency == LeadUrgency.HOT
        assert result.lead_score == 92
        assert result.ai_summary == "Customer needs emergency plumbing service today."
        assert result.error_reason is None

    @pytest.mark.unit
    def test_parse_cleans_markdown_json_fence(self):
        """Test markdown JSON fences are stripped before validation."""
        result = parse_classification_response(f"```json\n{valid_payload()}\n```")

        assert result.classification_status == LeadClassificationStatus.CLASSIFIED
        assert result.customer_name == "Jane Doe"

    @pytest.mark.unit
    def test_parse_extracts_json_object_from_surrounding_text(self):
        """Test parser can recover a JSON object from extra wrapper text."""
        result = parse_classification_response(f"Here is JSON:\n{valid_payload()}")

        assert result.classification_status == LeadClassificationStatus.CLASSIFIED
        assert result.lead_score == 92

    @pytest.mark.unit
    def test_parse_malformed_json_returns_failed_contract(self):
        """Test malformed JSON does not raise or leak internals."""
        result = parse_classification_response("{not valid json")

        assert result.classification_status == LeadClassificationStatus.FAILED
        assert result.error_reason == "invalid_json"
        assert result.customer_name is None
        assert result.lead_score is None

    @pytest.mark.unit
    def test_parse_invalid_json_shape_returns_failed_contract(self):
        """Test non-object JSON is rejected."""
        result = parse_classification_response("[1, 2, 3]")

        assert result.classification_status == LeadClassificationStatus.FAILED
        assert result.error_reason == "invalid_json_shape"

    @pytest.mark.unit
    @pytest.mark.parametrize("score", [-1, 101])
    def test_parse_rejects_score_outside_zero_to_one_hundred(self, score: int):
        """Test score range is strictly validated."""
        result = parse_classification_response(valid_payload(lead_score=score))

        assert result.classification_status == LeadClassificationStatus.FAILED
        assert result.error_reason == "invalid_classification_payload"

    @pytest.mark.unit
    @pytest.mark.parametrize("score", [0, 100])
    def test_parse_accepts_score_boundaries(self, score: int):
        """Test score boundary values are valid."""
        result = parse_classification_response(valid_payload(lead_score=score))

        assert result.classification_status == LeadClassificationStatus.CLASSIFIED
        assert result.lead_score == score

    @pytest.mark.unit
    @pytest.mark.parametrize("urgency", ["hot", "warm", "cold"])
    def test_parse_accepts_supported_urgency_values(self, urgency: str):
        """Test supported urgency enum values."""
        result = parse_classification_response(valid_payload(urgency=urgency))

        assert result.classification_status == LeadClassificationStatus.CLASSIFIED
        assert result.urgency == urgency

    @pytest.mark.unit
    def test_parse_rejects_invalid_urgency(self):
        """Test unsupported urgency values fail safely."""
        result = parse_classification_response(valid_payload(urgency="urgent"))

        assert result.classification_status == LeadClassificationStatus.FAILED
        assert result.error_reason == "invalid_classification_payload"

    @pytest.mark.unit
    def test_parse_rejects_extra_fields(self):
        """Test unexpected model fields fail strict validation."""
        result = parse_classification_response(valid_payload(unexpected="value"))

        assert result.classification_status == LeadClassificationStatus.FAILED
        assert result.error_reason == "invalid_classification_payload"

    @pytest.mark.unit
    def test_parse_rejects_missing_required_payload_keys(self):
        """Test model output must include every extraction key."""
        result = parse_classification_response(
            '{"customer_name": null, "email": null}'
        )

        assert result.classification_status == LeadClassificationStatus.FAILED
        assert result.error_reason == "invalid_classification_payload"

    @pytest.mark.unit
    def test_parse_rejects_model_supplied_lifecycle_fields(self):
        """Test model output cannot set classification lifecycle metadata."""
        result = parse_classification_response(
            valid_payload(
                classification_status="failed",
                error_reason="model supplied status",
            )
        )

        assert result.classification_status == LeadClassificationStatus.FAILED
        assert result.error_reason == "invalid_classification_payload"

    @pytest.mark.unit
    def test_parse_blank_strings_become_none(self):
        """Test blank string outputs are treated as missing values."""
        result = parse_classification_response(
            valid_payload(
                customer_name="   ",
                email="",
                phone=" ",
                requested_service="",
                ai_summary=" ",
            )
        )

        assert result.customer_name is None
        assert result.email is None
        assert result.phone is None
        assert result.requested_service is None
        assert result.ai_summary is None


class TestLeadClassificationService:
    """Service-level tests using mocked classification clients."""

    @pytest.mark.unit
    def test_classify_raw_message_uses_mocked_client(self):
        """Test service parses mocked client output without network calls."""
        client = FakeClassificationClient(valid_payload())

        result = asyncio.run(
            classify_raw_message(
                raw_message="I need emergency plumbing service today.",
                client=client,
            )
        )

        assert client.received_message == "I need emergency plumbing service today."
        assert result.classification_status == LeadClassificationStatus.CLASSIFIED
        assert result.requested_service == "emergency plumbing"

    @pytest.mark.unit
    def test_prompt_messages_have_expected_shape(self):
        """Test prompt messages are valid chat messages."""
        raw_message = "Please call me about plumbing."

        messages = build_classification_messages(raw_message)

        assert validate_prompt_messages(messages)
        assert messages[0]["role"] == "system"
        assert messages[1] == {"role": "user", "content": raw_message}


class TestOpenAILeadClassificationClient:
    """OpenAI wrapper tests with mocked OpenAI client objects."""

    @pytest.mark.unit
    def test_openai_wrapper_uses_configured_model_and_json_response_format(self):
        """Test wrapper calls mocked OpenAI client without network access."""
        completions = FakeOpenAICompletions(valid_payload())
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions),
        )
        client = OpenAILeadClassificationClient(
            model="gpt-test",
            client=fake_client,
        )

        content = asyncio.run(client.classify("Please call me about plumbing."))

        assert content == valid_payload()
        assert completions.calls[0]["model"] == "gpt-test"
        assert completions.calls[0]["temperature"] == 0
        assert completions.calls[0]["response_format"] == {"type": "json_object"}
        assert completions.calls[0]["messages"][1]["content"] == (
            "Please call me about plumbing."
        )

    @pytest.mark.unit
    def test_openai_wrapper_does_not_log_raw_customer_message(self, caplog):
        """Test logs include metadata but not full customer text."""
        raw_message = "Private phone 301-555-0144, urgent plumbing help."
        completions = FakeOpenAICompletions(valid_payload())
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions),
        )
        client = OpenAILeadClassificationClient(
            model="gpt-test",
            client=fake_client,
        )

        with caplog.at_level(logging.INFO):
            asyncio.run(client.classify(raw_message))

        assert "gpt-test" in caplog.text
        assert "message_length" in caplog.text
        assert raw_message not in caplog.text
        assert "301-555-0144" not in caplog.text

    @pytest.mark.unit
    def test_openai_wrapper_rejects_empty_content(self):
        """Test empty model content raises a controlled wrapper error."""
        completions = FakeOpenAICompletions(None)
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions),
        )
        client = OpenAILeadClassificationClient(
            model="gpt-test",
            client=fake_client,
        )

        with pytest.raises(OpenAIClassificationError):
            asyncio.run(client.classify("Please call me about plumbing."))

    @pytest.mark.unit
    def test_openai_wrapper_rejects_invalid_response_shape(self):
        """Test invalid OpenAI response shapes raise controlled errors."""
        async def invalid_create(**kwargs):
            return SimpleNamespace(choices=[])

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=invalid_create,
                )
            ),
        )
        client = OpenAILeadClassificationClient(
            model="gpt-test",
            client=fake_client,
        )

        with pytest.raises(OpenAIClassificationError):
            asyncio.run(client.classify("Please call me about plumbing."))
