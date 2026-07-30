"""Signed worker alert delivery and threshold-based incident detection."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict

from app.config import settings
from app.services.signed_webhook import post_signed_json

logger = logging.getLogger(__name__)


class AlertSeverity(StrEnum):
    """Supported worker incident severities."""

    WARNING = "warning"
    CRITICAL = "critical"


class AlertEvent(BaseModel):
    """Data-minimized worker incident sent to an alert destination."""

    model_config = ConfigDict(extra="forbid")

    alert_type: str
    severity: AlertSeverity
    worker: str
    message: str
    occurred_at: datetime
    incident_key: str
    metrics: dict[str, int | float | str | None]


class AlertDeliveryError(RuntimeError):
    """Raised when a configured alert destination rejects delivery."""


class AlertRouter(Protocol):
    """Interface for worker alert destinations."""

    async def route_alert(self, event: AlertEvent) -> None:
        """Deliver one sanitized incident."""


class SignedWebhookAlertRouter:
    """Deliver worker incidents through a separately signed webhook."""

    def __init__(
        self,
        *,
        url: str,
        secret: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = url
        self.secret = secret
        self.timeout_seconds = timeout_seconds
        self.client = client

    async def route_alert(self, event: AlertEvent) -> None:
        """Send one signed, idempotent alert event."""
        try:
            response = await post_signed_json(
                url=self.url,
                secret=self.secret,
                event_type=event.alert_type,
                idempotency_key=event.incident_key,
                payload={
                    "event": "worker.alert",
                    "alert": event.model_dump(mode="json"),
                },
                timeout_seconds=self.timeout_seconds,
                client=self.client,
            )
        except httpx.TransportError as exc:
            raise AlertDeliveryError("alert_transport_failed") from exc

        if not 200 <= response.status_code < 300:
            raise AlertDeliveryError("alert_destination_rejected")


class LoggingAlertRouter:
    """Accurately report incidents when no external destination is configured."""

    async def route_alert(self, event: AlertEvent) -> None:
        """Log one safe alert without pretending external delivery occurred."""
        logger.warning(
            "Worker alert not delivered because alert webhook is unconfigured "
            "alert_type=%s severity=%s worker=%s incident_key=%s",
            event.alert_type,
            event.severity,
            event.worker,
            event.incident_key,
        )


def configured_alert_router(
    client: httpx.AsyncClient | None = None,
) -> AlertRouter:
    """Build the configured alert router or a truthful logging fallback."""
    if settings.alert_webhook_url is None or settings.alert_webhook_secret is None:
        return LoggingAlertRouter()
    return SignedWebhookAlertRouter(
        url=settings.alert_webhook_url,
        secret=settings.alert_webhook_secret,
        timeout_seconds=settings.alert_webhook_timeout_seconds,
        client=client,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


class WorkerAlertMonitor:
    """Track consecutive worker health signals and route incidents."""

    def __init__(
        self,
        *,
        worker: str,
        router: AlertRouter,
        stalled_queue_iterations: int,
        high_error_rate_threshold: float,
        min_error_sample_size: int,
        repeated_crash_count: int,
        cooldown_seconds: int,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.worker = worker
        self.router = router
        self.stalled_queue_iterations = stalled_queue_iterations
        self.high_error_rate_threshold = high_error_rate_threshold
        self.min_error_sample_size = min_error_sample_size
        self.repeated_crash_count = repeated_crash_count
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock
        self.stalled_iterations = 0
        self.consecutive_crashes = 0
        self.last_delivered_at: dict[str, datetime] = {}

    async def _route(
        self,
        *,
        alert_type: str,
        severity: AlertSeverity,
        message: str,
        metrics: dict[str, int | float | str | None],
    ) -> None:
        now = self.clock().astimezone(UTC)
        previous = self.last_delivered_at.get(alert_type)
        if previous is not None:
            elapsed = (now - previous).total_seconds()
            if elapsed < self.cooldown_seconds:
                return

        period = int(now.timestamp()) // self.cooldown_seconds
        event = AlertEvent(
            alert_type=alert_type,
            severity=severity,
            worker=self.worker,
            message=message,
            occurred_at=now,
            incident_key=f"{self.worker}:{alert_type}:{period}",
            metrics=metrics,
        )
        try:
            await self.router.route_alert(event)
        except Exception as exc:
            logger.error(
                "Worker alert delivery failed alert_type=%s worker=%s error_type=%s",
                alert_type,
                self.worker,
                type(exc).__name__,
            )
            return
        self.last_delivered_at[alert_type] = now

    async def observe_iteration(
        self,
        *,
        fetched: int,
        completed: int,
        errors: int,
        queue_pending: int | None,
        batch_crashed: bool,
    ) -> None:
        """Evaluate one daemon iteration against configured alert thresholds."""
        if queue_pending is not None and queue_pending > 0 and completed == 0:
            self.stalled_iterations += 1
        else:
            self.stalled_iterations = 0

        if self.stalled_iterations >= self.stalled_queue_iterations:
            await self._route(
                alert_type="worker.queue_stalled",
                severity=AlertSeverity.WARNING,
                message="Worker queue has pending work without completed items.",
                metrics={
                    "pending_count": queue_pending,
                    "consecutive_stalled_iterations": self.stalled_iterations,
                    "fetched": fetched,
                    "completed": completed,
                },
            )

        if (
            fetched >= self.min_error_sample_size
            and errors / fetched >= self.high_error_rate_threshold
        ):
            await self._route(
                alert_type="worker.high_error_rate",
                severity=AlertSeverity.WARNING,
                message="Worker batch error rate crossed the configured threshold.",
                metrics={
                    "fetched": fetched,
                    "errors": errors,
                    "error_rate": errors / fetched,
                    "threshold": self.high_error_rate_threshold,
                },
            )

        if batch_crashed:
            self.consecutive_crashes += 1
        else:
            self.consecutive_crashes = 0

        if self.consecutive_crashes >= self.repeated_crash_count:
            await self._route(
                alert_type="worker.repeated_crashes",
                severity=AlertSeverity.CRITICAL,
                message="Worker batch execution crashed repeatedly.",
                metrics={
                    "consecutive_crashes": self.consecutive_crashes,
                },
            )
