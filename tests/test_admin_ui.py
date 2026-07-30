"""Tests for the browser admin dashboard foundation."""

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
        assert "<title>LeadTriage Admin Dashboard</title>" in response.text
        assert "LeadTriage Admin" in response.text

    @pytest.mark.unit
    def test_admin_dashboard_renders_required_markup(self, client: TestClient):
        """Test dashboard includes token, telemetry, filters, and table markup."""
        response = client.get("/admin")
        html = response.text

        assert "<style>" in html
        assert "https://cdn.tailwindcss.com" not in html
        assert "<script src=" not in html
        assert 'id="adminToken"' in html
        assert 'id="queueToken"' in html
        assert "lead_triage_admin_token" in html
        assert "lead_triage_queue_metrics_token" in html
        assert 'id="pendingCount"' in html
        assert 'id="backoffCount"' in html
        assert 'id="exhaustedCount"' in html
        assert 'id="maxAttempts"' in html
        assert 'id="statusFilter"' in html
        assert 'id="urgencyFilter"' in html
        assert 'id="leadsTableBody"' in html

    @pytest.mark.unit
    def test_admin_dashboard_uses_existing_api_contracts(self, client: TestClient):
        """Test dashboard JavaScript matches protected backend endpoints."""
        response = client.get("/admin")
        html = response.text

        assert 'fetch("/health/queue"' in html
        assert '`/api/leads?${params.toString()}`' in html
        assert 'return token ? { "X-Admin-Token": token } : {}' in html
        assert "return token ? { Authorization: `Bearer ${token}` } : {}" in html
        assert 'params.set("classification_status", statusFilter.value)' in html
        assert 'params.set("urgency", urgencyFilter.value)' in html
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
    def test_admin_dashboard_has_live_refresh(self, client: TestClient):
        """Test dashboard periodically refreshes queue and lead data."""
        response = client.get("/admin")
        html = response.text

        assert "REFRESH_INTERVAL_MS = 30000" in html
        assert "window.setInterval(refreshData, REFRESH_INTERVAL_MS)" in html

    @pytest.mark.unit
    def test_admin_dashboard_renders_rich_lead_detail_dialog(self, client: TestClient):
        """Test dashboard exposes an accessible detail view with safe lead fields."""
        response = client.get("/admin")
        html = response.text

        assert 'id="leadDetailDialog"' in html
        assert 'aria-labelledby="leadDetailTitle"' in html
        assert 'id="detailStatus"' in html
        assert 'aria-live="polite"' in html
        assert 'id="leadDetailContent"' in html
        assert 'id="syncLeadButton"' in html
        assert 'id="closeDetailButton"' in html
        assert "Classification attempts" in html
        assert "CRM integration" in html
        assert "Last CRM sync" in html
        assert "CRM retry state" in html
        assert "Next CRM attempt" in html
        assert "CRM retry attempts" in html
        assert "Original message" in html
        assert "AI summary" in html

    @pytest.mark.unit
    def test_admin_dashboard_wires_detail_sync_and_export_endpoints(
        self,
        client: TestClient,
    ):
        """Test all Milestone 5D actions call the existing protected contracts."""
        response = client.get("/admin")
        html = response.text

        assert '`/api/leads/${encodeURIComponent(leadId)}`' in html
        assert '`/api/leads/${encodeURIComponent(leadId)}/sync`' in html
        assert '`/api/leads/export/csv?${params.toString()}`' in html
        assert 'method: "POST"' in html
        assert 'headers: getAdminAuthHeaders()' in html
        assert 'headers: getQueueAuthHeaders()' in html
        assert 'id="exportCsvButton"' in html
        assert 'class="button-secondary button-small view-lead-button"' in html

    @pytest.mark.unit
    def test_admin_dashboard_exports_current_filters_without_token_in_url(
        self,
        client: TestClient,
    ):
        """Test filtered exports authenticate by header and clean up blob URLs."""
        response = client.get("/admin")
        html = response.text

        assert "const params = buildLeadQueryParams(1000)" in html
        assert 'params.set("classification_status", statusFilter.value)' in html
        assert 'params.set("urgency", urgencyFilter.value)' in html
        assert "const blob = await response.blob()" in html
        assert 'downloadLink.download = "classified_leads_export.csv"' in html
        assert "URL.revokeObjectURL(downloadUrl)" in html
        assert "X-Admin-Token=${" not in html
        assert "token=${" not in html

    @pytest.mark.unit
    def test_admin_dashboard_hardens_async_and_sync_interactions(
        self,
        client: TestClient,
    ):
        """Test duplicate, stale, unsafe, and unclassified actions are guarded."""
        response = client.get("/admin")
        html = response.text

        assert "if (refreshInFlight && !force)" in html
        assert "queueAbortController.abort()" in html
        assert "leadListAbortController.abort()" in html
        assert "detailAbortController.abort()" in html
        assert "syncAbortController.abort()" in html
        assert "exportAbortController.abort()" in html
        assert "function cancelProtectedRequests()" in html
        assert "function cancelAdminRequests()" in html
        assert "function cancelQueueRequest()" in html
        assert "function showEmptyLeadDetail()" in html
        assert "const adminTokenChanged = adminToken !== getAdminToken()" in html
        assert "const queueTokenChanged = queueToken !== getQueueToken()" in html
        assert 'if (error.name === "AbortError")' in html
        assert "leadDetailContent.removeAttribute(\"aria-busy\")" in html
        assert "cancelProtectedRequests();" in html
        assert "signal: requestController.signal" in html
        assert 'error.name === "AbortError"' in html
        assert 'selectedLead.classification_status !== "classified"' in html
        assert "const confirmed = window.confirm(" in html
        assert "setButtonBusy(syncLeadButton, true" in html
        assert "String(body.id) !== String(leadId)" in html
        assert "escapeHtml(lead.message" in html

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
