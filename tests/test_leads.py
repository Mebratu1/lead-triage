"""Tests for health, lead API contract, and idempotent persistence."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, TYPE_CHECKING
from uuid import UUID

import pytest

from app.db.client import get_db
from app.models.lead import LeadCreateRequest
from app.services.lead_persistence import (
    deduplication_bucket_for,
    generate_idempotency_key,
    persist_lead,
)
from app.services.rate_limiter import LeadIntakeRateLimiter

if TYPE_CHECKING:
    from starlette.testclient import TestClient


class FakeUniqueConstraintError(Exception):
    """Test double for a database unique constraint violation."""

    code = "23505"


class FakeLeadQuery:
    """Small Supabase query-builder test double."""

    def __init__(self, database: FakeLeadDatabase):
        self.database = database
        self.operation = "lookup"
        self.filters: dict[str, Any] = {}
        self.payload: dict[str, Any] | None = None

    def select(self, *args):
        return self

    def eq(self, field: str, value: Any):
        self.filters[field] = value
        return self

    def limit(self, *args):
        return self

    def insert(self, payload: dict[str, Any]):
        self.operation = "insert"
        self.payload = payload
        return self

    async def execute(self):
        if self.operation == "lookup":
            return self.database.execute_lookup(self.filters)

        if self.operation == "insert" and self.payload is not None:
            return self.database.execute_insert(self.payload)

        raise RuntimeError("unsupported fake query operation")


class FakeLeadDatabase:
    """In-memory stand-in for the Supabase leads table."""

    def __init__(
        self,
        records: list[dict[str, Any]] | None = None,
        fail_lookup: bool = False,
        fail_insert: bool = False,
        race_duplicate_on_insert: bool = False,
        empty_insert_response: bool = False,
        failure_message: str = "database failure",
    ):
        self.records_by_key = {
            self._storage_key(
                record["idempotency_key"],
                record["deduplication_bucket"],
            ): dict(record)
            for record in records or []
        }
        self.fail_lookup = fail_lookup
        self.fail_insert = fail_insert
        self.race_duplicate_on_insert = race_duplicate_on_insert
        self.empty_insert_response = empty_insert_response
        self.failure_message = failure_message
        self.lookup_count = 0
        self.insert_count = 0
        self.inserted_payloads: list[dict[str, Any]] = []

    def table(self, name: str):
        assert name == "leads"
        return FakeLeadQuery(self)

    def execute_lookup(self, filters: dict[str, Any]):
        self.lookup_count += 1
        if self.fail_lookup:
            raise RuntimeError(self.failure_message)

        key = self._storage_key(
            filters.get("idempotency_key"),
            filters.get("deduplication_bucket"),
        )
        record = self.records_by_key.get(key)
        return SimpleNamespace(data=[record] if record else [])

    def execute_insert(self, payload: dict[str, Any]):
        self.insert_count += 1
        if self.fail_insert:
            raise RuntimeError(self.failure_message)
        if self.empty_insert_response:
            return SimpleNamespace(data=[])

        key = self._storage_key(
            payload["idempotency_key"],
            payload["deduplication_bucket"],
        )
        if self.race_duplicate_on_insert:
            self.records_by_key[key] = self._build_row(
                payload,
                lead_id="33333333-3333-4333-8333-333333333333",
                created_at="2026-07-23T16:00:00+00:00",
            )
            self.race_duplicate_on_insert = False
            raise FakeUniqueConstraintError(
                'duplicate key value violates unique constraint '
                '"idx_leads_idempotency_bucket_unique"'
            )

        if key in self.records_by_key:
            raise FakeUniqueConstraintError(
                'duplicate key value violates unique constraint '
                '"idx_leads_idempotency_bucket_unique"'
            )

        row = self._build_row(
            payload,
            lead_id=f"11111111-1111-4111-8111-{self.insert_count:012d}",
            created_at=f"2026-07-23T16:00:{self.insert_count:02d}+00:00",
        )
        self.records_by_key[key] = row
        self.inserted_payloads.append(dict(payload))
        return SimpleNamespace(data=[row])

    @staticmethod
    def _storage_key(idempotency_key: str, deduplication_bucket: str):
        return idempotency_key, deduplication_bucket

    @staticmethod
    def _build_row(payload: dict[str, Any], lead_id: str, created_at: str):
        return {
            "id": lead_id,
            "idempotency_key": payload["idempotency_key"],
            "deduplication_bucket": payload["deduplication_bucket"],
            "source": payload["source"],
            "raw_message": payload["raw_message"],
            "classification_status": payload["classification_status"],
            "created_at": created_at,
        }


def existing_lead(
    message: str = "I need emergency plumbing service today.",
    source: str = "website",
    bucket: str = "2026-07-23",
    lead_id: str = "22222222-2222-4222-8222-222222222222",
) -> dict[str, Any]:
    return {
        "id": lead_id,
        "idempotency_key": generate_idempotency_key(source=source, message=message),
        "deduplication_bucket": bucket,
        "source": source,
        "raw_message": message,
        "classification_status": "pending",
        "created_at": "2026-07-23T15:00:00+00:00",
    }


def post_lead(
    client: TestClient,
    database: FakeLeadDatabase,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
):
    async def override_get_db():
        yield database

    client.app.dependency_overrides[get_db] = override_get_db
    try:
        return client.post("/api/leads", json=payload, headers=headers)
    finally:
        client.app.dependency_overrides.clear()


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

    @pytest.mark.unit
    def test_database_health_failure_does_not_log_raw_exception(
        self,
        client: TestClient,
        caplog,
    ):
        """Test database health logs only a safe exception category."""
        secret_text = "raw-service-role-key-and-customer-message"

        class FailingDatabase:
            def table(self, name: str):
                assert name == "leads"
                raise RuntimeError(secret_text)

        async def override_get_db():
            yield FailingDatabase()

        client.app.dependency_overrides[get_db] = override_get_db
        with caplog.at_level(logging.WARNING):
            try:
                response = client.get("/health/database")
            finally:
                client.app.dependency_overrides.clear()

        assert response.status_code == 503
        assert "error_type=RuntimeError" in caplog.text
        assert secret_text not in caplog.text


class TestLeadPersistenceContract:
    """Official lead inquiry persistence tests."""

    @pytest.mark.unit
    def test_create_lead_persists_valid_request_with_201(self, client: TestClient):
        """Test POST /api/leads inserts an unstructured inquiry."""
        database = FakeLeadDatabase()

        response = post_lead(
            client,
            database,
            {
                "source": "website",
                "message": "I need emergency plumbing service today.",
            },
        )

        body = response.json()
        assert response.status_code == 201
        assert UUID(body["id"])
        assert body == {
            "id": body["id"],
            "source": "website",
            "classification_status": "pending",
            "created_at": "2026-07-23T16:00:01Z",
            "duplicate": False,
        }
        assert database.insert_count == 1
        assert database.inserted_payloads == [
            {
                "idempotency_key": generate_idempotency_key(
                    source="website",
                    message="I need emergency plumbing service today.",
                ),
                "deduplication_bucket": deduplication_bucket_for().isoformat(),
                "source": "website",
                "raw_message": "I need emergency plumbing service today.",
                "classification_status": "pending",
            }
        ]

    @pytest.mark.unit
    def test_create_lead_returns_exact_duplicate_with_200(self, client: TestClient):
        """Test duplicate requests return the saved lead without reinserting."""
        bucket = deduplication_bucket_for().isoformat()
        existing = existing_lead(bucket=bucket)
        database = FakeLeadDatabase(records=[existing])

        response = post_lead(
            client,
            database,
            {
                "source": "website",
                "message": "I need emergency plumbing service today.",
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "id": existing["id"],
            "source": "website",
            "classification_status": "pending",
            "created_at": "2026-07-23T15:00:00Z",
            "duplicate": True,
        }
        assert database.insert_count == 0

    @pytest.mark.unit
    def test_create_lead_deduplicates_outer_whitespace(self, client: TestClient):
        """Test outer whitespace does not bypass idempotency."""
        database = FakeLeadDatabase()

        first_response = post_lead(
            client,
            database,
            {
                "source": "website",
                "message": "Please contact me about emergency plumbing.",
            },
        )
        second_response = post_lead(
            client,
            database,
            {
                "source": " website ",
                "message": "  Please contact me about emergency plumbing.  ",
            },
        )

        assert first_response.status_code == 201
        assert second_response.status_code == 200
        assert second_response.json()["duplicate"] is True
        assert second_response.json()["id"] == first_response.json()["id"]
        assert database.insert_count == 1

    @pytest.mark.unit
    def test_create_lead_deduplicates_repeated_internal_whitespace(
        self,
        client: TestClient,
    ):
        """Test repeated internal whitespace does not bypass idempotency."""
        database = FakeLeadDatabase()

        first_response = post_lead(
            client,
            database,
            {
                "source": "website",
                "message": "Please contact me about emergency plumbing.",
            },
        )
        second_response = post_lead(
            client,
            database,
            {
                "source": "website",
                "message": "Please   contact   me about emergency plumbing.",
            },
        )

        assert first_response.status_code == 201
        assert second_response.status_code == 200
        assert second_response.json()["id"] == first_response.json()["id"]
        assert database.insert_count == 1

    @pytest.mark.unit
    def test_create_lead_deduplicates_source_case(self, client: TestClient):
        """Test source case differences do not bypass idempotency."""
        database = FakeLeadDatabase()

        first_response = post_lead(
            client,
            database,
            {
                "source": "Website",
                "message": "Please contact me about emergency plumbing.",
            },
        )
        second_response = post_lead(
            client,
            database,
            {
                "source": "WEBSITE",
                "message": "Please contact me about emergency plumbing.",
            },
        )

        assert first_response.status_code == 201
        assert second_response.status_code == 200
        assert second_response.json()["id"] == first_response.json()["id"]
        assert database.insert_count == 1

    @pytest.mark.unit
    def test_meaningful_punctuation_differences_are_distinct(self, client: TestClient):
        """Test punctuation changes are not normalized away."""
        database = FakeLeadDatabase()

        first_response = post_lead(
            client,
            database,
            {"message": "Please call me about plumbing."},
        )
        second_response = post_lead(
            client,
            database,
            {"message": "Please call me about plumbing!"},
        )

        assert first_response.status_code == 201
        assert second_response.status_code == 201
        assert second_response.json()["id"] != first_response.json()["id"]
        assert database.insert_count == 2

    @pytest.mark.unit
    def test_meaningful_number_differences_are_distinct(self, client: TestClient):
        """Test number changes are not normalized away."""
        database = FakeLeadDatabase()

        first_response = post_lead(
            client,
            database,
            {"message": "Please call me at 301 555 0144."},
        )
        second_response = post_lead(
            client,
            database,
            {"message": "Please call me at 301 555 0145."},
        )

        assert first_response.status_code == 201
        assert second_response.status_code == 201
        assert second_response.json()["id"] != first_response.json()["id"]
        assert database.insert_count == 2

    @pytest.mark.unit
    def test_same_message_outside_deduplication_window_is_new(self):
        """Test configured buckets allow repeat messages after the window."""
        database = FakeLeadDatabase()
        request = LeadCreateRequest(
            source="website",
            message="Please contact me about emergency plumbing.",
        )

        first_response = asyncio.run(
            persist_lead(
                db=database,
                request=request,
                now=datetime(2026, 7, 23, 12, tzinfo=UTC),
            )
        )
        second_response = asyncio.run(
            persist_lead(
                db=database,
                request=request,
                now=datetime(2026, 7, 30, 12, tzinfo=UTC),
            )
        )

        assert first_response.duplicate is False
        assert second_response.duplicate is False
        assert second_response.id != first_response.id
        assert database.insert_count == 2

    @pytest.mark.unit
    def test_idempotency_key_uses_expected_sha256_policy(self):
        """Test exact normalization and hashing policy."""
        expected = hashlib.sha256(
            "website\nPlease contact me about emergency plumbing.".encode("utf-8")
        ).hexdigest()

        assert (
            generate_idempotency_key(
                source=" WEBSITE ",
                message="  Please   contact me about emergency plumbing.  ",
            )
            == expected
        )

    @pytest.mark.unit
    def test_create_lead_handles_database_insertion_failure_safely(
        self,
        client: TestClient,
    ):
        """Test insert failures return a safe service error."""
        database = FakeLeadDatabase(
            fail_insert=True,
            failure_message="insert failed with test-service-role-key and SQL details",
        )

        response = post_lead(
            client,
            database,
            {
                "source": "website",
                "message": "Please contact me about emergency plumbing.",
            },
        )

        assert response.status_code == 503
        assert response.json() == {"detail": "Lead persistence failed"}
        assert "test-service-role-key" not in response.text
        assert "SQL details" not in response.text

    @pytest.mark.unit
    def test_create_lead_handles_database_lookup_failure_safely(
        self,
        client: TestClient,
    ):
        """Test lookup failures return a safe service error."""
        database = FakeLeadDatabase(
            fail_lookup=True,
            failure_message="lookup failed with test-service-role-key and SQL details",
        )

        response = post_lead(
            client,
            database,
            {
                "source": "website",
                "message": "Please contact me about emergency plumbing.",
            },
        )

        assert response.status_code == 503
        assert response.json() == {"detail": "Lead persistence failed"}
        assert "test-service-role-key" not in response.text
        assert "SQL details" not in response.text

    @pytest.mark.unit
    def test_create_lead_handles_unexpected_insert_result_safely(
        self,
        client: TestClient,
    ):
        """Test unexpected persistence failures return a safe 500."""
        database = FakeLeadDatabase(empty_insert_response=True)

        response = post_lead(
            client,
            database,
            {
                "source": "website",
                "message": "Please contact me about emergency plumbing.",
            },
        )

        assert response.status_code == 500
        assert response.json() == {"detail": "Lead persistence failed"}

    @pytest.mark.unit
    def test_create_lead_handles_unique_constraint_race(self, client: TestClient):
        """Test a database unique constraint race returns the existing row."""
        database = FakeLeadDatabase(race_duplicate_on_insert=True)

        response = post_lead(
            client,
            database,
            {
                "source": "website",
                "message": "Please contact me about emergency plumbing.",
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "id": "33333333-3333-4333-8333-333333333333",
            "source": "website",
            "classification_status": "pending",
            "created_at": "2026-07-23T16:00:00Z",
            "duplicate": True,
        }
        assert database.lookup_count == 2
        assert database.insert_count == 1

    @pytest.mark.unit
    def test_create_lead_requires_message(self, client: TestClient):
        """Test missing message fails validation before database access."""
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
        database = FakeLeadDatabase()
        response = post_lead(
            client,
            database,
            {"message": "Please contact me about emergency plumbing."},
        )

        assert response.status_code == 201
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
    def test_create_lead_rate_limit_returns_429_without_database_write(
        self,
        client: TestClient,
    ):
        """Test the app-scoped limiter deterministically rejects intake abuse."""
        now = [100.0]
        client.app.state.lead_intake_rate_limiter = LeadIntakeRateLimiter(
            per_minute=2,
            per_hour=10,
            clock=lambda: now[0],
        )
        database = FakeLeadDatabase()

        first = post_lead(
            client,
            database,
            {"message": "Please contact me about plumbing request one."},
        )
        second = post_lead(
            client,
            database,
            {"message": "Please contact me about plumbing request two."},
        )
        limited = post_lead(
            client,
            database,
            {"message": "Please contact me about plumbing request three."},
        )

        assert first.status_code == 201
        assert second.status_code == 201
        assert limited.status_code == 429
        assert limited.json() == {"detail": "Lead intake rate limit exceeded"}
        assert limited.headers["retry-after"] == "60"
        assert database.insert_count == 2

    @pytest.mark.unit
    def test_create_lead_passes_forwarded_chain_to_client_ip_resolver(
        self,
        client: TestClient,
    ):
        """Test intake wiring supplies both peer and forwarding context."""
        calls: list[tuple[str | None, str | None]] = []

        class RecordingResolver:
            def resolve(
                self,
                *,
                peer_host: str | None,
                forwarded_for: str | None,
            ) -> str:
                calls.append((peer_host, forwarded_for))
                return "198.51.100.8"

        client.app.state.trusted_proxy_client_ip_resolver = RecordingResolver()
        client.app.state.lead_intake_rate_limiter = LeadIntakeRateLimiter(
            per_minute=1,
            per_hour=10,
        )
        database = FakeLeadDatabase()
        headers = {"X-Forwarded-For": "203.0.113.250, 198.51.100.8"}

        first = post_lead(
            client,
            database,
            {"message": "Please contact me about forwarded request one."},
            headers=headers,
        )
        limited = post_lead(
            client,
            database,
            {"message": "Please contact me about forwarded request two."},
            headers=headers,
        )

        assert first.status_code == 201
        assert limited.status_code == 429
        assert len(calls) == 2
        assert all(call[1] == headers["X-Forwarded-For"] for call in calls)

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


class TestSupabaseClientConfiguration:
    """Backend database client configuration tests."""

    @pytest.mark.unit
    def test_database_client_uses_service_role_key(self, monkeypatch):
        """Test backend Supabase access does not depend on the anon key."""
        import app.db.client as db_client

        captured: dict[str, str | None] = {}

        async def fake_create_client(supabase_url: str, supabase_key: str):
            captured["supabase_url"] = supabase_url
            captured["supabase_key"] = supabase_key
            return object()

        monkeypatch.setattr(db_client, "acreate_client", fake_create_client)
        monkeypatch.setattr(db_client.settings, "supabase_key", None)
        monkeypatch.setattr(
            db_client.settings,
            "supabase_service_role_key",
            "test-service-role-key",
        )
        db_client.SupabaseClient._instance = None

        try:
            asyncio.run(db_client.SupabaseClient.get_client())
        finally:
            db_client.SupabaseClient._instance = None

        assert captured["supabase_key"] == "test-service-role-key"
        assert captured["supabase_key"] != "test-anon-key"

    @pytest.mark.unit
    def test_database_client_initialization_failure_does_not_log_exception_text(
        self,
        monkeypatch,
        caplog,
    ):
        """Test initialization errors retain type context without secret text."""
        import app.db.client as db_client

        secret_text = "raw-service-role-key-and-project-url"

        async def failing_create_client(supabase_url: str, supabase_key: str):
            raise RuntimeError(secret_text)

        monkeypatch.setattr(db_client, "acreate_client", failing_create_client)
        db_client.SupabaseClient._instance = None
        try:
            with caplog.at_level(logging.ERROR):
                with pytest.raises(
                    db_client.SupabaseClientInitializationError,
                    match="Supabase client initialization failed",
                ):
                    asyncio.run(db_client.SupabaseClient.get_client())
        finally:
            db_client.SupabaseClient._instance = None

        assert "error_type=RuntimeError" in caplog.text
        assert secret_text not in caplog.text
