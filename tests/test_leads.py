"""Tests for health and lead API contract endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from app.db.client import get_db
from app.models.lead import LeadCreateRequest

if TYPE_CHECKING:
    from starlette.testclient import TestClient


class TestHealthCheck:
    """Health check endpoint tests."""

    def test_health_check_returns_200(self, client: TestClient):
        """Test health check endpoint returns 200."""
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "service": "lead-triage",
            "environment": "test",
            "version": "0.1.0",
        }

    def test_root_endpoint(self, client: TestClient):
        """Test root endpoint returns health check."""
        response = client.get("/")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_database_health_works_with_mock(self, client: TestClient):
        """Test database health without touching a live Supabase project."""

        class MockQuery:
            def select(self, *args):
                return self

            def limit(self, *args):
                return self

            async def execute(self):
                return SimpleNamespace(data=[])

        class MockDatabase:
            def table(self, name: str):
                assert name == "leads"
                return MockQuery()

        async def override_get_db():
            yield MockDatabase()

        client.app.dependency_overrides[get_db] = override_get_db
        try:
            response = client.get("/health/database")
        finally:
            client.app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database": "connected"}


class TestLeadContract:
    """Official lead inquiry contract tests."""

    @pytest.mark.unit
    def test_create_lead_accepts_valid_request(self, client: TestClient):
        """Test POST /api/leads accepts an unstructured inquiry."""
        response = client.post(
            "/api/leads",
            json={
                "source": "website",
                "message": "I need emergency plumbing service today.",
            },
        )

        assert response.status_code == 202
        assert response.json() == {
            "status": "accepted",
            "source": "website",
            "message": "I need emergency plumbing service today.",
            "classification_status": "pending",
        }

    @pytest.mark.unit
    def test_create_lead_requires_message(self, client: TestClient):
        """Test missing message fails validation."""
        response = client.post("/api/leads", json={"source": "website"})

        assert response.status_code == 422

    @pytest.mark.unit
    def test_create_lead_rejects_short_message(self, client: TestClient):
        """Test message shorter than ten characters fails validation."""
        response = client.post("/api/leads", json={"message": "Too short"})

        assert response.status_code == 422

    @pytest.mark.unit
    def test_create_lead_rejects_whitespace_only_message(self, client: TestClient):
        """Test whitespace-only messages fail validation."""
        response = client.post("/api/leads", json={"message": "          "})

        assert response.status_code == 422

    @pytest.mark.unit
    def test_create_lead_defaults_source_to_website(self, client: TestClient):
        """Test source defaults to website when omitted."""
        response = client.post(
            "/api/leads",
            json={"message": "Please contact me about emergency plumbing."},
        )

        assert response.status_code == 202
        assert response.json()["source"] == "website"

    @pytest.mark.unit
    def test_create_lead_rejects_invalid_source_length(self, client: TestClient):
        """Test source must satisfy configured length constraints."""
        response = client.post(
            "/api/leads",
            json={
                "source": "x",
                "message": "Please contact me about emergency plumbing.",
            },
        )

        assert response.status_code == 422

    @pytest.mark.unit
    def test_create_lead_rejects_extra_fields(self, client: TestClient):
        """Test unsupported fields are forbidden."""
        response = client.post(
            "/api/leads",
            json={
                "source": "website",
                "message": "Please contact me about emergency plumbing.",
                "email": "customer@example.com",
            },
        )

        assert response.status_code == 422

    @pytest.mark.unit
    def test_create_lead_model_trims_and_normalizes(self):
        """Test model-level whitespace handling and source normalization."""
        request = LeadCreateRequest(
            source=" Website ",
            message="  I need emergency plumbing service today.  ",
        )

        assert request.source == "website"
        assert request.message == "I need emergency plumbing service today."

    @pytest.mark.unit
    def test_old_ingest_endpoint_is_removed(self, client: TestClient):
        """Test the old public lead route is no longer available."""
        response = client.post(
            "/leads/ingest",
            json={
                "email": "customer@example.com",
                "first_name": "Old",
                "last_name": "Contract",
            },
        )

        assert response.status_code == 404
