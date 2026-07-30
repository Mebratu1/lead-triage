"""Tests for the browser admin dashboard shell."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from starlette.testclient import TestClient


class TestAdminDashboard:
    """Static admin dashboard contract tests."""

    @pytest.mark.unit
    def test_admin_dashboard_serves_html(self, client: TestClient):
        """Test GET /admin returns the browser dashboard shell."""
        response = client.get("/admin")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "<title>LeadTriage Admin</title>" in response.text
        assert "LeadTriage Admin" in response.text

    @pytest.mark.unit
    def test_admin_dashboard_uses_existing_api_contracts(self, client: TestClient):
        """Test dashboard JavaScript matches protected backend endpoints."""
        response = client.get("/admin")
        html = response.text

        assert 'fetch("/health/queue"' in html
        assert "Authorization: `Bearer ${token}`" in html
        assert '`/api/leads?${params.toString()}`' in html
        assert '"X-Admin-Token": token' in html
        assert 'params.set("classification_status", statusFilter.value)' in html
        assert "X-Queue-Metrics-Token" not in html
        assert "params.set(\"status\"" not in html

    @pytest.mark.unit
    def test_admin_dashboard_uses_supported_urgency_values(self, client: TestClient):
        """Test dashboard filters use classification urgency enum values."""
        response = client.get("/admin")
        html = response.text

        assert '<option value="hot">Hot</option>' in html
        assert '<option value="warm">Warm</option>' in html
        assert '<option value="cold">Cold</option>' in html
        assert '<option value="high">High</option>' not in html
        assert '<option value="medium">Medium</option>' not in html
        assert '<option value="low">Low</option>' not in html

    @pytest.mark.unit
    def test_admin_dashboard_does_not_embed_secrets(self, client: TestClient):
        """Test static HTML does not contain real or placeholder secret values."""
        response = client.get("/admin")
        html = response.text

        assert "SUPABASE_SERVICE_ROLE_KEY" not in html
        assert "OPENAI_API_KEY" not in html
        assert "sk-proj-" not in html
        assert "sb_secret_" not in html
        assert "test-service-role-key" not in html
