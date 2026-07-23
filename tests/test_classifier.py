"""Tests for lead classification service."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.models.lead import Lead
from app.services.classifier import ClassificationService


class TestClassificationService:
    """Classification service tests."""

    @pytest.fixture
    def classifier(self):
        """Create classifier with mock API key."""
        return ClassificationService(api_key="test-key", model="gpt-4-turbo-preview")

    @pytest.mark.unit
    def test_parse_valid_classification_response(self, classifier):
        """Test parsing valid LLM response."""
        response_text = json.dumps({
            "lead_score": 85,
            "status": "qualified",
            "tags": ["sales_ready", "high_priority"],
            "rationale": "Strong fit for enterprise market, budget confirmed.",
        })

        result = classifier._parse_classification_response(response_text)

        assert result["lead_score"] == 85
        assert result["status"] == "qualified"
        assert "sales_ready" in result["tags"]
        assert len(result["rationale"]) > 0

    @pytest.mark.unit
    def test_parse_response_with_wrapper_text(self, classifier):
        """Test parsing response with surrounding text."""
        response_text = """
        Here's the classification:
        
        {
            "lead_score": 72,
            "status": "needs_nurture",
            "tags": ["needs_nurture"],
            "rationale": "Good company profile but early stage decision-maker."
        }
        
        Let me know if you need anything else!
        """

        result = classifier._parse_classification_response(response_text)

        assert result["lead_score"] == 72
        assert result["status"] == "needs_nurture"

    @pytest.mark.unit
    def test_parse_invalid_lead_score(self, classifier):
        """Test validation of lead score range."""
        response_text = json.dumps({
            "lead_score": 150,  # Invalid: > 100
            "status": "qualified",
            "tags": ["sales_ready"],
            "rationale": "Test",
        })

        with pytest.raises(ValueError):
            classifier._parse_classification_response(response_text)

    @pytest.mark.unit
    def test_parse_invalid_status(self, classifier):
        """Test validation of status field."""
        response_text = json.dumps({
            "lead_score": 85,
            "status": "invalid_status",
            "tags": ["sales_ready"],
            "rationale": "Test",
        })

        with pytest.raises(ValueError):
            classifier._parse_classification_response(response_text)

    @pytest.mark.unit
    def test_parse_missing_required_field(self, classifier):
        """Test validation of required fields."""
        response_text = json.dumps({
            "lead_score": 85,
            "status": "qualified",
            # Missing 'tags' and 'rationale'
        })

        with pytest.raises(ValueError):
            classifier._parse_classification_response(response_text)

    @pytest.mark.unit
    def test_parse_invalid_json(self, classifier):
        """Test handling of invalid JSON."""
        response_text = "This is not valid JSON"

        with pytest.raises(ValueError):
            classifier._parse_classification_response(response_text)

    @pytest.mark.unit
    def test_build_prompt_includes_lead_data(self, classifier):
        """Test prompt building includes all lead data."""
        lead = Lead(
            id="test-id",
            email="john@example.com",
            first_name="John",
            last_name="Doe",
            company="Acme Corp",
            job_title="CTO",
            phone="+1-555-123-4567",
            source="inbound_form",
        )

        prompt = classifier._build_prompt(lead)

        assert "john@example.com" in prompt
        assert "John Doe" in prompt
        assert "Acme Corp" in prompt
        assert "CTO" in prompt
        assert "inbound_form" in prompt
        assert "lead_score" in prompt
        assert "JSON" in prompt

    @pytest.mark.unit
    def test_build_prompt_handles_missing_fields(self, classifier):
        """Test prompt building with missing optional fields."""
        lead = Lead(
            id="test-id",
            email="jane@example.com",
            first_name="Jane",
            last_name="Smith",
            # phone, company, job_title are None
        )

        prompt = classifier._build_prompt(lead)

        assert "jane@example.com" in prompt
        assert "Jane Smith" in prompt
        assert "Not provided" in prompt or "None" in prompt


class TestClassificationServiceProviderCalls:
    """Classification provider-call tests."""

    @pytest.mark.unit
    def test_classify_lead_propagates_provider_failure(self):
        """Test provider failures surface to the caller without a live API key."""

        class FailingCompletions:
            async def create(self, **kwargs):
                raise RuntimeError("provider unavailable")

        classifier = ClassificationService(api_key="test-key", model="gpt-4-turbo-preview")
        classifier.client = SimpleNamespace(
            chat=SimpleNamespace(completions=FailingCompletions())
        )
        lead = Lead(
            id="test-id",
            email="test@example.com",
            first_name="Test",
            last_name="User",
        )

        with pytest.raises(RuntimeError, match="provider unavailable"):
            asyncio.run(classifier.classify_lead(lead))
