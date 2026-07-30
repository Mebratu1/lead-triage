"""Tests for the long-running classification daemon."""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

import pytest

from app.jobs.classification_daemon import (
    DaemonRunSummary,
    DaemonSettings,
    ShutdownFlag,
    async_main,
    install_signal_handlers,
    parse_args,
    run_daemon,
    settings_from_args,
)
from app.models.classification import LeadClassificationBatchResult
from app.models.classification import LeadClassificationQueueMetrics
from app.services.alert_routing import AlertEvent


class FakeDaemonClient:
    """Mock classification client with optional close tracking."""

    model = "gpt-daemon-test"

    def __init__(self) -> None:
        self.closed = False

    async def classify(self, raw_message: str) -> str:
        raise AssertionError("daemon tests inject process_batch")

    async def close(self) -> None:
        self.closed = True


class RecordingAlertRouter:
    """Capture daemon-routed worker incidents."""

    def __init__(self) -> None:
        self.events: list[AlertEvent] = []

    async def route_alert(self, event: AlertEvent) -> None:
        self.events.append(event)


def batch_result(
    fetched: int = 0,
    saved: int = 0,
    classified: int = 0,
    failed: int = 0,
    skipped: int = 0,
    errors: int = 0,
) -> LeadClassificationBatchResult:
    """Build a daemon batch result."""
    return LeadClassificationBatchResult(
        fetched=fetched,
        saved=saved,
        classified=classified,
        failed=failed,
        skipped=skipped,
        errors=errors,
        results=[],
    )


class TestClassificationDaemonArgs:
    """Daemon argument parsing tests."""

    @pytest.mark.unit
    def test_parse_args_defaults(self):
        """Test daemon default settings."""
        args = parse_args([])
        settings = settings_from_args(args)

        assert settings == DaemonSettings()

    @pytest.mark.unit
    def test_parse_args_accepts_runtime_controls(self):
        """Test daemon CLI controls map to settings."""
        args = parse_args(
            [
                "--limit",
                "25",
                "--sleep-seconds",
                "7",
                "--worker-id",
                "daemon-1",
                "--claim-timeout-seconds",
                "30",
                "--retry-after-seconds",
                "45",
                "--max-attempts",
                "3",
                "--run-once",
            ]
        )

        assert settings_from_args(args) == DaemonSettings(
            limit=25,
            sleep_seconds=7,
            worker_id="daemon-1",
            claim_timeout_seconds=30,
            retry_after_seconds=45,
            max_attempts=3,
            run_once=True,
        )

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("option", "value"),
        [
            ("--limit", "0"),
            ("--limit", "101"),
            ("--sleep-seconds", "0"),
            ("--claim-timeout-seconds", "-1"),
            ("--retry-after-seconds", "not-a-number"),
            ("--max-attempts", "0"),
        ],
    )
    def test_parse_args_rejects_invalid_controls(self, option: str, value: str):
        """Test invalid daemon controls fail argument parsing."""
        with pytest.raises(SystemExit):
            parse_args([option, value])


