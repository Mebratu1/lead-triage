"""Long-running asynchronous consumer for due CRM webhook retries.

Run with:
    python -m app.jobs.crm_sync_daemon
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from supabase import AsyncClient

from app.config import settings
from app.db.client import SupabaseClient
from app.models.integration import CrmSyncBatchResult
from app.repositories.lead_repository import count_due_leads_for_crm_sync
from app.services.alert_routing import (
    AlertRouter,
    WorkerAlertMonitor,
    configured_alert_router,
)
from app.services.crm_sync import LeadCrmDispatcher, configured_crm_dispatcher
from app.services.crm_sync_worker import (
    MAX_CRM_RETRY_BATCH_SIZE,
    process_due_crm_sync_batch,
)

logger = logging.getLogger(__name__)

ProcessBatch = Callable[..., Awaitable[CrmSyncBatchResult]]
Sleep = Callable[[float], Awaitable[None]]
AlertRouterFactory = Callable[[], Awaitable[AlertRouter]]
FetchQueueMetrics = Callable[[AsyncClient, int], Awaitable[int]]


@dataclass(frozen=True)
class CrmSyncDaemonSettings:
    """Runtime settings for the CRM retry daemon."""

    limit: int = settings.crm_retry_batch_size
    sleep_seconds: int = settings.crm_retry_poll_seconds
    worker_id: str | None = None
    claim_timeout_seconds: int = settings.crm_retry_claim_timeout_seconds
    retry_base_seconds: int = settings.crm_retry_base_seconds
    retry_max_seconds: int = settings.crm_retry_max_seconds
    max_attempts: int = settings.crm_retry_max_attempts
    run_once: bool = False
    alert_stalled_queue_iterations: int = settings.alert_stalled_queue_iterations
    alert_high_error_rate_threshold: float = settings.alert_high_error_rate_threshold
    alert_min_error_sample_size: int = settings.alert_min_error_sample_size
    alert_repeated_crash_count: int = settings.alert_repeated_crash_count
    alert_cooldown_seconds: int = settings.alert_cooldown_seconds


@dataclass(frozen=True)
class CrmSyncDaemonRunSummary:
    """Aggregate CRM retry daemon run summary."""

    iterations: int
    fetched: int
    synced: int
    retry_scheduled: int
    permanent_failed: int
    exhausted: int
    skipped: int
    errors: int


class ShutdownFlag:
    """Shared shutdown state set by signal handlers or tests."""

    def __init__(self) -> None:
        self._requested = False

    @property
    def requested(self) -> bool:
        """Whether shutdown has been requested."""
        return self._requested

    def request(self) -> None:
        """Request graceful daemon shutdown."""
        self._requested = True


def _positive_int(name: str) -> Callable[[str], int]:
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
        if parsed < 1:
            raise argparse.ArgumentTypeError(f"{name} must be at least 1")
        return parsed

    return parse


def _bounded_limit(value: str) -> int:
    parsed = _positive_int("limit")(value)
    if parsed > MAX_CRM_RETRY_BATCH_SIZE:
        raise argparse.ArgumentTypeError(
            f"limit must not exceed {MAX_CRM_RETRY_BATCH_SIZE}"
        )
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the CRM retry daemon CLI parser."""
    parser = argparse.ArgumentParser(description="Run the CRM sync retry daemon.")
    parser.add_argument("--limit", type=_bounded_limit, default=settings.crm_retry_batch_size)
    parser.add_argument(
        "--sleep-seconds",
        type=_positive_int("sleep-seconds"),
        default=settings.crm_retry_poll_seconds,
    )
    parser.add_argument("--worker-id", default=None)
    parser.add_argument(
        "--claim-timeout-seconds",
        type=_positive_int("claim-timeout-seconds"),
        default=settings.crm_retry_claim_timeout_seconds,
    )
    parser.add_argument(
        "--retry-base-seconds",
        type=_positive_int("retry-base-seconds"),
        default=settings.crm_retry_base_seconds,
    )
    parser.add_argument(
        "--retry-max-seconds",
        type=_positive_int("retry-max-seconds"),
        default=settings.crm_retry_max_seconds,
    )
    parser.add_argument(
        "--max-attempts",
        type=_positive_int("max-attempts"),
        default=settings.crm_retry_max_attempts,
    )
    parser.add_argument("--run-once", action="store_true")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CRM retry daemon arguments."""
    return build_parser().parse_args(argv)


def settings_from_args(args: argparse.Namespace) -> CrmSyncDaemonSettings:
    """Build daemon settings from parsed arguments."""
    if args.retry_max_seconds < args.retry_base_seconds:
        raise ValueError("retry-max-seconds must be at least retry-base-seconds")
    return CrmSyncDaemonSettings(
        limit=args.limit,
        sleep_seconds=args.sleep_seconds,
        worker_id=args.worker_id,
        claim_timeout_seconds=args.claim_timeout_seconds,
        retry_base_seconds=args.retry_base_seconds,
        retry_max_seconds=args.retry_max_seconds,
        max_attempts=args.max_attempts,
        run_once=args.run_once,
    )


def configure_logging() -> None:
    """Configure logs without exposing webhook secrets or lead payloads."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(levelname)s:%(name)s:%(message)s",
    )


