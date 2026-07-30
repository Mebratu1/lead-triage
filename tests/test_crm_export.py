"""Tests for CRM sync tracking and CSV lead export."""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from io import StringIO
from types import SimpleNamespace
from typing import Any, TYPE_CHECKING

import pytest

from app.db.client import get_db

if TYPE_CHECKING:
    from starlette.testclient import TestClient


ADMIN_TOKEN = "admin-token-with-enough-length"
CSV_HEADERS = [
    "ID",
    "Source",
    "Customer Name",
    "Customer Email",
    "Customer Phone",
    "Status",
    "Urgency",
    "Summary",
    "Created At",
]


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class FakeCrmExportQuery:
    """Small Supabase query-builder fake for export and sync routes."""

    def __init__(self, database: FakeCrmExportDatabase):
        self.database = database
        self.operation = "select"
        self.eq_filters: dict[str, Any] = {}
        self.gte_filters: dict[str, Any] = {}
        self.lte_filters: dict[str, Any] = {}
        self.limit_value: int | None = None
        self.range_start: int | None = None
        self.range_end: int | None = None
        self.order_field: str | None = None
        self.order_desc = False
        self.include_count = False
        self.payload: dict[str, Any] = {}

    def select(self, *args, **kwargs):
        self.include_count = kwargs.get("count") == "exact"
        return self

    def eq(self, field: str, value: Any):
        self.eq_filters[field] = value
        return self

    def gte(self, field: str, value: Any):
        self.gte_filters[field] = value
        return self

    def lte(self, field: str, value: Any):
        self.lte_filters[field] = value
        return self

    def order(self, field: str, desc: bool = False):
        self.order_field = field
        self.order_desc = desc
        return self

    def range(self, start: int, end: int):
        self.range_start = start
        self.range_end = end
        return self

    def limit(self, value: int):
        self.limit_value = value
        return self

    def update(self, payload: dict[str, Any]):
        self.operation = "update"
        self.payload = dict(payload)
        return self

    async def execute(self):
        return self.database.execute_query(self)


class FakeCrmExportDatabase:
    """In-memory Supabase stand-in for CRM/export tests."""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        fail_select: bool = False,
        fail_update: bool = False,
        failure_message: str = "database failure",
    ):
        self.rows = [dict(row) for row in rows or []]
        self.fail_select = fail_select
        self.fail_update = fail_update
        self.failure_message = failure_message
        self.updated_payloads: list[dict[str, Any]] = []

    def table(self, name: str):
        assert name == "leads"
        return FakeCrmExportQuery(self)

    def execute_query(self, query: FakeCrmExportQuery):
        if query.operation == "update":
            return self._execute_update(query)

        if self.fail_select:
            raise RuntimeError(self.failure_message)

        rows = self._filtered_rows(query)
        total = len(rows)
        if query.order_field is not None:
            rows.sort(
                key=lambda row: _parse_timestamp(row[query.order_field]),
                reverse=query.order_desc,
            )
        if query.range_start is not None and query.range_end is not None:
            rows = rows[query.range_start : query.range_end + 1]
        elif query.limit_value is not None:
            rows = rows[: query.limit_value]

        return SimpleNamespace(
            data=[dict(row) for row in rows],
            count=total if query.include_count else None,
        )

    def _execute_update(self, query: FakeCrmExportQuery):
        if self.fail_update:
            raise RuntimeError(self.failure_message)

        updated_rows = []
        for row in self.rows:
            if not all(row.get(field) == value for field, value in query.eq_filters.items()):
                continue
            row.update(query.payload)
            self.updated_payloads.append(dict(query.payload))
            updated_rows.append(dict(row))

        return SimpleNamespace(data=updated_rows)

    def _filtered_rows(self, query: FakeCrmExportQuery) -> list[dict[str, Any]]:
        rows = [dict(row) for row in self.rows]
        for field, value in query.eq_filters.items():
            rows = [row for row in rows if row.get(field) == value]
        for field, value in query.gte_filters.items():
            rows = [
                row
                for row in rows
                if _parse_timestamp(row[field]) >= _parse_timestamp(value)
            ]
        for field, value in query.lte_filters.items():
            rows = [
                row
                for row in rows
                if _parse_timestamp(row[field]) <= _parse_timestamp(value)
            ]
        return rows


def lead_row(
    lead_id: str,
    created_at: str,
    source: str = "website",
    status: str = "classified",
    urgency: str | None = "hot",
    name: str | None = "Maria Customer",
) -> dict[str, Any]:
    """Build a persisted lead row with internal fields that must stay private."""
    return {
        "id": lead_id,
        "source": source,
        "raw_message": "Hi, I need emergency plumbing today.",
        "customer_name": name,
        "email": "maria@example.com",
        "phone": "301-555-0144",
        "classification_status": status,
        "urgency": urgency,
        "ai_summary": "Customer needs plumbing help.",
        "classification_attempt_count": 2,
        "created_at": created_at,
        "classified_at": (
            "2026-07-30T12:30:00+00:00" if status == "classified" else None
        ),
        "integration_status": "pending",
        "integration_last_synced_at": None,
        "integration_error": "private CRM error with test-service-role-key",
        "integration_next_attempt_at": None,
        "idempotency_key": "internal-idempotency-key",
        "deduplication_bucket": "2026-07-30",
        "classification_error": "raw internal error",
        "last_classification_error": "private retry details",
    }


