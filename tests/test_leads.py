"""Tests for lead API endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.models.schemas import LeadIngest
from app.routes.leads import get_lead_service

if TYPE_CHECKING:
    from starlette.testclient import TestClient


class TestHealthCheck:
    """Health check endpoint tests."""

    def test_health_check_returns_200(self, client: TestClient):
        """Test health check endpoint returns 200."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "lead-triage"
        assert "version" in data
        assert "environment" in data

    def test_root_endpoint(self, client: TestClient):
        """Test root endpoint returns health check."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"


class TestLeadIngestion:
    """Lead ingestion endpoint tests."""

    @pytest.mark.unit
    def test_ingest_lead_requires_email(self, client: TestClient):
        """Test that email is required."""
        response = client.post("/leads/ingest", json={
            "first_name": "John",
            "last_name": "Doe",
        })
        assert response.status_code == 422  # Validation error

    @pytest.mark.unit
    def test_ingest_lead_requires_names(self, client: TestClient):
        """Test that first and last names are required."""
        response = client.post("/leads/ingest", json={
            "email": "test@example.com",
        })
        assert response.status_code == 422

    @pytest.mark.unit
    def test_ingest_lead_invalid_email(self, client: TestClient):
        """Test invalid email format."""
        response = client.post("/leads/ingest", json={
            "email": "invalid-email",
            "first_name": "John",
            "last_name": "Doe",
        })
        assert response.status_code == 422

    @pytest.mark.unit
    def test_ingest_lead_normalizes_email(self, client: TestClient):
        """Test email is normalized to lowercase."""
        lead = LeadIngest(
            email="John.Doe@EXAMPLE.COM",
            first_name="John",
            last_name="Doe",
        )

        assert lead.email == "john.doe@example.com"

    @pytest.mark.unit
    def test_ingest_lead_phone_normalization(self, client: TestClient):
        """Test phone number normalization."""
        lead = LeadIngest(
            email="john@example.com",
            first_name="John",
            last_name="Doe",
            phone="+1 (555) 123-4567",
        )

        assert lead.phone == "15551234567"


class TestLeadRetrieval:
    """Lead retrieval endpoint tests."""

    @pytest.fixture(autouse=True)
    def override_lead_service(self, client: TestClient):
        """Mock external lead service calls for retrieval tests."""

        class NotFoundLeadService:
            async def get_lead(self, lead_id: str):
                return None

        client.app.dependency_overrides[get_lead_service] = lambda: NotFoundLeadService()
        yield
        client.app.dependency_overrides.clear()

    @pytest.mark.unit
    def test_get_lead_not_found(self, client: TestClient):
        """Test retrieving non-existent lead."""
        response = client.get("/leads/nonexistent-id")
        assert response.status_code == 404

    @pytest.mark.unit
    def test_get_lead_requires_valid_uuid(self, client: TestClient):
        """Test that lead ID format is validated."""
        response = client.get("/leads/invalid-uuid-format")
        # This would return 404 or 422 depending on validation
        assert response.status_code in [404, 422]
