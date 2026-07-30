"""Tests for concurrent-safe CRM retry processing."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.integration import CrmSyncOutcome
from app.models.lead import LeadIntegrationStatus
from app.repositories.lead_repository import (
    claim_due_leads_for_crm_sync,
    count_due_leads_for_crm_sync,
)
from app.services.crm_sync import (
    CrmDeliveryPermanentError,
    CrmDeliveryRetryableError,
)
from app.services.crm_sync_worker import (
    exponential_retry_delay,
    process_due_crm_sync_batch,
)


def claimed_row(retry_attempt_count: int = 1) -> dict[str, Any]:
    """Build one row returned by the atomic CRM claim RPC."""
    return {
        "id": "11111111-1111-4111-8111-111111111111",
        "source": "website",
        "raw_message": "I need emergency plumbing today.",
        "customer_name": "Maria Customer",
        "email": "maria@example.com",
        "phone": "301-555-0144",
        "classification_status": "classified",
        "urgency": "hot",
        "ai_summary": "Customer needs plumbing help.",
        "classification_attempt_count": 1,
        "created_at": "2026-07-30T12:00:00+00:00",
        "classified_at": "2026-07-30T12:05:00+00:00",
        "integration_status": "failed",
        "integration_last_synced_at": None,
        "integration_next_attempt_at": "2026-07-30T12:10:00+00:00",
        "integration_retry_attempt_count": retry_attempt_count,
        "integration_claimed_at": "2026-07-30T12:11:00+00:00",
        "integration_claimed_by": "worker-1",
    }


class FakeDispatcher:
    """Dispatcher with a configured success or failure."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.lead_ids: list[str] = []

    async def sync_lead(self, lead) -> None:
        self.lead_ids.append(str(lead.id))
        if self.failure is not None:
            raise self.failure


async def run_worker_case(
    monkeypatch,
    *,
    row: dict[str, Any],
    dispatcher: FakeDispatcher,
    max_attempts: int = 5,
) -> tuple[Any, list[dict[str, Any]]]:
    updates: list[dict[str, Any]] = []

    async def claim(**kwargs):
        return [row]

    async def update(**kwargs):
        updates.append(kwargs)
        return {"id": kwargs["lead_id"]}

    monkeypatch.setattr(
        "app.services.crm_sync_worker.claim_due_leads_for_crm_sync",
        claim,
    )
    monkeypatch.setattr(
        "app.services.crm_sync_worker.update_lead_integration_status",
        update,
    )
    result = await process_due_crm_sync_batch(
        db=object(),
        dispatcher=dispatcher,
        worker_id="worker-1",
        retry_base_seconds=60,
        retry_max_seconds=600,
        max_attempts=max_attempts,
        completed_at=datetime(2026, 7, 30, 12, 15, tzinfo=UTC),
    )
    return result, updates


class TestCrmSyncWorker:
    """Retry scheduling and terminal-state tests."""

    @pytest.mark.unit
    def test_success_marks_claimed_lead_synced(self, monkeypatch):
        """Test a successful retry clears the claim through an owned update."""
        dispatcher = FakeDispatcher()
        result, updates = asyncio.run(
            run_worker_case(
                monkeypatch,
                row=claimed_row(),
                dispatcher=dispatcher,
            )
        )

        assert result.synced == 1
        assert result.results[0].outcome == CrmSyncOutcome.SYNCED
        assert updates[0]["integration_status"] == LeadIntegrationStatus.SYNCED
        assert updates[0]["claim_owner_id"] == "worker-1"
        assert dispatcher.lead_ids == ["11111111-1111-4111-8111-111111111111"]

    @pytest.mark.unit
    def test_retryable_failure_uses_capped_exponential_backoff(self, monkeypatch):
        """Test retry attempt one schedules 2x base after the initial delay."""
        dispatcher = FakeDispatcher(
            CrmDeliveryRetryableError("crm_retryable_response", status_code=503)
        )
        result, updates = asyncio.run(
            run_worker_case(
                monkeypatch,
                row=claimed_row(retry_attempt_count=1),
                dispatcher=dispatcher,
            )
        )

        assert result.retry_scheduled == 1
        assert result.results[0].retry_after_seconds == 120
        assert updates[0]["error_reason"] == "crm_retryable_failure"
        assert updates[0]["retry_after_seconds"] == 120
        assert updates[0].get("reset_retry_attempts", False) is False

    @pytest.mark.unit
    def test_retryable_failure_stops_after_max_attempts(self, monkeypatch):
        """Test the final retry becomes exhausted without another due timestamp."""
        dispatcher = FakeDispatcher(
            CrmDeliveryRetryableError("crm_retryable_response", status_code=429)
        )
        result, updates = asyncio.run(
            run_worker_case(
                monkeypatch,
                row=claimed_row(retry_attempt_count=3),
                dispatcher=dispatcher,
                max_attempts=3,
            )
        )

        assert result.exhausted == 1
        assert result.results[0].outcome == CrmSyncOutcome.EXHAUSTED
        assert updates[0]["error_reason"] == "crm_retry_exhausted"
        assert updates[0]["retry_after_seconds"] is None

    @pytest.mark.unit
    def test_permanent_failure_is_not_rescheduled(self, monkeypatch):
        """Test non-429 4xx failures remain visible but leave the retry queue."""
        dispatcher = FakeDispatcher(
            CrmDeliveryPermanentError("crm_permanent_response", status_code=422)
        )
        result, updates = asyncio.run(
            run_worker_case(
                monkeypatch,
                row=claimed_row(),
                dispatcher=dispatcher,
            )
        )

        assert result.permanent_failed == 1
        assert updates[0]["error_reason"] == "crm_permanent_failure"
        assert updates[0]["retry_after_seconds"] is None

    @pytest.mark.unit
    def test_exponential_delay_is_capped(self):
        """Test large retry counts cannot exceed the configured cap."""
        assert (
            exponential_retry_delay(
                retry_attempt_count=10,
                base_seconds=60,
                max_seconds=600,
            )
            == 600
        )