def read_rows() -> list[dict[str, Any]]:
    """Build representative lead rows for export and sync tests."""
    return [
        lead_row(
            lead_id="11111111-1111-4111-8111-111111111111",
            created_at="2026-07-30T12:00:00+00:00",
            source="website",
            status="classified",
            urgency="hot",
        ),
        lead_row(
            lead_id="22222222-2222-4222-8222-222222222222",
            created_at="2026-07-29T12:00:00+00:00",
            source="referral",
            status="pending",
            urgency=None,
            name=None,
        ),
        lead_row(
            lead_id="33333333-3333-4333-8333-333333333333",
            created_at="2026-07-28T12:00:00+00:00",
            source="website",
            status="classified",
            urgency="warm",
        ),
    ]


def db_override(database: FakeCrmExportDatabase):
    async def override_get_db():
        yield database

    return override_get_db


def get_with_db(
    client: TestClient,
    database: FakeCrmExportDatabase,
    url: str,
    headers: dict[str, str] | None = None,
):
    client.app.dependency_overrides[get_db] = db_override(database)
    try:
        request_headers = (
            {"X-Admin-Token": ADMIN_TOKEN} if headers is None else headers
        )
        return client.get(url, headers=request_headers)
    finally:
        client.app.dependency_overrides.clear()


def post_with_db(
    client: TestClient,
    database: FakeCrmExportDatabase,
    url: str,
    headers: dict[str, str] | None = None,
):
    client.app.dependency_overrides[get_db] = db_override(database)
    try:
        request_headers = (
            {"X-Admin-Token": ADMIN_TOKEN} if headers is None else headers
        )
        return client.post(url, headers=request_headers)
    finally:
        client.app.dependency_overrides.clear()


def csv_rows(response_text: str) -> list[dict[str, str]]:
    """Parse CSV response text into dictionaries."""
    return list(csv.DictReader(StringIO(response_text)))


@pytest.fixture(autouse=True)
def admin_token(monkeypatch):
    """Configure the admin token for all CRM/export API tests."""
    monkeypatch.setattr("app.api.routes.leads.settings.queue_metrics_token", ADMIN_TOKEN)


class TestLeadCsvExport:
    """Protected CSV export tests."""

    @pytest.mark.unit
    def test_csv_export_returns_safe_text_csv_with_clean_headers(
        self,
        client: TestClient,
    ):
        """Test CSV export MIME type, headers, and safe column surface."""
        database = FakeCrmExportDatabase(rows=read_rows())

        response = get_with_db(client, database, "/api/leads/export/csv")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/csv; charset=utf-8"
        assert (
            response.headers["content-disposition"]
            == 'attachment; filename="classified_leads_export.csv"'
        )

        reader = csv.DictReader(StringIO(response.text))
        rows = list(reader)
        assert reader.fieldnames == CSV_HEADERS
        assert rows[0] == {
            "ID": "11111111-1111-4111-8111-111111111111",
            "Source": "website",
            "Customer Name": "Maria Customer",
            "Customer Email": "maria@example.com",
            "Customer Phone": "301-555-0144",
            "Status": "classified",
            "Urgency": "hot",
            "Summary": "Customer needs plumbing help.",
            "Created At": "2026-07-30T12:00:00Z",
        }
        assert "idempotency_key" not in response.text
        assert "deduplication_bucket" not in response.text
        assert "integration_error" not in response.text
        assert "test-service-role-key" not in response.text

    @pytest.mark.unit
    def test_csv_export_escapes_spreadsheet_formula_cells(self, client: TestClient):
        """Test CSV export neutralizes spreadsheet formula injection values."""
        rows = read_rows()
        rows[0]["customer_name"] = "=cmd|' /C calc'!A0"
        rows[0]["ai_summary"] = " +SUM(1,1)"
        database = FakeCrmExportDatabase(rows=rows)

        response = get_with_db(client, database, "/api/leads/export/csv")

        assert response.status_code == 200
        exported_rows = csv_rows(response.text)
        assert exported_rows[0]["Customer Name"] == "'=cmd|' /C calc'!A0"
        assert exported_rows[0]["Summary"] == "' +SUM(1,1)"

    @pytest.mark.unit
    def test_csv_export_applies_status_urgency_source_and_date_filters(
        self,
        client: TestClient,
    ):
        """Test CSV export uses the same filtering behavior as lead list reads."""
        database = FakeCrmExportDatabase(rows=read_rows())

        response = get_with_db(
            client,
            database,
            (
                "/api/leads/export/csv?status=classified&urgency=hot"
                "&source=Website&start_date=2026-07-30T00:00:00Z"
                "&end_date=2026-07-30T23:59:59Z"
            ),
        )

        assert response.status_code == 200
        rows = csv_rows(response.text)
        assert [row["ID"] for row in rows] == [
            "11111111-1111-4111-8111-111111111111"
        ]

    @pytest.mark.unit
    @pytest.mark.parametrize("headers", [{}, {"X-Admin-Token": "wrong-token"}])
    def test_csv_export_rejects_unauthorized_requests(
        self,
        client: TestClient,
        headers: dict[str, str],
    ):
        """Test CSV export requires the configured admin token."""
        database = FakeCrmExportDatabase(rows=read_rows())

        response = get_with_db(
            client,
            database,
            "/api/leads/export/csv",
            headers=headers,
        )

        assert response.status_code == 403
        assert response.json() == {"detail": "Admin access required"}

    @pytest.mark.unit
    def test_csv_export_database_failure_is_safe(self, client: TestClient):
        """Test CSV export database failures do not expose internals."""
        database = FakeCrmExportDatabase(
            rows=read_rows(),
            fail_select=True,
            failure_message="database failed with test-service-role-key and SQL text",
        )

        response = get_with_db(client, database, "/api/leads/export/csv")

        assert response.status_code == 503
        assert response.json() == {"detail": "Lead export failed"}
        assert "test-service-role-key" not in response.text
        assert "SQL text" not in response.text


