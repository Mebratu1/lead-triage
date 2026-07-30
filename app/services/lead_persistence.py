"""Idempotent persistence orchestration for lead inquiries."""

import hashlib
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from supabase import AsyncClient

from app.config import settings
from app.models.classification import LeadClassified
from app.models.classification import LeadClassificationQueueMetrics
from app.models.lead import LeadCreateRequest, LeadPersistedResponse
from app.repositories.lead_repository import (
    LeadRepositoryInsertError,
    LeadRepositoryLookupError,
    LeadRepositoryUpdateConflict,
    LeadRepositoryUpdateError,
    LeadRepositoryUnexpectedResult,
    LeadRepositoryUniqueConflict,
    claim_pending_leads,
    fetch_classification_queue_metrics,
    fetch_pending_leads,
    find_by_idempotency,
    insert_lead,
    release_lead_classification_claim,
    update_lead_classification,
)

CLASSIFICATION_PENDING: Literal["pending"] = "pending"
EPOCH_DATE = date(1970, 1, 1)


class LeadPersistenceError(Exception):
    """Base error for safe lead persistence failures."""


class LeadLookupFailed(LeadPersistenceError):
    """Raised when the existing-lead lookup fails."""


class LeadInsertFailed(LeadPersistenceError):
    """Raised when lead insertion fails."""


class LeadUnexpectedPersistenceFailure(LeadPersistenceError):
    """Raised when persistence returns an unexpected result."""


class LeadClassificationUpdateFailed(LeadPersistenceError):
    """Raised when saving classification output fails."""


class LeadClassificationUpdateConflict(LeadClassificationUpdateFailed):
    """Raised when the lead is missing or no longer pending."""


def normalize_source(source: str) -> str:
    """Normalize source before hashing or saving."""
    return source.strip().lower()


def normalize_message(message: str) -> str:
    """Normalize message text for stable duplicate detection."""
    return " ".join(message.strip().split())


def generate_idempotency_key(source: str, message: str) -> str:
    """Create SHA-256(normalized_source + newline + normalized_message)."""
    normalized_source = normalize_source(source)
    normalized_message = normalize_message(message)
    return hashlib.sha256(
        f"{normalized_source}\n{normalized_message}".encode("utf-8")
    ).hexdigest()


def deduplication_bucket_for(
    moment: datetime | None = None,
    window_days: int | None = None,
) -> date:
    """Return the UTC duplicate-window bucket for the supplied timestamp."""
    configured_window_days = (
        settings.dedup_window_days if window_days is None else window_days
    )
    if configured_window_days < 1:
        raise ValueError("deduplication window must be at least one day")

    current_moment = moment or datetime.now(UTC)
    if current_moment.tzinfo is None:
        current_moment = current_moment.replace(tzinfo=UTC)

    current_date = current_moment.astimezone(UTC).date()
    days_since_epoch = (current_date - EPOCH_DATE).days
    bucket_start_days = (
        days_since_epoch // configured_window_days
    ) * configured_window_days
    return EPOCH_DATE + timedelta(days=bucket_start_days)


def _to_response(row: dict, duplicate: bool) -> LeadPersistedResponse:
    return LeadPersistedResponse(
        id=row["id"],
        source=row["source"],
        classification_status=row["classification_status"],
        created_at=row["created_at"],
        duplicate=duplicate,
    )


async def _find_existing_lead(
    db: AsyncClient,
    idempotency_key: str,
    deduplication_bucket: date,
) -> dict | None:
    try:
        return await find_by_idempotency(
            db=db,
            idempotency_key=idempotency_key,
            deduplication_bucket=deduplication_bucket,
        )
    except LeadRepositoryLookupError as exc:
        raise LeadLookupFailed from exc