class TestClassificationDaemonLoop:
    """Daemon loop behavior tests."""

    @pytest.mark.unit
    def test_run_once_executes_one_batch_and_closes_owned_resources(self):
        """Test run-once performs exactly one batch."""
        database = object()
        client = FakeDaemonClient()
        calls: dict[str, Any] = {"db_closed": False, "process": []}

        async def db_factory():
            return database

        async def db_close():
            calls["db_closed"] = True

        async def client_factory():
            return client

        async def process_batch(db, active_client, settings):
            calls["process"].append(
                {
                    "db": db,
                    "client": active_client,
                    "settings": settings,
                }
            )
            return batch_result(fetched=1, saved=1, classified=1)

        summary = asyncio.run(
            run_daemon(
                daemon_settings=DaemonSettings(run_once=True, worker_id="daemon-1"),
                db_factory=db_factory,
                db_close=db_close,
                client_factory=client_factory,
                process_batch=process_batch,
            )
        )

        assert summary == DaemonRunSummary(
            iterations=1,
            fetched=1,
            saved=1,
            classified=1,
            failed=0,
            skipped=0,
            errors=0,
        )
        assert len(calls["process"]) == 1
        assert calls["process"][0]["settings"].worker_id == "daemon-1"
        assert calls["db_closed"] is True
        assert client.closed is True

    @pytest.mark.unit
    def test_daemon_sleeps_between_iterations_and_honors_shutdown(self):
        """Test daemon sleeps between batches and exits when shutdown is requested."""
        shutdown = ShutdownFlag()
        calls = {"process": 0, "sleep": []}

        async def process_batch(db, client, settings):
            calls["process"] += 1
            return batch_result()

        async def sleep(seconds: float):
            calls["sleep"].append(seconds)
            shutdown.request()

        summary = asyncio.run(
            run_daemon(
                daemon_settings=DaemonSettings(sleep_seconds=11),
                shutdown=shutdown,
                db=object(),
                client=FakeDaemonClient(),
                process_batch=process_batch,
                sleep=sleep,
            )
        )

        assert calls["process"] == 1
        assert calls["sleep"] == [11]
        assert summary.iterations == 1

    @pytest.mark.unit
    def test_shutdown_during_active_batch_finishes_before_exit(self):
        """Test shutdown requested mid-batch does not interrupt the batch."""
        shutdown = ShutdownFlag()
        calls = {"process": 0, "sleep": 0}

        async def process_batch(db, client, settings):
            calls["process"] += 1
            shutdown.request()
            return batch_result(fetched=1, saved=1, classified=1)

        async def sleep(seconds: float):
            calls["sleep"] += 1

        summary = asyncio.run(
            run_daemon(
                daemon_settings=DaemonSettings(),
                shutdown=shutdown,
                db=object(),
                client=FakeDaemonClient(),
                process_batch=process_batch,
                sleep=sleep,
            )
        )

        assert calls == {"process": 1, "sleep": 0}
        assert summary.saved == 1
        assert summary.classified == 1

    @pytest.mark.unit
    def test_batch_errors_do_not_crash_daemon_or_log_raw_message(self, caplog):
        """Test batch exceptions are counted and logs stay private."""
        raw_message = "Private customer text 301-555-0144"

        async def process_batch(db, client, settings):
            raise RuntimeError(raw_message)

        with caplog.at_level(logging.ERROR):
            summary = asyncio.run(
                run_daemon(
                    daemon_settings=DaemonSettings(run_once=True),
                    db=object(),
                    client=FakeDaemonClient(),
                    process_batch=process_batch,
                )
            )

        assert summary.errors == 1
        assert "RuntimeError" in caplog.text
        assert raw_message not in caplog.text
        assert "301-555-0144" not in caplog.text

    @pytest.mark.unit
    def test_existing_resources_are_not_closed_by_daemon(self):
        """Test caller-owned db/client objects are not closed."""
        client = FakeDaemonClient()
        calls = {"db_closed": False}

        async def db_close():
            calls["db_closed"] = True

        async def process_batch(db, active_client, settings):
            return batch_result()

        asyncio.run(
            run_daemon(
                daemon_settings=DaemonSettings(run_once=True),
                db=object(),
                client=client,
                db_close=db_close,
                process_batch=process_batch,
            )
        )

        assert calls["db_closed"] is False
        assert client.closed is False

    @pytest.mark.unit
    def test_daemon_routes_stalled_queue_alert(self):
        """Test queue metrics and no-progress batches feed the alert monitor."""
        shutdown = ShutdownFlag()
        router = RecordingAlertRouter()
        calls = {"sleep": 0}

        async def process_batch(db, client, settings):
            return batch_result(fetched=2)

        async def fetch_queue_metrics(db, max_attempts):
            return LeadClassificationQueueMetrics(
                pending_count=7,
                backoff_count=0,
                exhausted_count=0,
                max_attempts=max_attempts,
            )

        async def sleep(seconds: float):
            calls["sleep"] += 1
            if calls["sleep"] == 2:
                shutdown.request()

        asyncio.run(
            run_daemon(
                daemon_settings=DaemonSettings(
                    alert_stalled_queue_iterations=2,
                ),
                shutdown=shutdown,
                db=object(),
                client=FakeDaemonClient(),
                process_batch=process_batch,
                fetch_queue_metrics=fetch_queue_metrics,
                alert_router=router,
                sleep=sleep,
            )
        )

        assert [event.alert_type for event in router.events] == [
            "worker.queue_stalled"
        ]

    @pytest.mark.unit
    def test_signal_handlers_request_graceful_shutdown(self, monkeypatch):
        """Test SIGINT/SIGTERM handlers request shutdown."""
        installed: dict[int, Any] = {}

        def fake_signal(signum: int, handler):
            installed[signum] = handler

        monkeypatch.setattr(signal, "signal", fake_signal)
        shutdown = ShutdownFlag()

        install_signal_handlers(shutdown)
        installed[signal.SIGINT](signal.SIGINT, None)

        assert shutdown.requested is True
        if hasattr(signal, "SIGTERM"):
            assert signal.SIGTERM in installed

    @pytest.mark.unit
    def test_async_main_returns_nonzero_for_accumulated_errors(self, monkeypatch):
        """Test CLI returns non-zero when daemon summary contains errors."""
        async def fake_run_daemon(daemon_settings, shutdown):
            return DaemonRunSummary(
                iterations=1,
                fetched=0,
                saved=0,
                classified=0,
                failed=0,
                skipped=0,
                errors=1,
            )

        monkeypatch.setattr(
            "app.jobs.classification_daemon.install_signal_handlers",
            lambda shutdown: None,
        )
        monkeypatch.setattr(
            "app.jobs.classification_daemon.run_daemon",
            fake_run_daemon,
        )

        exit_code = asyncio.run(async_main(["--run-once"]))

        assert exit_code == 1
