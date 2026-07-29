"""Manual CLI runner for classifying pending leads.

Run with:
    python -m app.jobs.classify_pending_leads --limit 10
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from supabase import AsyncClient

from app.config import settings
from app.db.client import SupabaseClient
from app.models.classification import LeadClassificationBatchResult
from app.services.lead_classification import LeadClassificationClient
from app.services.lead_classification_worker import (
    DEFAULT_CLASSIFICATION_BATCH_SIZE,
    MAX_CLASSIFICATION_BATCH_SIZE,
    process_pending_leads_batch,
)
from app.services.lead_persistence import get_pending_leads_for_classification
from app.services.openai_client import OpenAILeadClassificationClient

logger = logging.getLogger(__name__)

FetchPendingLeads = Callable[[AsyncClient, int], Awaitable[list[dict[str, Any]]]]
ProcessBatch = Callable[
    [AsyncClient, LeadClassificationClient, int],
    Awaitable[LeadClassificationBatchResult],
]


@dataclass(frozen=True)
class ClassificationJobResult:
    """Summary returned by the manual classification job runner."""

    dry_run: bool
    fetched: int
    saved: int = 0
    classified: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0

    @classmethod
    def from_batch(
        cls,
        batch: LeadClassificationBatchResult,
    ) -> "ClassificationJobResult":
        """Build a CLI result from orchestration output."""
        return cls(
            dry_run=False,
            fetched=batch.fetched,
            saved=batch.saved,
            classified=batch.classified,
            failed=batch.failed,
            skipped=batch.skipped,
            errors=batch.errors,
        )


def _limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer") from exc

    if parsed < 1:
        raise argparse.ArgumentTypeError("limit must be at least 1")
    if parsed > MAX_CLASSIFICATION_BATCH_SIZE:
        raise argparse.ArgumentTypeError(
            f"limit must not exceed {MAX_CLASSIFICATION_BATCH_SIZE}"
        )
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the manual job argument parser."""
    parser = argparse.ArgumentParser(
        description="Manually classify a bounded batch of pending leads.",
    )
    parser.add_argument(
        "--limit",
        type=_limit,
        default=DEFAULT_CLASSIFICATION_BATCH_SIZE,
        help=(
            "Maximum number of pending leads to inspect. "
            f"Default: {DEFAULT_CLASSIFICATION_BATCH_SIZE}."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch pending leads and report counts without OpenAI calls or updates.",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    return build_parser().parse_args(argv)


def configure_logging() -> None:
    """Configure CLI logging without exposing lead message contents."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(levelname)s:%(name)s:%(message)s",
    )


async def _get_db() -> AsyncClient:
    return await SupabaseClient.get_client()


async def _close_db() -> None:
    await SupabaseClient.close()


async def _process_batch(
    db: AsyncClient,
    client: LeadClassificationClient,
    limit: int,
) -> LeadClassificationBatchResult:
    return await process_pending_leads_batch(
        db=db,
        client=client,
        limit=limit,
    )


async def run_classification_job(
    limit: int = DEFAULT_CLASSIFICATION_BATCH_SIZE,
    dry_run: bool = False,
    db: AsyncClient | None = None,
    client: LeadClassificationClient | None = None,
    db_factory: Callable[[], Awaitable[AsyncClient]] = _get_db,
    db_close: Callable[[], Awaitable[None]] = _close_db,
    fetch_pending: FetchPendingLeads = get_pending_leads_for_classification,
    process_batch: ProcessBatch = _process_batch,
) -> ClassificationJobResult:
    """Run one manual classification job invocation."""
    batch_limit = _limit(str(limit))
    created_db = db is None
    active_db = db or await db_factory()

    try:
        if dry_run:
            pending_leads = await fetch_pending(active_db, batch_limit)
            result = ClassificationJobResult(
                dry_run=True,
                fetched=len(pending_leads),
            )
            logger.info(
                "Lead classification dry run completed fetched=%s limit=%s",
                result.fetched,
                batch_limit,
            )
            return result

        active_client = client or OpenAILeadClassificationClient()
        batch = await process_batch(active_db, active_client, batch_limit)
        result = ClassificationJobResult.from_batch(batch)
        logger.info(
            "Lead classification job completed fetched=%s saved=%s "
            "classified=%s failed=%s skipped=%s errors=%s",
            result.fetched,
            result.saved,
            result.classified,
            result.failed,
            result.skipped,
            result.errors,
        )
        return result
    finally:
        if created_db:
            await db_close()


async def async_main(argv: Sequence[str] | None = None) -> int:
    """Async CLI entrypoint."""
    configure_logging()
    args = parse_args(argv)

    try:
        result = await run_classification_job(
            limit=args.limit,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        logger.error(
            "Lead classification job failed error_type=%s",
            type(exc).__name__,
        )
        return 1

    return 1 if result.errors else 0


def main(argv: Sequence[str] | None = None) -> int:
    """Synchronous CLI entrypoint."""
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