async def persist_lead(
    db: AsyncClient,
    request: LeadCreateRequest,
    now: datetime | None = None,
) -> LeadPersistedResponse:
    """Persist a lead once within the configured duplicate window."""
    source = normalize_source(request.source)
    raw_message = request.message
    idempotency_key = generate_idempotency_key(
        source=source,
        message=raw_message,
    )
    deduplication_bucket = deduplication_bucket_for(moment=now)

    existing_lead = await _find_existing_lead(
        db=db,
        idempotency_key=idempotency_key,
        deduplication_bucket=deduplication_bucket,
    )
    if existing_lead is not None:
        return _to_response(existing_lead, duplicate=True)

    payload = {
        "idempotency_key": idempotency_key,
        "deduplication_bucket": deduplication_bucket.isoformat(),
        "source": source,
        "raw_message": raw_message,
        "classification_status": CLASSIFICATION_PENDING,
    }

    try:
        inserted_lead = await insert_lead(db=db, payload=payload)
    except LeadRepositoryUniqueConflict:
        existing_after_race = await _find_existing_lead(
            db=db,
            idempotency_key=idempotency_key,
            deduplication_bucket=deduplication_bucket,
        )
        if existing_after_race is not None:
            return _to_response(existing_after_race, duplicate=True)
        raise LeadInsertFailed
    except LeadRepositoryInsertError as exc:
        raise LeadInsertFailed from exc
    except LeadRepositoryUnexpectedResult as exc:
        raise LeadUnexpectedPersistenceFailure from exc

    return _to_response(inserted_lead, duplicate=False)


async def persist_lead_classification(
    db: AsyncClient,
    lead_id: str,
    classification: LeadClassified,
    classification_model: str | None = None,
    classified_at: datetime | None = None,
    claim_owner_id: str | None = None,
) -> dict[str, Any]:
    """Persist classification output to a pending lead record."""
    try:
        return await update_lead_classification(
            db=db,
            lead_id=lead_id,
            classification=classification,
            classification_model=classification_model,
            classified_at=classified_at,
            claim_owner_id=claim_owner_id,
        )
    except LeadRepositoryUpdateConflict as exc:
        raise LeadClassificationUpdateConflict from exc
    except LeadRepositoryUpdateError as exc:
        raise LeadClassificationUpdateFailed from exc


async def get_pending_leads_for_classification(
    db: AsyncClient,
    limit: int,
) -> list[dict[str, Any]]:
    """Fetch pending leads for classification orchestration."""
    try:
        return await fetch_pending_leads(db=db, limit=limit)
    except LeadRepositoryLookupError as exc:
        raise LeadLookupFailed from exc


async def get_classification_queue_metrics(
    db: AsyncClient,
    max_attempts: int,
    now: datetime | None = None,
) -> LeadClassificationQueueMetrics:
    """Fetch aggregate classification queue health counters."""
    try:
        return await fetch_classification_queue_metrics(
            db=db,
            max_attempts=max_attempts,
            now=now,
        )
    except LeadRepositoryLookupError as exc:
        raise LeadLookupFailed from exc


async def claim_pending_leads_for_classification(
    db: AsyncClient,
    limit: int,
    worker_id: str,
    claim_timeout_seconds: int,
    max_attempts: int,
) -> list[dict[str, Any]]:
    """Atomically claim pending leads for classification."""
    try:
        return await claim_pending_leads(
            db=db,
            limit=limit,
            worker_id=worker_id,
            claim_timeout_seconds=claim_timeout_seconds,
            max_attempts=max_attempts,
        )
    except LeadRepositoryLookupError as exc:
        raise LeadLookupFailed from exc


async def release_lead_classification_for_retry(
    db: AsyncClient,
    lead_id: str,
    worker_id: str,
    error_reason: str,
    retry_after_seconds: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Release a claimed lead after retryable classification failure."""
    try:
        return await release_lead_classification_claim(
            db=db,
            lead_id=lead_id,
            worker_id=worker_id,
            error_reason=error_reason,
            retry_after_seconds=retry_after_seconds,
            now=now,
        )
    except LeadRepositoryUpdateConflict as exc:
        raise LeadClassificationUpdateConflict from exc
    except LeadRepositoryUpdateError as exc:
        raise LeadClassificationUpdateFailed from exc
