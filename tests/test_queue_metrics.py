"""Tests for classification queue observability."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.db.client import get_db
from app.jobs.classification_daemon import DaemonSettings, run_daemon
from app.models.classification import (
    LeadClassificationBatchResult,
    LeadClassificationQueueMetrics,
)
from app.repositories.lead_repository import (
    LeadRepositoryLookupError,
    fetch_classification_queue_metrics,
)
from app.services.lead_persistence import get_classification_queue_metrics

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
TOKEN = "monitoring-token-with-enough-length"


class FakeQueueQuery:
    """Small Supabase query-builder fake for queue metrics count queries."""

    def __init__(self, database: FakeQueueDatabase):
        self.database = database
        self.eq_filters: dict[str, Any] = {}
        self.gt_filters: dict[str, Any] = {}
        self.gte_filters: dict[str, Any] = {}
        self.lt_filters: dict[str, Any] = {}

    def select(self, *args, **kwargs):
        return self

    def eq(self, field: str, value: Any):
        self.eq_filters[field] = value
        return self

    def gt(self, field: str, value: Any):
        self.gt_filters[field] = value
        return self

    def gte(self, field: str, value: Any):
        self.gte_filters[field] = value
        return self

    def lt(self, field: str, value: Any):
        self.lt_filters[field] = value
        return self

    def limit(self, *args):
        return self

    async def execute(self):
        return self.database.execute_count(
            eq_filters=self.eq_filters,
            gt_filters=self.gt_filters,
            gte_filters=self.gte_filters,
            lt_filters=self.lt_filters,
        )


class FakeQueueDatabase:
    """In-memory stand-in for aggregate queue-health queries."""

    def __init__(
        self,
        rows: list[dict[str, Any]],
        fail: bool = False,
        invalid_response: bool = False,
    ):
        self.rows = [dict(row) for row in rows]
        self.fail = fail
        self.invalid_response = invalid_response
        self.count_queries = 0

    def table(self, name: str):
        assert name == "leads"
        return FakeQueueQuery(self)

    def execute_count(
        self,
        eq_filters: dict[str, Any],
        gt_filters: dict[str, Any],
        gte_filters: dict[str, Any],
        lt_filters: dict[str, Any],
    ):
        self.count_queries += 1
        if self.fail:
            raise RuntimeError("raw private customer message 301-555-0144")
        if self.invalid_response:
            return SimpleNamespace(data="not-countable")

        count = 0
        for row in self.rows:
            if not all(row.get(field) == value for field, value in eq_filters.items()):
                continue
            if not all(_gt(row.get(field), value) for field, value in gt_filters.items()):
                continue
            if not all(row.get(field, 0) >= value for field, value in gte_filters.items()):
                continue
            if not all(row.get(field, 0) < value for field, value in lt_filters.items()):
                continue
            count += 1

        return SimpleNamespace(data=[], count=count)


def _gt(left: Any, right: Any) -> bool:
    if left is None:
        return False
    if isinstance(left, str) and isinstance(right, str):
        left_timestamp = datetime.fromisoformat(left.replace("Z", "+00:00"))
        right_timestamp = datetime.fromisoformat(right.replace("Z", "+00:00"))
        return left_timestamp > right_timestamp
    return left > right


def queue_row(
    lead_id: str,
    status: str = "pending",
    attempts: int = 0,
    next_attempt_at: str | None = None,
) -> dict[str, Any]:
    """Build a row shaped for queue metrics queries without raw lead text."""
    return {
        "id": lead_id,
        "classification_status": status,
        "classification_attempt_count": attempts,
        "next_classification_attempt_at": next_attempt_at,
    }


def queue_rows() -> list[dict[str, Any]]:
    """Build representative queue rows."""
    return [
        queue_row("pending-1"),
        queue_row("backoff-1", attempts=1, next_attempt_at="2099-07-30T12:05:00+00:00"),
        queue_row("exhausted-1", attempts=5),
        queue_row(
            "exhausted-backoff-1",
            attempts=6,
            next_attempt_at="2099-07-30T12:10:00+00:00",
        ),
        queue_row("classified-1", status="classified", attempts=5),
    ]


def db_override(database: FakeQueueDatabase):
    async def override_get_db():
        yield database

    return override_get_db


class TestQueueMetricsRepository:
    """Repository and service tests for aggregate queue metrics."""

    @pytest.mark.unit
    def test_fetch_classification_queue_metrics_counts_pending_backoff_and_exhausted(self):
        """Test queue counters are calculated from aggregate database counts."""
        database = FakeQueueDatabase(rows=queue_rows())

        metrics = asyncio.run(
            fetch_classification_queue_metrics(
                db=database,
                max_attempts=5,
                now=NOW,
            )
        )

        assert metrics == LeadClassificationQueueMetrics(
            pending_count=4,
            backoff_count=1,
            exhausted_count=2,
            max_attempts=5,
        )
        assert database.count_queries == 3

    @pytest.mark.unit
    def test_backoff_count_excludes_exhausted_pending_leads(self):
        """Test retry backoff only counts leads that still have attempts left."""
        database = FakeQueueDatabase(
            rows=[
                queue_row(
                    "retryable-backoff",
                    attempts=4,
                    next_attempt_at="2099-07-30T12:05:00+00:00",
                ),
                queue_row(
                    "exhausted-backoff",
                    attempts=5,
                    next_attempt_at="2099-07-30T12:05:00+00:00",
                ),
            ]
        )

        metrics = asyncio.run(
            fetch_classification_queue_metrics(
                db=database,
                max_attempts=5,
                now=NOW,
            )
        )

        assert metrics == LeadClassificationQueueMetrics(
            pending_count=2,
            backoff_count=1,
            exhausted_count=1,
            max_attempts=5,
        )

    @pytest.mark.unit
    def test_fetch_classification_queue_metrics_rejects_invalid_max_attempts(self):
        """Test invalid exhausted-threshold values fail before database access."""
        database = FakeQueueDatabase(rows=[])

        with pytest.raises(ValueError, match="max_attempts must be at least 1"):
            asyncio.run(
                fetch_classification_queue_metrics(
                    db=database,
                    max_attempts=0,
                    now=NOW,
                )
            )

    @pytest.mark.unit
    def test_fetch_classification_queue_metrics_maps_database_failures(self):
        """Test count failures become controlled lookup errors."""
        database = FakeQueueDatabase(rows=queue_rows(), fail=True)

        with pytest.raises(LeadRepositoryLookupError):
            asyncio.run(
                fetch_classification_queue_metrics(
                    db=database,
                    max_attempts=5,
                    now=NOW,
                )
            )

    @pytest.mark.unit
    def test_service_wrapper_returns_queue_metrics(self):
        """Test service wrapper exposes queue metrics without repository details."""
        database = FakeQueueDatabase(rows=queue_rows())

        metrics = asyncio.run(
            get_classification_queue_metrics(
                db=database,
                max_attempts=5,
                now=NOW,
            )
        )

        assert metrics.pending_count == 4
        assert metrics.backoff_count == 1
        assert metrics.exhausted_count == 2


class TestQueueHealthEndpoint:
    """API tests for protected queue health metrics."""

    @pytest.mark.unit
    def test_queue_health_returns_aggregate_counts_without_token_when_unconfigured(
        self,
        client,
        monkeypatch,
    ):
        """Test development queue health can expose aggregate counters without a token."""
        monkeypatch.setattr("app.routes.health.settings.queue_metrics_token", None)
        database = FakeQueueDatabase(rows=queue_rows())
        client.app.dependency_overrides[get_db] = db_override(database)
        try:
            response = client.get("/health/queue")
        finally:
            client.app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "pending_count": 4,
            "backoff_count": 1,
            "exhausted_count": 2,
            "max_attempts": 5,
        }

    @pytest.mark.unit
    def test_queue_health_requires_bearer_token_when_configured(self, client, monkeypatch):
        """Test configured queue metrics token protects the endpoint."""
        monkeypatch.setattr("app.routes.health.settings.queue_metrics_token", TOKEN)
        database = FakeQueueDatabase(rows=queue_rows())
        client.app.dependency_overrides[get_db] = db_override(database)
        try:
            response = client.get("/health/queue")
        finally:
            client.app.dependency_overrides.clear()

        assert response.status_code == 401
        assert response.json() == {"detail": "Queue health authorization required"}

    @pytest.mark.unit
    def test_queue_health_accepts_valid_bearer_token(self, client, monkeypatch):
        """Test authorized monitoring requests can read aggregate counters."""
        monkeypatch.setattr("app.routes.health.settings.queue_metrics_token", TOKEN)
        database = FakeQueueDatabase(rows=queue_rows())
        client.app.dependency_overrides[get_db] = db_override(database)
        try:
            response = client.get(
                "/health/queue",
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
        finally:
            client.app.dependency_overrides.clear()

        assert response.status_code == 200
        payload = response.json()
        assert payload["pending_count"] == 4
        assert payload["backoff_count"] >= 0
        assert payload["exhausted_count"] == 2

    @pytest.mark.unit
    def test_queue_health_accepts_valid_admin_token_header(self, client, monkeypatch):
        """Test browser admin token header can read aggregate counters."""
        monkeypatch.setattr("app.routes.health.settings.queue_metrics_token", TOKEN)
        database = FakeQueueDatabase(rows=queue_rows())
        client.app.dependency_overrides[get_db] = db_override(database)
        try:
            response = client.get(
                "/health/queue",
                headers={"X-Admin-Token": TOKEN},
            )
        finally:
            client.app.dependency_overrides.clear()

        assert response.status_code == 200
        payload = response.json()
        assert payload["pending_count"] == 4
        assert payload["backoff_count"] >= 0
        assert payload["exhausted_count"] == 2

    @pytest.mark.unit
    def test_queue_health_database_failure_is_safe(self, client, monkeypatch, caplog):
        """Test failed metrics queries return a safe response and safe logs."""
        monkeypatch.setattr("app.routes.health.settings.queue_metrics_token", TOKEN)
        database = FakeQueueDatabase(rows=queue_rows(), fail=True)
        client.app.dependency_overrides[get_db] = db_override(database)
        with caplog.at_level(logging.WARNING):
            try:
                response = client.get(
                    "/health/queue",
                    headers={"Authorization": f"Bearer {TOKEN}"},
                )
            finally:
                client.app.dependency_overrides.clear()

        assert response.status_code == 503
        assert response.json() == {"detail": "Queue health check failed"}
        assert "raw private customer message" not in caplog.text
        assert "301-555-0144" not in caplog.text


class TestDaemonQueueMetricsLogging:
    """Daemon log tests for queue metrics observability."""

    @pytest.mark.unit
    def test_daemon_logs_queue_metrics_without_raw_text(self, caplog):
        """Test daemon iteration logs aggregate queue counters safely."""
        raw_message = "Do not log customer details 301-555-0144"

        async def process_batch(db, client, settings):
            return LeadClassificationBatchResult(
                fetched=1,
                saved=1,
                classified=1,
                failed=0,
                skipped=0,
                errors=0,
                results=[],
            )

        async def fetch_queue_metrics(db, max_attempts):
            return LeadClassificationQueueMetrics(
                pending_count=7,
                backoff_count=2,
                exhausted_count=1,
                max_attempts=max_attempts,
            )

        with caplog.at_level(logging.INFO):
            summary = asyncio.run(
                run_daemon(
                    daemon_settings=DaemonSettings(run_once=True),
                    db=object(),
                    client=object(),
                    process_batch=process_batch,
                    fetch_queue_metrics=fetch_queue_metrics,
                )
            )

        assert summary.classified == 1
        assert "pending_count=7" in caplog.text
        assert "backoff_count=2" in caplog.text
        assert "exhausted_count=1" in caplog.text
        assert raw_message not in caplog.text
        assert "301-555-0144" not in caplog.text

    @pytest.mark.unit
    def test_daemon_metrics_failure_does_not_interrupt_batch(self, caplog):
        """Test metrics failures are observable but do not fail worker progress."""
        async def process_batch(db, client, settings):
            return LeadClassificationBatchResult(
                fetched=1,
                saved=1,
                classified=1,
                failed=0,
                skipped=0,
                errors=0,
                results=[],
            )

        async def fetch_queue_metrics(db, max_attempts):
            raise RuntimeError("raw private customer message 301-555-0144")

        with caplog.at_level(logging.WARNING):
            summary = asyncio.run(
                run_daemon(
                    daemon_settings=DaemonSettings(run_once=True),
                    db=object(),
                    client=object(),
                    process_batch=process_batch,
                    fetch_queue_metrics=fetch_queue_metrics,
                )
            )

        assert summary.classified == 1
        assert "queue metrics unavailable error_type=RuntimeError" in caplog.text
        assert "raw private customer message" not in caplog.text
        assert "301-555-0144" not in caplog.text
