"""Long-running classification worker daemon.

Run with:
    python -m app.jobs.classification_daemon
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
from app.models.classification import (
    LeadClassificationBatchResult,
    LeadClassificationQueueMetrics,
)
from app.services.lead_classification import LeadClassificationClient
from app.services.lead_classification_worker import (
    DEFAULT_CLAIM_TIMEOUT_SECONDS,
    DEFAULT_CLASSIFICATION_BATCH_SIZE,
    DEFAULT_MAX_CLASSIFICATION_ATTEMPTS,
    DEFAULT_RETRY_AFTER_SECONDS,
    MAX_CLASSIFICATION_BATCH_SIZE,
    process_pending_leads_batch,
)
from app.services.lead_persistence import get_classification_queue_metrics
from app.services.openai_client import OpenAILeadClassificationClient

logger = logging.getLogger(__name__)

DEFAULT_DAEMON_SLEEP_SECONDS = 30

ProcessBatch = Callable[
    ...,
    Awaitable[LeadClassificationBatchResult],
]
Sleep = Callable[[float], Awaitable[None]]
FetchQueueMetrics = Callable[
    [AsyncClient, int],
    Awaitable[LeadClassificationQueueMetrics],
]


@dataclass(frozen=True)
class DaemonSettings:
    """Runtime settings for the classification daemon."""

    limit: int = DEFAULT_CLASSIFICATION_BATCH_SIZE
    sleep_seconds: int = DEFAULT_DAEMON_SLEEP_SECONDS
    worker_id: str | None = None
    claim_timeout_seconds: int = DEFAULT_CLAIM_TIMEOUT_SECONDS
    retry_after_seconds: int = DEFAULT_RETRY_AFTER_SECONDS
    max_attempts: int = DEFAULT_MAX_CLASSIFICATION_ATTEMPTS
    run_once: bool = False


@dataclass(frozen=True)
class DaemonRunSummary:
    """Aggregate daemon run summary."""

    iterations: int
    fetched: int
    saved: int
    classified: int
    failed: int
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


def _bounded_limit(value: str) -> int:
    parsed = _positive_int("limit")(value)
    if parsed > MAX_CLASSIFICATION_BATCH_SIZE:
        raise argparse.ArgumentTypeError(
            f"limit must not exceed {MAX_CLASSIFICATION_BATCH_SIZE}"
        )
    return parsed


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


def build_parser() -> argparse.ArgumentParser:
    """Build the daemon CLI parser."""
    parser = argparse.ArgumentParser(
        description="Run the lead classification worker daemon.",
    )
    parser.add_argument(
        "--limit",
        type=_bounded_limit,
        default=DEFAULT_CLASSIFICATION_BATCH_SIZE,
        help=(
            "Maximum number of leads to claim per batch. "
            f"Default: {DEFAULT_CLASSIFICATION_BATCH_SIZE}."
        ),
    )
    parser.add_argument(
        "--sleep-seconds",
        type=_positive_int("sleep-seconds"),
        default=DEFAULT_DAEMON_SLEEP_SECONDS,
        help=(
            "Seconds to sleep between daemon iterations. "
            f"Default: {DEFAULT_DAEMON_SLEEP_SECONDS}."
        ),
    )
    parser.add_argument(
        "--worker-id",
        default=None,
        help="Optional stable worker id used for claimed lead ownership.",
    )
    parser.add_argument(
        "--claim-timeout-seconds",
        type=_positive_int("claim-timeout-seconds"),
        default=DEFAULT_CLAIM_TIMEOUT_SECONDS,
        help=(
            "Seconds before another runner may reclaim a stale claim. "
            f"Default: {DEFAULT_CLAIM_TIMEOUT_SECONDS}."
        ),
    )
    parser.add_argument(
        "--retry-after-seconds",
        type=_positive_int("retry-after-seconds"),
        default=DEFAULT_RETRY_AFTER_SECONDS,
        help=(
            "Seconds before retrying after a client/OpenAI failure. "
            f"Default: {DEFAULT_RETRY_AFTER_SECONDS}."
        ),
    )
    parser.add_argument(
        "--max-attempts",
        type=_positive_int("max-attempts"),
        default=DEFAULT_MAX_CLASSIFICATION_ATTEMPTS,
        help=(
            "Maximum classification attempts before pending leads are skipped. "
            f"Default: {DEFAULT_MAX_CLASSIFICATION_ATTEMPTS}."
        ),
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run a single daemon iteration and exit.",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse daemon CLI arguments."""
    return build_parser().parse_args(argv)


def settings_from_args(args: argparse.Namespace) -> DaemonSettings:
    """Build daemon settings from parsed arguments."""
    return DaemonSettings(
        limit=args.limit,
        sleep_seconds=args.sleep_seconds,
        worker_id=args.worker_id,
        claim_timeout_seconds=args.claim_timeout_seconds,
        retry_after_seconds=args.retry_after_seconds,
        max_attempts=args.max_attempts,
        run_once=args.run_once,
    )


