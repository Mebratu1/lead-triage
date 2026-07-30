"""Tests for signed worker alert routing and incident thresholds."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.services.alert_routing import (
    AlertDeliveryError,
    AlertEvent,
    AlertSeverity,
    SignedWebhookAlertRouter,
    WorkerAlertMonitor,
)

ALERT_SECRET = "alert-test-signing-secret-with-32-bytes"


class RecordingAlertRouter:
    """Capture alert events without external I/O."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.events: list[AlertEvent] = []

    async def route_alert(self, event: AlertEvent) -> None:
        if self.failure is not None:
            raise self.failure
        self.events.append(event)


def alert_event() -> AlertEvent:
    """Build a sanitized representative worker incident."""
    return AlertEvent(
        alert_type="worker.queue_stalled",
        severity=AlertSeverity.WARNING,
        worker="classification-worker-1",
        message="Worker queue has pending work without completed items.",
        occurred_at=datetime(2026, 7, 30, 12, tzinfo=UTC),
        incident_key="classification-worker-1:worker.queue_stalled:123",
        metrics={"pending_count": 12, "completed": 0},
    )


class TestSignedWebhookAlertRouter:
    """Alert webhook signing and failure tests."""

    @pytest.mark.unit
    def test_alerts_use_separate_hmac_and_incident_idempotency(self):
        """Test alert payload bytes match signature and idempotency headers."""
        captured: dict[str, httpx.Request] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["request"] = request
            return httpx.Response(202)

        async def route() -> None:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                router = SignedWebhookAlertRouter(
                    url="https://alerts.example.test/worker",
                    secret=ALERT_SECRET,
                    timeout_seconds=3,
                    client=client,
                )
                await router.route_alert(alert_event())

        asyncio.run(route())

        request = captured["request"]
        body = request.content
        parsed = json.loads(body)
        assert parsed["event"] == "worker.alert"
        assert parsed["alert"]["alert_type"] == "worker.queue_stalled"
        assert (
            request.headers["Idempotency-Key"]
            == "classification-worker-1:worker.queue_stalled:123"
        )
        timestamp = request.headers["X-LeadTriage-Timestamp"]
        digest = hmac.new(
            ALERT_SECRET.encode(),
            timestamp.encode("ascii") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        assert request.headers["X-LeadTriage-Signature"] == f"sha256={digest}"
        assert request.extensions["timeout"] == {
            "connect": 3,
            "read": 3,
            "write": 3,
            "pool": 3,
        }

    @pytest.mark.unit
    def test_alert_destination_failure_is_sanitized(self):
        """Test alert response bodies cannot leak into worker logs."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="private alert destination details")

        async def route() -> None:
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                router = SignedWebhookAlertRouter(
                    url="https://alerts.example.test/worker",
                    secret=ALERT_SECRET,
                    timeout_seconds=3,
                    client=client,
                )
                await router.route_alert(alert_event())

        with pytest.raises(AlertDeliveryError) as raised:
            asyncio.run(route())

        assert str(raised.value) == "alert_destination_rejected"
        assert "private alert destination" not in str(raised.value)


class TestWorkerAlertMonitor:
    """Threshold, reset, cooldown, and error-containment tests."""

    def build_monitor(
        self,
        *,
        router: RecordingAlertRouter,
        now: list[datetime],
    ) -> WorkerAlertMonitor:
        """Build a monitor with small deterministic test thresholds."""
        return WorkerAlertMonitor(
            worker="classification-worker-1",
            router=router,
            stalled_queue_iterations=2,
            high_error_rate_threshold=0.5,
            min_error_sample_size=4,
            repeated_crash_count=2,
            cooldown_seconds=60,
            clock=lambda: now[0],
        )

    @pytest.mark.unit
    def test_stalled_queue_alerts_after_consecutive_no_progress_iterations(self):
        """Test pending work with no completions must persist before alerting."""
        router = RecordingAlertRouter()
        now = [datetime(2026, 7, 30, 12, tzinfo=UTC)]
        monitor = self.build_monitor(router=router, now=now)

        async def observe() -> None:
            await monitor.observe_iteration(
                fetched=2,
                completed=0,
                errors=0,
                queue_pending=8,
                batch_crashed=False,
            )
            assert router.events == []
            await monitor.observe_iteration(
                fetched=2,
                completed=0,
                errors=0,
                queue_pending=8,
                batch_crashed=False,
            )
            await monitor.observe_iteration(
                fetched=2,
                completed=0,
                errors=0,
                queue_pending=8,
                batch_crashed=False,
            )

        asyncio.run(observe())

        assert [event.alert_type for event in router.events] == [
            "worker.queue_stalled"
        ]
        assert router.events[0].metrics["pending_count"] == 8

    @pytest.mark.unit
    def test_high_error_rate_and_repeated_crashes_route_distinct_alerts(self):
        """Test batch error-rate and crash signals are evaluated independently."""
        router = RecordingAlertRouter()
        now = [datetime(2026, 7, 30, 12, tzinfo=UTC)]
        monitor = self.build_monitor(router=router, now=now)

        async def observe() -> None:
            await monitor.observe_iteration(
                fetched=10,
                completed=4,
                errors=6,
                queue_pending=0,
                batch_crashed=True,
            )
            await monitor.observe_iteration(
                fetched=0,
                completed=0,
                errors=1,
                queue_pending=0,
                batch_crashed=True,
            )

        asyncio.run(observe())

        assert [event.alert_type for event in router.events] == [
            "worker.high_error_rate",
            "worker.repeated_crashes",
        ]
        assert router.events[1].severity == AlertSeverity.CRITICAL

    @pytest.mark.unit
    def test_cooldown_allows_a_later_incident_notification(self):
        """Test sustained incidents are deduplicated until cooldown expires."""
        router = RecordingAlertRouter()
        now = [datetime(2026, 7, 30, 12, tzinfo=UTC)]
        monitor = self.build_monitor(router=router, now=now)

        async def observe_twice() -> None:
            for _ in range(2):
                await monitor.observe_iteration(
                    fetched=0,
                    completed=0,
                    errors=0,
                    queue_pending=5,
                    batch_crashed=False,
                )
            now[0] += timedelta(seconds=61)
            await monitor.observe_iteration(
                fetched=0,
                completed=0,
                errors=0,
                queue_pending=5,
                batch_crashed=False,
            )

        asyncio.run(observe_twice())

        assert len(router.events) == 2
        assert router.events[0].incident_key != router.events[1].incident_key

    @pytest.mark.unit
    def test_alert_delivery_failure_does_not_crash_worker_or_log_details(
        self,
        caplog,
    ):
        """Test notification outages are contained and logged safely."""
        router = RecordingAlertRouter(
            RuntimeError("private webhook secret and customer message")
        )
        now = [datetime(2026, 7, 30, 12, tzinfo=UTC)]
        monitor = self.build_monitor(router=router, now=now)

        async def observe() -> None:
            for _ in range(2):
                await monitor.observe_iteration(
                    fetched=0,
                    completed=0,
                    errors=0,
                    queue_pending=5,
                    batch_crashed=False,
                )

        with caplog.at_level(logging.ERROR):
            asyncio.run(observe())

        assert "RuntimeError" in caplog.text
        assert "private webhook secret" not in caplog.text
        assert "customer message" not in caplog.text
