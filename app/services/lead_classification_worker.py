"""Worker-safe orchestration for classifying pending leads.

This module exposes callable batch logic only. It does not start background
tasks, schedulers, queues, or long-running loops.
"""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from supabase import AsyncClient

from app.models.classification import (
    LeadClassificationBatchResult,
    LeadClassificationPersistenceStatus,
    LeadClassificationStatus,
    LeadClassificationWorkItemResult,
    LeadClassified,
)
from app.services.lead_classification import (
    LeadClassificationClient,
    classify_raw_message,
)
from app.services.lead_persistence import (
    LeadClassificationUpdateConflict,
    LeadClassificationUpdateFailed,
    LeadLookupFailed,
    claim_pending_leads_for_classification,
    persist_lead_classification,
    release_lead_classification_for_retry,
)

logger = logging.getLogger(__name__)

DEFAULT_CLASSIFICATION_BATCH_SIZE = 10
MAX_CLASSIFICATION_BATCH_SIZE = 100
DEFAULT_CLAIM_TIMEOUT_SECONDS = 15 * 60
DEFAULT_RETRY_AFTER_SECONDS = 5 * 60
DEFAULT_MAX_CLASSIFICATION_ATTEMPTS = 5


class LeadClassificationBatchFetchFailed(Exception):
    """Raised when pending leads cannot be fetched."""


def _validate_batch_limit(limit: int) -> int:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if limit > MAX_CLASSIFICATION_BATCH_SIZE:
        raise ValueError(
            f"limit must not exceed {MAX_CLASSIFICATION_BATCH_SIZE}"
        )
    return limit


def _classification_model_name(
    client: LeadClassificationClient,
    classification_model: str | None,
) -> str | None:
    if classification_model:
        return classification_model

    model = getattr(client, "model", None)
    return model if isinstance(model, str) and model else None


def _worker_id(worker_id: str | None) -> str:
    if worker_id and worker_id.strip():
        return worker_id.strip()
    return f"lead-classifier-{uuid4()}"


def _result(
    lead_id: str,
    persistence_status: LeadClassificationPersistenceStatus,
    classification_status: LeadClassificationStatus | None = None,
    error_reason: str | None = None,
) -> LeadClassificationWorkItemResult:
    return LeadClassificationWorkItemResult(
        lead_id=lead_id,
        classification_status=classification_status,
        persistence_status=persistence_status,
        error_reason=error_reason,
    )


def _summarize(
    fetched: int,
    results: list[LeadClassificationWorkItemResult],
) -> LeadClassificationBatchResult:
    saved_results = [
        item
        for item in results
        if item.persistence_status == LeadClassificationPersistenceStatus.SAVED
    ]
    return LeadClassificationBatchResult(
        fetched=fetched,
        saved=len(saved_results),
        classified=sum(
            item.classification_status == LeadClassificationStatus.CLASSIFIED
            for item in saved_results
        ),
        failed=sum(
            item.classification_status == LeadClassificationStatus.FAILED
            for item in saved_results
        ),
        skipped=sum(
            item.persistence_status == LeadClassificationPersistenceStatus.SKIPPED
            for item in results
        ),
        errors=sum(
            item.persistence_status == LeadClassificationPersistenceStatus.ERROR
            for item in results
        ),
        results=results,
    )


async def _classify_lead(
    lead: dict[str, Any],
    client: LeadClassificationClient,
) -> LeadClassified:
    raw_message = lead.get("raw_message")
    if not isinstance(raw_message, str) or not raw_message.strip():
        return LeadClassified.failed("invalid_raw_message")

    return await classify_raw_message(raw_message=raw_message, client=client)


async def process_pending_leads_batch(
    db: AsyncClient,
    client: LeadClassificationClient,
    limit: int = DEFAULT_CLASSIFICATION_BATCH_SIZE,
    classification_model: str | None = None,
    classified_at: datetime | None = None,
    worker_id: str | None = None,
    claim_timeout_seconds: int = DEFAULT_CLAIM_TIMEOUT_SECONDS,
    retry_after_seconds: int = DEFAULT_RETRY_AFTER_SECONDS,
    max_attempts: int = DEFAULT_MAX_CLASSIFICATION_ATTEMPTS,
) -> LeadClassificationBatchResult:
    """Classify and persist one bounded batch of pending leads."""
    batch_limit = _validate_batch_limit(limit)
    active_worker_id = _worker_id(worker_id)
    model_name = _classification_model_name(
        client=client,
        classification_model=classification_model,
    )
    completed_at = classified_at or datetime.now(UTC)

    try:
        claimed_leads = await claim_pending_leads_for_classification(
            db=db,
            limit=batch_limit,
            worker_id=active_worker_id,
            claim_timeout_seconds=claim_timeout_seconds,
            max_attempts=max_attempts,
        )
    except LeadLookupFailed as exc:
        raise LeadClassificationBatchFetchFailed from exc

    results: list[LeadClassificationWorkItemResult] = []
    for lead in claimed_leads:
        lead_id = str(lead["id"])
        try:
            classification = await _classify_lead(lead=lead, client=client)
        except Exception:
            logger.warning(
                "Lead classification client failed",
                extra={"lead_id": lead_id},
            )
            try:
                await release_lead_classification_for_retry(
                    db=db,
                    lead_id=lead_id,
                    worker_id=active_worker_id,
                    error_reason="classification_client_error",
                    retry_after_seconds=retry_after_seconds,
                    now=completed_at,
                )
            except (LeadClassificationUpdateConflict, LeadClassificationUpdateFailed):
                logger.warning(
                    "Lead classification retry release failed",
                    extra={"lead_id": lead_id},
                )
                results.append(
                    _result(
                        lead_id=lead_id,
                        persistence_status=LeadClassificationPersistenceStatus.ERROR,
                        error_reason="classification_retry_release_failed",
                    )
                )
                continue

            results.append(
                _result(
                    lead_id=lead_id,
                    persistence_status=LeadClassificationPersistenceStatus.ERROR,
                    error_reason="classification_client_error",
                )
            )
            continue

        try:
            await persist_lead_classification(
                db=db,
                lead_id=lead_id,
                classification=classification,
                classification_model=model_name,
                classified_at=completed_at,
                claim_owner_id=active_worker_id,
            )
        except LeadClassificationUpdateConflict:
            results.append(
                _result(
                    lead_id=lead_id,
                    persistence_status=LeadClassificationPersistenceStatus.SKIPPED,
                    error_reason="lead_not_pending",
                )
            )
            continue
        except LeadClassificationUpdateFailed:
            logger.warning(
                "Lead classification persistence failed",
                extra={"lead_id": lead_id},
            )
            results.append(
                _result(
                    lead_id=lead_id,
                    persistence_status=LeadClassificationPersistenceStatus.ERROR,
                    classification_status=classification.classification_status,
                    error_reason="classification_update_failed",
                )
            )
            continue

        results.append(
            _result(
                lead_id=lead_id,
                persistence_status=LeadClassificationPersistenceStatus.SAVED,
                classification_status=classification.classification_status,
                error_reason=classification.error_reason,
            )
        )

    return _summarize(fetched=len(claimed_leads), results=results)