def configure_logging() -> None:
    """Configure daemon logging without exposing lead message contents."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(levelname)s:%(name)s:%(message)s",
    )


def install_signal_handlers(shutdown: ShutdownFlag) -> None:
    """Install SIGINT/SIGTERM handlers for graceful shutdown."""
    def request_shutdown(signum: int, frame: Any) -> None:
        logger.info("Lead classification daemon shutdown requested signal=%s", signum)
        shutdown.request()

    signal.signal(signal.SIGINT, request_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_shutdown)


async def _get_db() -> AsyncClient:
    return await SupabaseClient.get_client()


async def _close_db() -> None:
    await SupabaseClient.close()


async def _get_client() -> LeadClassificationClient:
    return OpenAILeadClassificationClient()


async def _process_batch(
    db: AsyncClient,
    client: LeadClassificationClient,
    daemon_settings: DaemonSettings,
) -> LeadClassificationBatchResult:
    return await process_pending_leads_batch(
        db=db,
        client=client,
        limit=daemon_settings.limit,
        worker_id=daemon_settings.worker_id,
        claim_timeout_seconds=daemon_settings.claim_timeout_seconds,
        retry_after_seconds=daemon_settings.retry_after_seconds,
        max_attempts=daemon_settings.max_attempts,
    )


async def _fetch_queue_metrics(
    db: AsyncClient,
    max_attempts: int,
) -> LeadClassificationQueueMetrics:
    return await get_classification_queue_metrics(
        db=db,
        max_attempts=max_attempts,
    )


def _empty_summary() -> DaemonRunSummary:
    return DaemonRunSummary(
        iterations=0,
        fetched=0,
        saved=0,
        classified=0,
        failed=0,
        skipped=0,
        errors=0,
    )


def _add_batch(
    summary: DaemonRunSummary,
    batch: LeadClassificationBatchResult,
) -> DaemonRunSummary:
    return DaemonRunSummary(
        iterations=summary.iterations + 1,
        fetched=summary.fetched + batch.fetched,
        saved=summary.saved + batch.saved,
        classified=summary.classified + batch.classified,
        failed=summary.failed + batch.failed,
        skipped=summary.skipped + batch.skipped,
        errors=summary.errors + batch.errors,
    )


async def run_daemon(
    daemon_settings: DaemonSettings,
    shutdown: ShutdownFlag | None = None,
    db: AsyncClient | None = None,
    client: LeadClassificationClient | None = None,
    db_factory: Callable[[], Awaitable[AsyncClient]] = _get_db,
    db_close: Callable[[], Awaitable[None]] = _close_db,
    client_factory: Callable[[], Awaitable[LeadClassificationClient]] = _get_client,
    process_batch: ProcessBatch = _process_batch,
    fetch_queue_metrics: FetchQueueMetrics = _fetch_queue_metrics,
    sleep: Sleep = asyncio.sleep,
) -> DaemonRunSummary:
    """Run classification batches until shutdown or run-once completion."""
    active_shutdown = shutdown or ShutdownFlag()
    created_db = db is None
    created_client = client is None
    active_db = db or await db_factory()
    active_client = client or await client_factory()
    summary = _empty_summary()

    logger.info(
        "Lead classification daemon started limit=%s sleep_seconds=%s "
        "worker_id=%s run_once=%s",
        daemon_settings.limit,
        daemon_settings.sleep_seconds,
        daemon_settings.worker_id,
        daemon_settings.run_once,
    )

    try:
        while not active_shutdown.requested:
            try:
                batch = await process_batch(
                    active_db,
                    active_client,
                    daemon_settings,
                )
            except Exception as exc:
                logger.error(
                    "Lead classification daemon batch failed error_type=%s",
                    type(exc).__name__,
                )
                batch = LeadClassificationBatchResult(
                    fetched=0,
                    saved=0,
                    classified=0,
                    failed=0,
                    skipped=0,
                    errors=1,
                    results=[],
                )

            summary = _add_batch(summary, batch)
            queue_metrics: LeadClassificationQueueMetrics | None = None
            try:
                queue_metrics = await fetch_queue_metrics(
                    active_db,
                    daemon_settings.max_attempts,
                )
            except Exception as exc:
                logger.warning(
                    "Lead classification queue metrics unavailable error_type=%s",
                    type(exc).__name__,
                )

            logger.info(
                "Lead classification daemon iteration completed iteration=%s "
                "fetched=%s saved=%s classified=%s failed=%s skipped=%s errors=%s "
                "pending_count=%s backoff_count=%s exhausted_count=%s",
                summary.iterations,
                batch.fetched,
                batch.saved,
                batch.classified,
                batch.failed,
                batch.skipped,
                batch.errors,
                queue_metrics.pending_count if queue_metrics else None,
                queue_metrics.backoff_count if queue_metrics else None,
                queue_metrics.exhausted_count if queue_metrics else None,
            )

            if daemon_settings.run_once or active_shutdown.requested:
                break

            logger.info(
                "Lead classification daemon sleeping sleep_seconds=%s",
                daemon_settings.sleep_seconds,
            )
            await sleep(daemon_settings.sleep_seconds)
    finally:
        if created_db:
            await db_close()
        if created_client:
            close = getattr(active_client, "close", None)
            if callable(close):
                result = close()
                if isinstance(result, Awaitable):
                    await result

        logger.info(
            "Lead classification daemon stopped iterations=%s fetched=%s "
            "saved=%s classified=%s failed=%s skipped=%s errors=%s",
            summary.iterations,
            summary.fetched,
            summary.saved,
            summary.classified,
            summary.failed,
            summary.skipped,
            summary.errors,
        )

    return summary


async def async_main(argv: Sequence[str] | None = None) -> int:
    """Async daemon CLI entrypoint."""
    configure_logging()
    args = parse_args(argv)
    daemon_settings = settings_from_args(args)
    shutdown = ShutdownFlag()
    install_signal_handlers(shutdown)
    summary = await run_daemon(daemon_settings=daemon_settings, shutdown=shutdown)
    return 1 if summary.errors else 0


def main(argv: Sequence[str] | None = None) -> int:
    """Synchronous daemon CLI entrypoint."""
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
