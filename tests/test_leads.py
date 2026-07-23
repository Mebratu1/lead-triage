"""Tests for health, lead API contract, and idempotent persistence."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, TYPE_CHECKING
from uuid import UUID

import pytest

from app.db.client import get_db
from app.models.lead import LeadCreateRequest
from app.services.lead_persistence import generate_idempotency_key

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
        failure_message: str = "database failure",
    ):
        self.records_by_key = {
            record["idempotency_key"]: dict(record) for record in records or []
        }
        self.fail_lookup = fail_lookup
        self.fail_insert = fail_insert
        self.race_duplicate_on_insert = race_duplicate_on_insert
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

        idempotency_key = filters.get("idempotency_key")
        record = self.records_by_key.get(idempotency_key)
        return SimpleNamespace(data=[record] if record else [])

    def execute_insert(self, payload: dict[str, Any]):
        self.insert_count += 1
        if self.fail_insert:
            raise RuntimeError(self.failure_message)

        idempotency_key = payload["idempotency_key"]
        if self.race_duplicate_on_insert:
            self.records_by_key[idempotency_key] = self._build_row(
                payload,
                lead_id="33333333-3333-4333-8333-333333333333",
            )
            self.race_duplicate_on_insert = False
            raise FakeUniqueConstraintError(
                'duplicate key value violates unique constraint "leads_idempotency_key_key"'
            )

        if idempotency_key in self.records_by_key:
            raise FakeUniqueConstraintError(
                'duplicate key value violates unique constraint "leads_idempotency_key_key"'
            )

        row = self._build_row(
            payload,
            lead_id=f"11111111-1111-4111-8111-{self.insert_count:012d}",
        )
        self.records_by_key[idempotency_key] = row
        self.inserted_payloads.append(dict(payload))
        return SimpleNamespace(data=[row])

    @staticmethod
    def _build_row(payload: dict[str, Any], lead_id: str):
        return {
            "id": lead_id,
            "idempotency_key": payload["idempotency_key"],
            "source": payload["source"],
            "raw_message": payload["raw_message"],
            "classification_status": payload["classification_status"],
        }


def existing_lead(
    message: str = "I need emergency plumbing service today.",
    source: str = "website",
    lead_id: str = "22222222-2222-4222-8222-222222222222",
) -> dict[str, Any]:
    return {
        "id": lead_id,
        "idempotency_key": generate_idempotency_key(source=source, message=message),
        "source": source,
        "raw_message": message,
        "classification_status": "pending",
    }


def post_lead(client: TestClient, database: FakeLeadDatabase, payload: dict[str, Any]):
    async def override_get_db():
        yield database

    client.app.dependency_overrides[get_db] = override_get_db
    try:
        return client.post("/api/leads", json=payload)
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


class TestLeadPersistenceContract:
    """Official lead inquiry persistence tests."""

    @pytest.mark.unit
    def test_create_lead_persists_valid_request(self, client: TestClient):
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
        assert response.status_code == 202
        assert UUID(body["id"])
        assert body == {
            "status": "accepted",
            "id": body["id"],
            "source": "website",
            "classification_status": "pending",
            "persistence_status": "created",
        }
        assert database.insert_count == 1
        assert database.inserted_payloads == [
            {
                "idempotency_key": generate_idempotency_key(
                    source="website",
                    message="I need emergency plumbing service today.",
                ),
                "source": "website",
                "raw_message": "I need emergency plumbing service today.",
                "classification_status": "pending",
            }
        ]

    @pytest.mark.unit
    def test_create_lead_returns_existing_duplicate(self, client: TestClient):
        """Test duplicate requests return the saved lead without reinserting."""
        existing = existing_lead()
        database = FakeLeadDatabase(records=[existing])

        response = post_lead(
            client,
            database,
            {
                "source": "website",
                "message": "I need emergency plumbing service today.",
            },
        )

        assert response.status_code == 202
        assert response.json() == {
            "status": "accepted",
            "id": existing["id"],
            "source": "website",
            "classification_status": "pending",
            "persistence_status": "deduplicated",
        }
        assert database.insert_count == 0

    @pytest.mark.unit
    def test_create_lead_deduplicates_normalized_request(self, client: TestClient):
        """Test source casing and message spacing do not bypass idempotency."""
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
                "source": " WEBSITE ",
                "message": "  Please   contact me about emergency plumbing.  ",
            },
        )

        assert first_response.status_code == 202
        assert second_response.status_code == 202
        assert first_response.json()["persistence_status"] == "created"
        assert second_response.json()["persistence_status"] == "deduplicated"
        assert second_response.json()["id"] == first_response.json()["id"]
        assert database.insert_count == 1

    @pytest.mark.unit
    def test_create_lead_handles_database_insertion_failure(self, client: TestClient):
        """Test insert failures return a safe service error."""
        database = FakeLeadDatabase(
            fail_insert=True,
            failure_message="insert failed with test-service-role-key",
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

    @pytest.mark.unit
    def test_create_lead_handles_database_lookup_failure(self, client: TestClient):
        """Test lookup failures return a safe service error."""
        database = FakeLeadDatabase(
            fail_lookup=True,
            failure_message="lookup failed with test-service-role-key",
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

        assert response.status_code == 202
        assert response.json() == {
            "status": "accepted",
            "id": "33333333-3333-4333-8333-333333333333",
            "source": "website",
            "classification_status": "pending",
            "persistence_status": "deduplicated",
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


class TestSupabaseClientConfiguration:
    """Backend database client configuration tests."""

    @pytest.mark.unit
    def test_database_client_uses_service_role_key(self, monkeypatch):
        """Test backend Supabase access does not depend on the anon key."""
        import app.db.client as db_client

        captured: dict[str, str] = {}

        async def fake_create_client(supabase_url: str, supabase_key: str):
            captured["supabase_url"] = supabase_url
            captured["supabase_key"] = supabase_key
            return object()

        monkeypatch.setattr(db_client, "acreate_client", fake_create_client)
        monkeypatch.setattr(db_client.settings, "supabase_key", "test-anon-key")
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
