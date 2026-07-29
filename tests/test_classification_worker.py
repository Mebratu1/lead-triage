"""Tests for worker-safe lead classification orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.classification import (
    LeadClassificationPersistenceStatus,
    LeadClassificationStatus,
)
from app.repositories.lead_repository import (
    LeadRepositoryLookupError,
    claim_pending_leads,
    fetch_pending_leads,
)
from app.services.lead_classification_worker import (
    LeadClassificationBatchFetchFailed,
    process_pending_leads_batch,
)

COMPLETED_AT = datetime(2026, 7, 27, 16, 45, tzinfo=UTC)
COMPLETED_AT_ISO = COMPLETED_AT.isoformat()
NEXT_RETRY_AT_ISO = "2026-07-27T16:50:00+00:00"


class FakeWorkerQuery:
    """Small Supabase query-builder fake for select and update paths."""

    def __init__(self, database: FakeWorkerDatabase):
        self.database = database
        self.operation = "select"
        self.rpc_name: str | None = None
        self.rpc_params: dict[str, Any] = {}
        self.payload: dict[str, Any] | None = None
        self.filters: dict[str, Any] = {}
        self.limit_value: int | None = None
        self.order_field: str | None = None
        self.order_desc = False

    def select(self, *args):
        return self

    def update(self, payload: dict[str, Any]):
        self.operation = "update"
        self.payload = dict(payload)
        return self

    def eq(self, field: str, value: Any):
        self.filters[field] = value
        return self

    def order(self, field: str, desc: bool = False):
        self.order_field = field
        self.order_desc = desc
        return self

    def limit(self, limit: int):
        self.limit_value = limit
        return self

    async def execute(self):
        if self.operation == "rpc":
            return self.database.execute_rpc(self.rpc_name, self.rpc_params)

        if self.operation == "update":
            if self.payload is None:
                raise RuntimeError("missing update payload")
            return self.database.execute_update(self.payload, self.filters)

        return self.database.execute_select(
            filters=self.filters,
            order_field=self.order_field,
            order_desc=self.order_desc,
            limit=self.limit_value,
        )


class FakeWorkerDatabase:
    """In-memory stand-in for worker-facing lead persistence."""

    def __init__(
        self,
        rows: list[dict[str, Any]],
        fail_fetch: bool = False,
        invalid_fetch_response: bool = False,
        invalid_claim_response: bool = False,
        conflict_update_ids: set[str] | None = None,
        fail_update_ids: set[str] | None = None,
    ):
        self.rows = {row["id"]: dict(row) for row in rows}
        self.fail_fetch = fail_fetch
        self.invalid_fetch_response = invalid_fetch_response
        self.invalid_claim_response = invalid_claim_response
        self.conflict_update_ids = conflict_update_ids or set()
        self.fail_update_ids = fail_update_ids or set()
        self.select_count = 0
        self.claim_count = 0
        self.update_count = 0
        self.updated_payloads: dict[str, dict[str, Any]] = {}

    def table(self, name: str):
        assert name == "leads"
        return FakeWorkerQuery(self)

    def rpc(self, name: str, params: dict[str, Any]):
        query = FakeWorkerQuery(self)
        query.operation = "rpc"
        query.rpc_name = name
        query.rpc_params = dict(params)
        return query

    def execute_select(
        self,
        filters: dict[str, Any],
        order_field: str | None,
        order_desc: bool,
        limit: int | None,
    ):
        self.select_count += 1
        if self.fail_fetch:
            raise RuntimeError("fetch failed")
        if self.invalid_fetch_response:
            return SimpleNamespace(data="not a list")

        rows = [
            dict(row)
            for row in self.rows.values()
            if all(row.get(field) == value for field, value in filters.items())
        ]
        if order_field:
            rows.sort(
                key=lambda row: row.get(order_field),
                reverse=order_desc,
            )
        if limit is not None:
            rows = rows[:limit]
        return SimpleNamespace(data=rows)

    def execute_rpc(self, name: str | None, params: dict[str, Any]):
        assert name == "claim_pending_leads_for_classification"
        self.claim_count += 1
        if self.fail_fetch:
            raise RuntimeError("claim failed")
        if self.invalid_claim_response:
            return SimpleNamespace(data="not a list")

        limit = params["p_batch_limit"]
        worker_id = params["p_worker_id"]
        max_attempts = params["p_max_attempts"]
        stale_before = COMPLETED_AT - timedelta(
            seconds=params["p_claim_timeout_seconds"]
        )
        candidates = [
            row
            for row in self.rows.values()
            if row["classification_status"] == "pending"
            and row["classification_attempt_count"] < max_attempts
            and self._is_retry_available(row)
            and self._is_unclaimed_or_stale(row, stale_before)
        ]
        candidates.sort(key=lambda row: row["created_at"])
        claimed = []
        for row in candidates[:limit]:
            row["classification_claimed_at"] = COMPLETED_AT_ISO
            row["classification_claimed_by"] = worker_id
            row["classification_attempt_count"] += 1
            claimed.append(dict(row))
        return SimpleNamespace(data=claimed)

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _is_retry_available(self, row: dict[str, Any]) -> bool:
        next_attempt = row["next_classification_attempt_at"]
        return next_attempt is None or self._parse_timestamp(next_attempt) <= COMPLETED_AT

    def _is_unclaimed_or_stale(
        self,
        row: dict[str, Any],
        stale_before: datetime,
    ) -> bool:
        claimed_at = row["classification_claimed_at"]
        return claimed_at is None or self._parse_timestamp(claimed_at) < stale_before

    def execute_update(self, payload: dict[str, Any], filters: dict[str, Any]):
        self.update_count += 1
        lead_id = str(filters.get("id"))
        if lead_id in self.fail_update_ids:
            raise RuntimeError("update failed")
        if lead_id in self.conflict_update_ids:
            return SimpleNamespace(data=[])

        row = self.rows.get(lead_id)
        if row is None:
            return SimpleNamespace(data=[])
        if not all(row.get(field) == value for field, value in filters.items()):
            return SimpleNamespace(data=[])

        self.updated_payloads[lead_id] = dict(payload)
        row.update(payload)
        return SimpleNamespace(data=[dict(row)])


class FakeWorkerClassificationClient:
    """Mock classification client for worker tests."""

    model = "gpt-worker-test"

    def __init__(self, responses: list[str | Exception]):
        self.responses = list(responses)
        self.calls: list[str] = []

    async def classify(self, raw_message: str) -> str:
        self.calls.append(raw_message)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


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
    return json.dumps(payload)


def lead_row(
    lead_id: str,
    created_at: str,
    status: str = "pending",
    raw_message: str = "I need emergency plumbing service today.",
) -> dict[str, Any]:
    """Build a lead row for worker tests."""
    return {
        "id": lead_id,
        "source": "website",
        "raw_message": raw_message,
        "classification_status": status,
        "created_at": created_at,
        "customer_name": None,
        "email": None,
        "phone": None,
        "requested_service": None,
        "urgency": None,
        "lead_score": None,
        "ai_summary": None,
        "classification_error": None,
        "classified_at": None,
        "classification_model": None,
        "classification_claimed_at": None,
        "classification_claimed_by": None,
        "classification_attempt_count": 0,
        "last_classification_error": None,
        "next_classification_attempt_at": None,
    }


class TestFetchPendingLeads:
    """Repository tests for pending batch fetches."""

    @pytest.mark.unit
    def test_fetch_pending_leads_returns_oldest_pending_rows_with_limit(self):
        """Test pending lead fetch order and limit."""
        database = FakeWorkerDatabase(
            rows=[
                lead_row("lead-3", "2026-07-27T16:03:00+00:00"),
                lead_row("lead-1", "2026-07-27T16:01:00+00:00"),
                lead_row("done-1", "2026-07-27T16:00:00+00:00", status="classified"),
                lead_row("lead-2", "2026-07-27T16:02:00+00:00"),
            ]
        )

        rows = asyncio.run(fetch_pending_leads(db=database, limit=2))

        assert [row["id"] for row in rows] == ["lead-1", "lead-2"]

    @pytest.mark.unit
    def test_fetch_pending_leads_rejects_invalid_limit(self):
        """Test invalid batch sizes fail before database access."""
        database = FakeWorkerDatabase(rows=[])

        with pytest.raises(ValueError):
            asyncio.run(fetch_pending_leads(db=database, limit=0))

        assert database.select_count == 0

    @pytest.mark.unit
    def test_fetch_pending_leads_maps_unexpected_response_shape(self):
        """Test unexpected Supabase payloads are not treated as empty batches."""
        database = FakeWorkerDatabase(rows=[], invalid_fetch_response=True)

        with pytest.raises(LeadRepositoryLookupError):
            asyncio.run(fetch_pending_leads(db=database, limit=1))

    @pytest.mark.unit
    def test_claim_pending_leads_claims_oldest_available_rows(self):
        """Test claim RPC marks oldest eligible rows with worker ownership."""
        database = FakeWorkerDatabase(
            rows=[
                lead_row("lead-3", "2026-07-27T16:03:00+00:00"),
                lead_row("lead-1", "2026-07-27T16:01:00+00:00"),
                lead_row("lead-2", "2026-07-27T16:02:00+00:00"),
            ]
        )

        rows = asyncio.run(
            claim_pending_leads(
                db=database,
                limit=2,
                worker_id="worker-1",
                claim_timeout_seconds=900,
                max_attempts=5,
            )
        )

        assert [row["id"] for row in rows] == ["lead-1", "lead-2"]
        assert database.rows["lead-1"]["classification_claimed_by"] == "worker-1"
        assert database.rows["lead-1"]["classification_attempt_count"] == 1
        assert database.rows["lead-3"]["classification_claimed_by"] is None

    @pytest.mark.unit
    def test_claim_pending_leads_skips_backoff_claimed_and_exhausted_rows(self):
        """Test claim RPC only returns retry-eligible unclaimed rows."""
        database = FakeWorkerDatabase(
            rows=[
                lead_row("backoff", "2026-07-27T16:01:00+00:00"),
                lead_row("claimed", "2026-07-27T16:02:00+00:00"),
                lead_row("exhausted", "2026-07-27T16:03:00+00:00"),
                lead_row("ready", "2026-07-27T16:04:00+00:00"),
            ]
        )
        database.rows["backoff"]["next_classification_attempt_at"] = (
            "2026-07-27T17:00:00+00:00"
        )
        database.rows["claimed"]["classification_claimed_at"] = COMPLETED_AT_ISO
        database.rows["claimed"]["classification_claimed_by"] = "worker-2"
        database.rows["exhausted"]["classification_attempt_count"] = 5

        rows = asyncio.run(
            claim_pending_leads(
                db=database,
                limit=10,
                worker_id="worker-1",
                claim_timeout_seconds=900,
                max_attempts=5,
            )
        )

        assert [row["id"] for row in rows] == ["ready"]

    @pytest.mark.unit
    def test_claim_pending_leads_reclaims_stale_claims(self):
        """Test stale claims are eligible for a new worker."""
        database = FakeWorkerDatabase(
            rows=[
                lead_row("fresh", "2026-07-27T16:01:00+00:00"),
                lead_row("stale", "2026-07-27T16:02:00+00:00"),
            ]
        )
        database.rows["fresh"]["classification_claimed_at"] = COMPLETED_AT_ISO
        database.rows["fresh"]["classification_claimed_by"] = "worker-2"
        database.rows["stale"]["classification_claimed_at"] = (
            "2026-07-27T16:20:00+00:00"
        )
        database.rows["stale"]["classification_claimed_by"] = "worker-2"

        rows = asyncio.run(
            claim_pending_leads(
                db=database,
                limit=10,
                worker_id="worker-1",
                claim_timeout_seconds=900,
                max_attempts=5,
            )
        )

        assert [row["id"] for row in rows] == ["stale"]
        assert database.rows["stale"]["classification_claimed_by"] == "worker-1"
        assert database.rows["fresh"]["classification_claimed_by"] == "worker-2"

    @pytest.mark.unit
    def test_claim_pending_leads_maps_unexpected_response_shape(self):
        """Test malformed claim RPC responses fail safely."""
        database = FakeWorkerDatabase(rows=[], invalid_claim_response=True)

        with pytest.raises(LeadRepositoryLookupError):
            asyncio.run(
                claim_pending_leads(
                    db=database,
                    limit=1,
                    worker_id="worker-1",
                    claim_timeout_seconds=900,
                    max_attempts=5,
                )
            )


class TestLeadClassificationWorker:
    """Worker-safe batch orchestration tests."""

    @pytest.mark.integration
    def test_process_pending_batch_classifies_and_persists_rows(self):
        """Test worker flow classifies pending rows and saves metadata."""
        database = FakeWorkerDatabase(
            rows=[
                lead_row("lead-1", "2026-07-27T16:01:00+00:00"),
                lead_row("lead-2", "2026-07-27T16:02:00+00:00"),
            ]
        )
        client = FakeWorkerClassificationClient(
            [
                valid_payload(customer_name="Jane Doe", lead_score=92),
                valid_payload(customer_name="John Smith", lead_score=75),
            ]
        )

        result = asyncio.run(
            process_pending_leads_batch(
                db=database,
                client=client,
                limit=2,
                classified_at=COMPLETED_AT,
                worker_id="worker-1",
            )
        )

        assert result.fetched == 2
        assert result.saved == 2
        assert result.classified == 2
        assert result.failed == 0
        assert database.rows["lead-1"]["classification_status"] == "classified"
        assert database.rows["lead-1"]["customer_name"] == "Jane Doe"
        assert database.rows["lead-1"]["classification_error"] is None
        assert database.rows["lead-1"]["classified_at"] == COMPLETED_AT_ISO
        assert database.rows["lead-1"]["classification_model"] == "gpt-worker-test"
        assert database.rows["lead-1"]["classification_claimed_by"] is None
        assert database.rows["lead-1"]["last_classification_error"] is None
        assert database.rows["lead-2"]["lead_score"] == 75
        assert len(client.calls) == 2

    @pytest.mark.integration
    def test_process_pending_batch_persists_parser_failure(self):
        """Test malformed model output becomes a failed lead update."""
        database = FakeWorkerDatabase(
            rows=[lead_row("lead-1", "2026-07-27T16:01:00+00:00")]
        )
        client = FakeWorkerClassificationClient(["{not valid json"])

        result = asyncio.run(
            process_pending_leads_batch(
                db=database,
                client=client,
                limit=1,
                classification_model="explicit-model",
                classified_at=COMPLETED_AT,
                worker_id="worker-1",
            )
        )

        assert result.fetched == 1
        assert result.saved == 1
        assert result.classified == 0
        assert result.failed == 1
        assert result.results[0].classification_status == LeadClassificationStatus.FAILED
        assert result.results[0].error_reason == "invalid_json"
        assert database.rows["lead-1"]["classification_status"] == "failed"
        assert database.rows["lead-1"]["classification_error"] == "invalid_json"
        assert database.rows["lead-1"]["ai_summary"] is None
        assert database.rows["lead-1"]["classification_model"] == "explicit-model"
        assert database.rows["lead-1"]["classification_claimed_by"] is None
        assert database.rows["lead-1"]["last_classification_error"] == "invalid_json"

    @pytest.mark.integration
    def test_process_pending_batch_keeps_client_exception_pending(self, caplog):
        """Test OpenAI/client failures do not burn pending leads."""
        raw_message = "Private customer text 301-555-0144"
        database = FakeWorkerDatabase(
            rows=[
                lead_row(
                    "lead-1",
                    "2026-07-27T16:01:00+00:00",
                    raw_message=raw_message,
                )
            ]
        )
        client = FakeWorkerClassificationClient([RuntimeError("network outage")])

        with caplog.at_level(logging.INFO):
            result = asyncio.run(
                process_pending_leads_batch(
                    db=database,
                    client=client,
                    limit=1,
                    classified_at=COMPLETED_AT,
                    worker_id="worker-1",
                )
            )

        assert result.saved == 0
        assert result.failed == 0
        assert result.errors == 1
        assert result.results[0].persistence_status == (
            LeadClassificationPersistenceStatus.ERROR
        )
        assert result.results[0].classification_status is None
        assert result.results[0].error_reason == "classification_client_error"
        assert database.rows["lead-1"]["classification_status"] == "pending"
        assert database.rows["lead-1"]["classification_error"] is None
        assert database.rows["lead-1"]["classification_claimed_by"] is None
        assert database.rows["lead-1"]["last_classification_error"] == (
            "classification_client_error"
        )
        assert database.rows["lead-1"]["next_classification_attempt_at"] == (
            NEXT_RETRY_AT_ISO
        )
        assert database.update_count == 1
        assert "retry_after_seconds=300" in caplog.text
        assert raw_message not in caplog.text
        assert "301-555-0144" not in caplog.text

    @pytest.mark.integration
    def test_process_pending_batch_skips_non_pending_update_conflict(self):
        """Test duplicate workers do not overwrite already-claimed rows."""
        database = FakeWorkerDatabase(
            rows=[lead_row("lead-1", "2026-07-27T16:01:00+00:00")],
            conflict_update_ids={"lead-1"},
        )
        client = FakeWorkerClassificationClient([valid_payload()])

        result = asyncio.run(
            process_pending_leads_batch(
                db=database,
                client=client,
                limit=1,
                classified_at=COMPLETED_AT,
                worker_id="worker-1",
            )
        )

        assert result.saved == 0
        assert result.skipped == 1
        assert result.results[0].persistence_status == (
            LeadClassificationPersistenceStatus.SKIPPED
        )
        assert database.rows["lead-1"]["classification_status"] == "pending"
        assert database.rows["lead-1"]["classification_claimed_by"] == "worker-1"

    @pytest.mark.integration
    def test_process_pending_batch_continues_after_update_error(self):
        """Test one persistence error does not stop the rest of the batch."""
        database = FakeWorkerDatabase(
            rows=[
                lead_row("lead-1", "2026-07-27T16:01:00+00:00"),
                lead_row("lead-2", "2026-07-27T16:02:00+00:00"),
            ],
            fail_update_ids={"lead-1"},
        )
        client = FakeWorkerClassificationClient([valid_payload(), valid_payload()])

        result = asyncio.run(
            process_pending_leads_batch(
                db=database,
                client=client,
                limit=2,
                classified_at=COMPLETED_AT,
                worker_id="worker-1",
            )
        )

        assert result.fetched == 2
        assert result.saved == 1
        assert result.classified == 1
        assert result.errors == 1
        assert database.rows["lead-1"]["classification_status"] == "pending"
        assert database.rows["lead-1"]["classification_claimed_by"] == "worker-1"
        assert database.rows["lead-2"]["classification_status"] == "classified"

    @pytest.mark.integration
    def test_process_pending_batch_maps_fetch_failure(self):
        """Test fetch failures surface as controlled batch errors."""
        database = FakeWorkerDatabase(rows=[], fail_fetch=True)
        client = FakeWorkerClassificationClient([])

        with pytest.raises(LeadClassificationBatchFetchFailed):
            asyncio.run(
                process_pending_leads_batch(
                    db=database,
                    client=client,
                    limit=1,
                    classified_at=COMPLETED_AT,
                    worker_id="worker-1",
                )
            )

    @pytest.mark.unit
    def test_process_pending_batch_rejects_invalid_limits(self):
        """Test worker validates batch size before querying."""
        database = FakeWorkerDatabase(rows=[])
        client = FakeWorkerClassificationClient([])

        with pytest.raises(ValueError):
            asyncio.run(process_pending_leads_batch(db=database, client=client, limit=0))

        with pytest.raises(ValueError):
            asyncio.run(
                process_pending_leads_batch(db=database, client=client, limit=101)
            )

        assert database.claim_count == 0