class FakeRpcQuery:
    """Small RPC query fake used to inspect claim parameters."""

    def select(self, fields: str):
        self.fields = fields
        return self

    async def execute(self):
        return SimpleNamespace(data=[claimed_row()])


class FakeRpcDatabase:
    """Capture calls to the atomic CRM claim RPC."""

    def __init__(self) -> None:
        self.name = ""
        self.params: dict[str, Any] = {}
        self.query = FakeRpcQuery()

    def rpc(self, name: str, params: dict[str, Any]):
        self.name = name
        self.params = params
        return self.query


class FakeCountQuery:
    """Capture the due-retry count filters."""

    def __init__(self) -> None:
        self.filters: dict[str, Any] = {}
        self.count_requested = False
        self.limit_value: int | None = None

    def select(self, fields: str, count: str | None = None):
        self.count_requested = count == "exact"
        return self

    def eq(self, field: str, value: Any):
        self.filters[f"eq:{field}"] = value
        return self

    def lte(self, field: str, value: Any):
        self.filters[f"lte:{field}"] = value
        return self

    def lt(self, field: str, value: Any):
        self.filters[f"lt:{field}"] = value
        return self

    def limit(self, value: int):
        self.limit_value = value
        return self

    async def execute(self):
        return SimpleNamespace(data=[], count=3)


class FakeCountDatabase:
    """Return a query fake for integration queue metrics."""

    def __init__(self) -> None:
        self.query = FakeCountQuery()

    def table(self, name: str):
        assert name == "leads"
        return self.query


@pytest.mark.unit
def test_repository_claims_due_retries_through_atomic_rpc():
    """Test worker claiming uses the migration RPC and concurrency controls."""
    database = FakeRpcDatabase()

    rows = asyncio.run(
        claim_due_leads_for_crm_sync(
            db=database,
            limit=7,
            worker_id="worker-7",
            claim_timeout_seconds=90,
            max_attempts=4,
        )
    )

    assert len(rows) == 1
    assert database.name == "claim_due_leads_for_crm_sync"
    assert database.params == {
        "p_batch_limit": 7,
        "p_worker_id": "worker-7",
        "p_claim_timeout_seconds": 90,
        "p_max_attempts": 4,
    }
    assert "integration_retry_attempt_count" in database.query.fields


@pytest.mark.unit
def test_repository_counts_only_due_non_exhausted_retries():
    """Test CRM queue health uses due time and retry-attempt filters."""
    database = FakeCountDatabase()
    now = datetime(2026, 7, 30, 12, tzinfo=UTC)

    count = asyncio.run(
        count_due_leads_for_crm_sync(
            db=database,
            max_attempts=5,
            now=now,
        )
    )

    assert count == 3
    assert database.query.count_requested is True
    assert database.query.limit_value == 0
    assert database.query.filters == {
        "eq:integration_status": "failed",
        "lte:integration_next_attempt_at": now.isoformat(),
        "lt:integration_retry_attempt_count": 5,
    }


@pytest.mark.unit
def test_crm_retry_migration_uses_skip_locked_and_due_timestamp():
    """Test migration provides atomic due-row selection and retry accounting."""
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "db"
        / "migrations"
        / "007_crm_retry_claiming.sql"
    ).read_text(encoding="utf-8")

    assert "FOR UPDATE SKIP LOCKED" in migration
    assert "integration_next_attempt_at <= NOW()" in migration
    assert "integration_retry_attempt_count + 1" in migration
    assert "classification_status = 'classified'" in migration
    assert "integration_retry_attempt_count >= p_max_attempts" in migration
    assert "integration_error = 'crm_retry_exhausted'" in migration
    assert "FROM PUBLIC" in migration
