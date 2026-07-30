"""Tests for the asynchronous CRM retry daemon loop."""

from __future__ import annotations

import asyncio

import pytest

from app.jobs.crm_sync_daemon import (
    CrmSyncDaemonRunSummary,
    CrmSyncDaemonSettings,
    ShutdownFlag,
    parse_args,
    run_daemon,
    settings_from_args,
)
from app.models.integration import CrmSyncBatchResult
from app.services.alert_routing import AlertEvent


class FakeDispatcher:
    """No-op dispatcher because daemon tests inject batch processing."""

    async def sync_lead(self, lead) -> None:
        raise AssertionError("daemon tests inject process_batch")


class RecordingAlertRouter:
    """Capture CRM daemon incidents."""

    def __init__(self) -> None:
        self.events: list[AlertEvent] = []

    async def route_alert(self, event: AlertEvent) -> None:
        self.events.append(event)


def batch_result(
    *,
    fetched: int = 0,
    synced: int = 0,
    retry_scheduled: int = 0,
    permanent_failed: int = 0,
    exhausted: int = 0,
    skipped: int = 0,
    errors: int = 0,
) -> CrmSyncBatchResult:
    """Build a daemon batch result."""
    return CrmSyncBatchResult(
        fetched=fetched,
        synced=synced,
        retry_scheduled=retry_scheduled,
        permanent_failed=permanent_failed,
        exhausted=exhausted,
        skipped=skipped,
        errors=errors,
        results=[],
    )


class TestCrmSyncDaemon:
    """CLI and loop behavior tests."""

    @pytest.mark.unit
    def test_runtime_controls_map_to_settings(self):
        """Test worker, backoff, and polling CLI options."""
        args = parse_args(
            [
                "--limit",
                "7",
                "--sleep-seconds",
                "11",
                "--worker-id",
                "crm-worker-1",
                "--claim-timeout-seconds",
                "90",
                "--retry-base-seconds",
                "30",
                "--retry-max-seconds",
                "300",
                "--max-attempts",
                "4",
                "--run-once",
            ]
        )

        assert settings_from_args(args) == CrmSyncDaemonSettings(
            limit=7,
            sleep_seconds=11,
            worker_id="crm-worker-1",
            claim_timeout_seconds=90,
            retry_base_seconds=30,
            retry_max_seconds=300,
            max_attempts=4,
            run_once=True,
        )

    @pytest.mark.unit
    def test_run_once_processes_one_batch_and_closes_owned_database(self):
        """Test run-once consumes one bounded due-retry batch."""
        calls = {"closed": False, "processed": 0}

        async def db_factory():
            return object()

        async def db_close():
            calls["closed"] = True

        async def dispatcher_factory():
            return FakeDispatcher()

        async def process_batch(db, dispatcher, daemon_settings):
            calls["processed"] += 1
            return batch_result(fetched=2, synced=1, retry_scheduled=1)

        summary = asyncio.run(
            run_daemon(
                CrmSyncDaemonSettings(run_once=True),
                db_factory=db_factory,
                db_close=db_close,
                dispatcher_factory=dispatcher_factory,
                process_batch=process_batch,
            )
        )

        assert summary == CrmSyncDaemonRunSummary(
            iterations=1,
            fetched=2,
            synced=1,
            retry_scheduled=1,
            permanent_failed=0,
            exhausted=0,
            skipped=0,
            errors=0,
        )
        assert calls == {"closed": True, "processed": 1}

    @pytest.mark.unit
    def test_loop_sleeps_and_honors_shutdown(self):
        """Test the async consumer polls until graceful shutdown."""
        shutdown = ShutdownFlag()
        calls = {"processed": 0, "sleeps": []}

        async def process_batch(db, dispatcher, daemon_settings):
            calls["processed"] += 1
            return batch_result()

        async def sleep(seconds: float):
            calls["sleeps"].append(seconds)
            shutdown.request()

        summary = asyncio.run(
            run_daemon(
                CrmSyncDaemonSettings(sleep_seconds=13),
                shutdown=shutdown,
                db=object(),
                dispatcher=FakeDispatcher(),
                process_batch=process_batch,
                sleep=sleep,
            )
        )

        assert summary.iterations == 1
        assert calls == {"processed": 1, "sleeps": [13]}

    @pytest.mark.unit
    def test_repeated_batch_crashes_route_critical_alert(self):
        """Test consecutive uncaught batch failures trigger notification."""
        shutdown = ShutdownFlag()
        router = RecordingAlertRouter()
        calls = {"sleeps": 0}

        async def process_batch(db, dispatcher, daemon_settings):
            raise RuntimeError("private customer data")

        async def sleep(seconds: float):
            calls["sleeps"] += 1
            if calls["sleeps"] == 2:
                shutdown.request()

        summary = asyncio.run(
            run_daemon(
                CrmSyncDaemonSettings(alert_repeated_crash_count=2),
                shutdown=shutdown,
                db=object(),
                dispatcher=FakeDispatcher(),
                alert_router=router,
                process_batch=process_batch,
                sleep=sleep,
            )
        )

        assert summary.errors == 2
        assert [event.alert_type for event in router.events] == [
            "worker.repeated_crashes"
        ]
        assert router.events[0].severity == "critical"

    @pytest.mark.unit
    def test_due_retry_backlog_routes_stalled_queue_alert(self):
        """Test the integration retry queue feeds stalled-queue detection."""
        shutdown = ShutdownFlag()
        router = RecordingAlertRouter()
        calls = {"sleeps": 0}

        async def process_batch(db, dispatcher, daemon_settings):
            return batch_result()

        async def fetch_queue_metrics(db, max_attempts):
            return 6

        async def sleep(seconds: float):
            calls["sleeps"] += 1
            if calls["sleeps"] == 2:
                shutdown.request()

        asyncio.run(
            run_daemon(
                CrmSyncDaemonSettings(
                    alert_stalled_queue_iterations=2,
                ),
                shutdown=shutdown,
                db=object(),
                dispatcher=FakeDispatcher(),
                alert_router=router,
                process_batch=process_batch,
                fetch_queue_metrics=fetch_queue_metrics,
                sleep=sleep,
            )
        )

        assert [event.alert_type for event in router.events] == [
            "worker.queue_stalled"
        ]
