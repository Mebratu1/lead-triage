"""Tests for protected classified lead read APIs."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any, TYPE_CHECKING

import pytest

from app.db.client import get_db

if TYPE_CHECKING:
    from starlette.testclient import TestClient


ADMIN_TOKEN = "admin-token-with-enough-length"


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class FakeLeadReadQuery:
    """Small Supabase query-builder fake for read-side lead queries."""

    def __init__(self, database: FakeLeadReadDatabase):
        self.database = database
        self.eq_filters: dict[str, Any] = {}
        self.gte_filters: dict[str, Any] = {}
        self.lte_filters: dict[str, Any] = {}
        self.limit_value: int | None = None
        self.range_start: int | None = None
        self.range_end: int | None = None
        self.order_field: str | None = None
        self.order_desc = False
        self.include_count = False

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

    async def execute(self):
        return self.database.execute_query(self)


class FakeLeadReadDatabase:
    """In-memory Supabase stand-in for read-side lead API tests."""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        fail: bool = False,
        failure_message: str = "database failure",
    ):
        self.rows = [dict(row) for row in rows or []]
        self.fail = fail
        self.failure_message = failure_message

    def table(self, name: str):
        assert name == "leads"
        return FakeLeadReadQuery(self)

    def execute_query(self, query: FakeLeadReadQuery):
        if self.fail:
            raise RuntimeError(self.failure_message)

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
            data=rows,
            count=total if query.include_count else None,
        )


def lead_row(
    lead_id: str,
    created_at: str,
    source: str = "website",
    status: str = "classified",
    urgency: str | None = "hot",
    name: str | None = "Maria Customer",
) -> dict[str, Any]:
    """Build a persisted lead row including internal fields that must stay private."""
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
        "idempotency_key": "internal-idempotency-key",
        "deduplication_bucket": "2026-07-30",
        "classification_error": "raw internal error with test-service-role-key",
        "last_classification_error": "private retry details",
        "classification_model": "gpt-4.1-mini",
        "classification_claimed_by": "worker-private",
    }


def read_rows() -> list[dict[str, Any]]:
    """Build representative read-side rows."""
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
        lead_row(
            lead_id="44444444-4444-4444-8444-444444444444",
            created_at="2026-07-27T12:00:00+00:00",
            source="website",
            status="failed",
            urgency=None,
            name=None,
        ),
    ]


def db_override(database: FakeLeadReadDatabase):
    async def override_get_db():
        yield database

    return override_get_db


def get_with_db(
    client: TestClient,
    database: FakeLeadReadDatabase,
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


@pytest.fixture(autouse=True)
def admin_token(monkeypatch):
    """Configure the admin token for all read API tests."""
    monkeypatch.setattr("app.api.routes.leads.settings.queue_metrics_token", ADMIN_TOKEN)


class TestLeadReadApi:
    """Protected read-side lead API tests."""

    @pytest.mark.unit
    def test_list_leads_filters_by_status_urgency_source_and_date_range(
        self,
        client: TestClient,
    ):
        """Test supported read filters are applied before pagination."""
        database = FakeLeadReadDatabase(rows=read_rows())

        response = get_with_db(
            client,
            database,
            (
                "/api/leads?classification_status=classified&urgency=hot"
                "&source=Website&start_date=2026-07-30T00:00:00Z"
                "&end_date=2026-07-30T23:59:59Z"
            ),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["limit"] == 50
        assert body["offset"] == 0
        assert [item["id"] for item in body["items"]] == [
            "11111111-1111-4111-8111-111111111111"
        ]

    @pytest.mark.unit
    def test_list_leads_paginates_newest_first(self, client: TestClient):
        """Test lead list returns newest rows first with limit and offset."""
        database = FakeLeadReadDatabase(rows=read_rows())

        response = get_with_db(client, database, "/api/leads?limit=2&offset=1")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 4
        assert body["limit"] == 2
        assert body["offset"] == 1
        assert [item["id"] for item in body["items"]] == [
            "22222222-2222-4222-8222-222222222222",
            "33333333-3333-4333-8333-333333333333",
        ]

    @pytest.mark.unit
    def test_list_leads_rejects_reversed_date_range(self, client: TestClient):
        """Test reversed date ranges fail safely before database access."""
        database = FakeLeadReadDatabase(rows=read_rows())

        response = get_with_db(
            client,
            database,
            (
                "/api/leads?start_date=2026-07-31T00:00:00"
                "&end_date=2026-07-30T00:00:00Z"
            ),
        )

        assert response.status_code == 422
        assert response.json() == {
            "detail": "start_date must be before or equal to end_date"
        }

    @pytest.mark.unit
    def test_get_lead_by_id_returns_safe_public_payload(self, client: TestClient):
        """Test detail response maps safe fields and omits internal data."""
        database = FakeLeadReadDatabase(rows=read_rows())

        response = get_with_db(
            client,
            database,
            "/api/leads/11111111-1111-4111-8111-111111111111",
        )

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {
            "id",
            "source",
            "customer_name",
            "customer_email",
            "customer_phone",
            "message",
            "classification_status",
            "urgency",
            "summary",
            "classification_attempt_count",
            "created_at",
            "updated_at",
        }
        assert body["customer_email"] == "maria@example.com"
        assert body["customer_phone"] == "301-555-0144"
        assert body["message"] == "Hi, I need emergency plumbing today."
        assert body["updated_at"] == "2026-07-30T12:30:00Z"
        assert "idempotency_key" not in response.text
        assert "deduplication_bucket" not in response.text
        assert "classification_error" not in response.text
        assert "last_classification_error" not in response.text
        assert "test-service-role-key" not in response.text
        assert "worker-private" not in response.text

    @pytest.mark.unit
    def test_get_pending_lead_uses_created_at_as_updated_at(self, client: TestClient):
        """Test rows without classification timestamps still satisfy the public schema."""
        database = FakeLeadReadDatabase(rows=read_rows())

        response = get_with_db(
            client,
            database,
            "/api/leads/22222222-2222-4222-8222-222222222222",
        )

        assert response.status_code == 200
        body = response.json()
        assert body["classification_status"] == "pending"
        assert body["updated_at"] == "2026-07-29T12:00:00Z"

    @pytest.mark.unit
    def test_get_lead_by_id_returns_404_when_missing(self, client: TestClient):
        """Test missing lead detail returns a safe 404."""
        database = FakeLeadReadDatabase(rows=read_rows())

        response = get_with_db(
            client,
            database,
            "/api/leads/99999999-9999-4999-8999-999999999999",
        )

        assert response.status_code == 404
        assert response.json() == {"detail": "Lead not found"}

    @pytest.mark.unit
    @pytest.mark.parametrize("headers", [{}, {"X-Admin-Token": "wrong-token"}])
    def test_read_endpoints_require_admin_token(
        self,
        client: TestClient,
        headers: dict[str, str],
    ):
        """Test read endpoints reject missing or invalid admin tokens."""
        database = FakeLeadReadDatabase(rows=read_rows())

        response = get_with_db(client, database, "/api/leads", headers=headers)

        assert response.status_code == 403
        assert response.json() == {"detail": "Admin access required"}

    @pytest.mark.unit
    def test_read_endpoints_reject_access_when_admin_token_unconfigured(
        self,
        client: TestClient,
        monkeypatch,
    ):
        """Test read endpoints stay closed if the admin token is not configured."""
        monkeypatch.setattr("app.api.routes.leads.settings.queue_metrics_token", None)
        database = FakeLeadReadDatabase(rows=read_rows())

        response = get_with_db(client, database, "/api/leads")

        assert response.status_code == 403
        assert response.json() == {"detail": "Admin access required"}

    @pytest.mark.unit
    def test_list_leads_database_failure_is_safe(self, client: TestClient):
        """Test list database failures return safe errors without internals."""
        database = FakeLeadReadDatabase(
            fail=True,
            failure_message="database failed with test-service-role-key and SQL text",
        )

        response = get_with_db(client, database, "/api/leads")

        assert response.status_code == 503
        assert response.json() == {"detail": "Lead lookup failed"}
        assert "test-service-role-key" not in response.text
        assert "SQL text" not in response.text

    @pytest.mark.unit
    def test_detail_database_failure_is_safe(self, client: TestClient):
        """Test detail database failures return safe errors without internals."""
        database = FakeLeadReadDatabase(
            fail=True,
            failure_message="database failed with test-service-role-key and SQL text",
        )

        response = get_with_db(
            client,
            database,
            "/api/leads/11111111-1111-4111-8111-111111111111",
        )

        assert response.status_code == 503
        assert response.json() == {"detail": "Lead lookup failed"}
        assert "test-service-role-key" not in response.text
        assert "SQL text" not in response.text