def install_signal_handlers(shutdown: ShutdownFlag) -> None:
    """Install graceful SIGINT and SIGTERM handlers."""
    def request_shutdown(signum: int, frame: Any) -> None:
        logger.info("CRM sync daemon shutdown requested signal=%s", signum)
        shutdown.request()

    signal.signal(signal.SIGINT, request_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_shutdown)


async def _get_db() -> AsyncClient:
    return await SupabaseClient.get_client()


async def _close_db() -> None:
    await SupabaseClient.close()


async def _get_dispatcher() -> LeadCrmDispatcher:
    return configured_crm_dispatcher()


async def _get_alert_router() -> AlertRouter:
    return configured_alert_router()


async def _fetch_queue_metrics(
    db: AsyncClient,
    max_attempts: int,
) -> int:
    return await count_due_leads_for_crm_sync(
        db=db,
        max_attempts=max_attempts,
    )


async def _process_batch(
    db: AsyncClient,
    dispatcher: LeadCrmDispatcher,
    daemon_settings: CrmSyncDaemonSettings,
) -> CrmSyncBatchResult:
    return await process_due_crm_sync_batch(
        db=db,
        dispatcher=dispatcher,
        limit=daemon_settings.limit,
        worker_id=daemon_settings.worker_id,
        claim_timeout_seconds=daemon_settings.claim_timeout_seconds,
        retry_base_seconds=daemon_settings.retry_base_seconds,
        retry_max_seconds=daemon_settings.retry_max_seconds,
        max_attempts=daemon_settings.max_attempts,
    )


def _empty_summary() -> CrmSyncDaemonRunSummary:
    return CrmSyncDaemonRunSummary(0, 0, 0, 0, 0, 0, 0, 0)


def _add_batch(
    summary: CrmSyncDaemonRunSummary,
    batch: CrmSyncBatchResult,
) -> CrmSyncDaemonRunSummary:
    return CrmSyncDaemonRunSummary(
        iterations=summary.iterations + 1,
        fetched=summary.fetched + batch.fetched,
        synced=summary.synced + batch.synced,
        retry_scheduled=summary.retry_scheduled + batch.retry_scheduled,
        permanent_failed=summary.permanent_failed + batch.permanent_failed,
        exhausted=summary.exhausted + batch.exhausted,
        skipped=summary.skipped + batch.skipped,
        errors=summary.errors + batch.errors,
    )