class TestLeadCrmSync:
    """Outbound CRM sync tracking route tests."""

    @pytest.mark.unit
    def test_sync_classified_lead_marks_integration_synced(self, client: TestClient):
        """Test successful sync records safe integration tracking fields."""
        database = FakeCrmExportDatabase(rows=read_rows())

        response = post_with_db(
            client,
            database,
            "/api/leads/11111111-1111-4111-8111-111111111111/sync",
        )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "11111111-1111-4111-8111-111111111111"
        assert body["integration_status"] == "synced"
        assert body["integration_last_synced_at"] is not None
        assert body["retry_after_seconds"] is None
        assert body["detail"] == "Lead synced"
        assert database.rows[0]["integration_status"] == "synced"
        assert database.rows[0]["integration_error"] is None
        assert database.rows[0]["integration_next_attempt_at"] is None

    @pytest.mark.unit
    def test_sync_rejects_unclassified_leads(self, client: TestClient):
        """Test CRM sync only accepts classified lead records."""
        database = FakeCrmExportDatabase(rows=read_rows())

        response = post_with_db(
            client,
            database,
            "/api/leads/22222222-2222-4222-8222-222222222222/sync",
        )

        assert response.status_code == 409
        assert response.json() == {"detail": "Lead must be classified before CRM sync"}
        assert database.updated_payloads == []

    @pytest.mark.unit
    @pytest.mark.parametrize("headers", [{}, {"X-Admin-Token": "wrong-token"}])
    def test_sync_rejects_unauthorized_requests(
        self,
        client: TestClient,
        headers: dict[str, str],
    ):
        """Test CRM sync route requires the configured admin token."""
        database = FakeCrmExportDatabase(rows=read_rows())

        response = post_with_db(
            client,
            database,
            "/api/leads/11111111-1111-4111-8111-111111111111/sync",
            headers=headers,
        )

        assert response.status_code == 403
        assert response.json() == {"detail": "Admin access required"}

    @pytest.mark.unit
    def test_sync_dispatch_failure_records_safe_retry_state(
        self,
        client: TestClient,
        monkeypatch,
        caplog,
    ):
        """Test outbound failures are tracked without leaking raw error text."""
        database = FakeCrmExportDatabase(rows=read_rows())

        async def failing_dispatch(lead):
            raise RuntimeError("raw customer message 301-555-0144 test-service-role-key")

        monkeypatch.setattr(
            "app.api.routes.leads.dispatch_lead_to_crm",
            failing_dispatch,
        )

        with caplog.at_level(logging.WARNING):
            response = post_with_db(
                client,
                database,
                "/api/leads/11111111-1111-4111-8111-111111111111/sync",
            )

        assert response.status_code == 502
        body = response.json()
        assert body["integration_status"] == "failed"
        assert body["integration_last_synced_at"] is None
        assert body["retry_after_seconds"] == 300
        assert body["detail"] == "CRM sync failed; retry scheduled"
        assert database.rows[0]["integration_status"] == "failed"
        assert database.rows[0]["integration_error"] == "crm_dispatch_failed"
        assert database.rows[0]["integration_next_attempt_at"] is not None
        assert "raw customer message" not in response.text
        assert "test-service-role-key" not in response.text
        assert "301-555-0144" not in caplog.text
        assert "test-service-role-key" not in caplog.text

    @pytest.mark.unit
    def test_sync_update_failure_is_safe(self, client: TestClient):
        """Test database update failures during sync return a safe service error."""
        database = FakeCrmExportDatabase(
            rows=read_rows(),
            fail_update=True,
            failure_message="update failed with test-service-role-key and SQL text",
        )

        response = post_with_db(
            client,
            database,
            "/api/leads/11111111-1111-4111-8111-111111111111/sync",
        )

        assert response.status_code == 503
        assert response.json() == {"detail": "Lead sync failed"}
        assert "test-service-role-key" not in response.text
        assert "SQL text" not in response.text