async def run_daemon(
    daemon_settings: CrmSyncDaemonSettings,
    shutdown: ShutdownFlag | None = None,
    db: AsyncClient | None = None,
    dispatcher: LeadCrmDispatcher | None = None,
    db_factory: Callable[[], Awaitable[AsyncClient]] = _get_db,
    db_close: Callable[[], Awaitable[None]] = _close_db,
    dispatcher_factory: Callable[[], Awaitable[LeadCrmDispatcher]] = _get_dispatcher,
    alert_router: AlertRouter | None = None,
    alert_router_factory: AlertRouterFactory = _get_alert_router,
    process_batch: ProcessBatch = _process_batch,
    fetch_queue_metrics: FetchQueueMetrics = _fetch_queue_metrics,
    sleep: Sleep = asyncio.sleep,
) -> CrmSyncDaemonRunSummary:
    """Process due CRM retries until shutdown or run-once completion."""
    active_shutdown = shutdown or ShutdownFlag()
    created_db = db is None
    active_dispatcher = dispatcher or await dispatcher_factory()
    active_alert_router = alert_router or await alert_router_factory()
    active_db = db or await db_factory()
    alert_monitor = WorkerAlertMonitor(
        worker=daemon_settings.worker_id or "crm-sync-daemon",
        router=active_alert_router,
        stalled_queue_iterations=daemon_settings.alert_stalled_queue_iterations,
        high_error_rate_threshold=daemon_settings.alert_high_error_rate_threshold,
        min_error_sample_size=daemon_settings.alert_min_error_sample_size,
        repeated_crash_count=daemon_settings.alert_repeated_crash_count,
        cooldown_seconds=daemon_settings.alert_cooldown_seconds,
    )
    summary = _empty_summary()

    logger.info(
        "CRM sync daemon started limit=%s sleep_seconds=%s worker_id=%s run_once=%s",
        daemon_settings.limit,
        daemon_settings.sleep_seconds,
        daemon_settings.worker_id,
        daemon_settings.run_once,
    )
    try:
        while not active_shutdown.requested:
            batch_crashed = False
            try:
                batch = await process_batch(
                    active_db,
                    active_dispatcher,
                    daemon_settings,
                )
            except Exception as exc:
                batch_crashed = True
                logger.error(
                    "CRM sync daemon batch failed error_type=%s",
                    type(exc).__name__,
                )
                batch = CrmSyncBatchResult(0, 0, 0, 0, 0, 0, 1, [])

            summary = _add_batch(summary, batch)
            due_retry_count: int | None = None
            try:
                due_retry_count = await fetch_queue_metrics(
                    active_db,
                    daemon_settings.max_attempts,
                )
            except Exception as exc:
                logger.warning(
                    "CRM sync queue metrics unavailable error_type=%s",
                    type(exc).__name__,
                )
            logger.info(
                "CRM sync daemon iteration completed iteration=%s fetched=%s "
                "synced=%s retry_scheduled=%s permanent_failed=%s exhausted=%s "
                "skipped=%s errors=%s due_retry_count=%s",
                summary.iterations,
                batch.fetched,
                batch.synced,
                batch.retry_scheduled,
                batch.permanent_failed,
                batch.exhausted,
                batch.skipped,
                batch.errors,
                due_retry_count,
            )
            await alert_monitor.observe_iteration(
                fetched=batch.fetched,
                completed=(
                    batch.synced
                    + batch.retry_scheduled
                    + batch.permanent_failed
                    + batch.exhausted
                    + batch.skipped
                ),
                errors=batch.errors,
                queue_pending=due_retry_count,
                batch_crashed=batch_crashed,
            )
            if daemon_settings.run_once or active_shutdown.requested:
                break
            await sleep(daemon_settings.sleep_seconds)
    finally:
        if created_db:
            await db_close()
        logger.info(
            "CRM sync daemon stopped iterations=%s fetched=%s synced=%s "
            "retry_scheduled=%s permanent_failed=%s exhausted=%s errors=%s",
            summary.iterations,
            summary.fetched,
            summary.synced,
            summary.retry_scheduled,
            summary.permanent_failed,
            summary.exhausted,
            summary.errors,
        )

    return summary


async def async_main(argv: Sequence[str] | None = None) -> int:
    """Async CRM retry daemon entrypoint."""
    configure_logging()
    daemon_settings = settings_from_args(parse_args(argv))
    shutdown = ShutdownFlag()
    install_signal_handlers(shutdown)
    summary = await run_daemon(daemon_settings, shutdown=shutdown)
    return 1 if summary.errors else 0


def main(argv: Sequence[str] | None = None) -> int:
    """Synchronous CRM retry daemon entrypoint."""
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
