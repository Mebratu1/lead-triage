"""Supabase repository for persisted leads."""

import inspect
from datetime import UTC, date, datetime, timedelta
from typing import Any

from supabase import AsyncClient

from app.models.classification import (
    LeadClassified,
    LeadClassificationQueueMetrics,
    LeadClassificationStatus,
)
from app.models.lead import LeadIntegrationStatus

LEAD_SELECT_FIELDS = "id,source,classification_status,created_at"
LEAD_CLASSIFICATION_SELECT_FIELDS = (
    "id,source,classification_status,created_at,"
    "customer_name,email,phone,requested_service,urgency,lead_score,ai_summary,"
    "classification_error,classified_at,classification_model,"
    "classification_claimed_at,classification_claimed_by,"
    "classification_attempt_count,last_classification_error,"
    "next_classification_attempt_at"
)
LEAD_RETRY_SELECT_FIELDS = (
    "id,classification_status,classification_claimed_at,classification_claimed_by,"
    "classification_attempt_count,last_classification_error,"
    "next_classification_attempt_at"
)
CLAIMED_LEAD_SELECT_FIELDS = (
    "id,raw_message,source,classification_status,created_at,"
    "classification_claimed_at,classification_claimed_by,"
    "classification_attempt_count"
)
PENDING_LEAD_SELECT_FIELDS = "id,raw_message,source,classification_status,created_at"
LEAD_PUBLIC_SELECT_FIELDS = (
    "id,source,raw_message,customer_name,email,phone,classification_status,"
    "urgency,ai_summary,classification_attempt_count,created_at,classified_at,"
    "integration_status,integration_last_synced_at"
)
LEAD_INTEGRATION_SELECT_FIELDS = (
    "id,integration_status,integration_last_synced_at,integration_error,"
    "integration_next_attempt_at"
)
SAFE_FAILURE_REASONS = {
    "invalid_json",
    "invalid_json_shape",
    "invalid_classification_payload",
    "classification_client_error",
    "empty_classification_response",
    "invalid_raw_message",
}
SAFE_RETRY_REASONS = {
    "classification_client_error",
    "classification_update_failed",
}
SAFE_INTEGRATION_REASONS = {
    "crm_dispatch_failed",
    "crm_sync_failed",
    "crm_update_failed",
}


class LeadRepositoryError(Exception):
    """Base repository error."""


class LeadRepositoryLookupError(LeadRepositoryError):
    """Raised when a lead lookup fails."""


class LeadRepositoryInsertError(LeadRepositoryError):
    """Raised when lead insertion fails."""


class LeadRepositoryUpdateError(LeadRepositoryError):
    """Raised when lead update fails."""


class LeadRepositoryUpdateConflict(LeadRepositoryUpdateError):
    """Raised when a lead cannot be atomically updated from pending."""


class LeadRepositoryUnexpectedResult(LeadRepositoryError):
    """Raised when Supabase returns an unexpected result shape."""


class LeadRepositoryUniqueConflict(LeadRepositoryInsertError):
    """Raised when the database unique constraint rejects an insert."""


async def _resolve(value: Any) -> Any:
    """Support real Supabase builders and lightweight test doubles."""
    if inspect.isawaitable(value):
        return await value
    return value


def _first_row(response: Any) -> dict[str, Any] | None:
    data = getattr(response, "data", None)
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        return data
    return None


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    if isinstance(data, list):
        if all(isinstance(row, dict) for row in data):
            return data
    if isinstance(data, dict):
        return [data]
    raise LeadRepositoryUnexpectedResult


def _count(response: Any) -> int:
    count = getattr(response, "count", None)
    if isinstance(count, int):
        return count

    data = getattr(response, "data", None)
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        return 1

    raise LeadRepositoryUnexpectedResult


def _is_unique_constraint_error(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    if code == "23505":
        return True

    message = str(exc).lower()
    return (
        "23505" in message
        or "duplicate key" in message
        or "unique constraint" in message
        or "idx_leads_idempotency_bucket_unique" in message
    )


def _safe_failure_reason(reason: str | None) -> str:
    if reason in SAFE_FAILURE_REASONS:
        return reason
    return "classification_failed"


def _safe_retry_reason(reason: str | None) -> str:
    if reason in SAFE_RETRY_REASONS:
        return reason
    return "classification_retry_failed"


def _safe_integration_reason(reason: str | None) -> str:
    if reason in SAFE_INTEGRATION_REASONS:
        return reason
    return "crm_sync_failed"


def _terminal_timestamp_value(classified_at: datetime | None) -> str:
    terminal_timestamp = classified_at or datetime.now(UTC)
    if terminal_timestamp.tzinfo is None:
        terminal_timestamp = terminal_timestamp.replace(tzinfo=UTC)
    return terminal_timestamp.astimezone(UTC).isoformat()


def _retry_timestamp_value(now: datetime | None, retry_after_seconds: int) -> str:
    if retry_after_seconds < 0:
        raise LeadRepositoryUpdateError("retry_after_seconds must not be negative")

    retry_base = now or datetime.now(UTC)
    if retry_base.tzinfo is None:
        retry_base = retry_base.replace(tzinfo=UTC)
    retry_at = retry_base.astimezone(UTC) + timedelta(seconds=retry_after_seconds)
    return retry_at.isoformat()


def _utc_isoformat(value: datetime) -> str:
    """Render a datetime as a stable UTC ISO timestamp for Supabase filters."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _classification_update_payload(
    classification: LeadClassified,
    classification_model: str | None,
    classified_at: datetime | None,
) -> dict[str, Any]:
    terminal_timestamp_value = _terminal_timestamp_value(classified_at)

    if classification.classification_status == LeadClassificationStatus.FAILED:
        return {
            "customer_name": None,
            "email": None,
            "phone": None,
            "requested_service": None,
            "urgency": None,
            "lead_score": None,
            "ai_summary": None,
            "classification_error": _safe_failure_reason(classification.error_reason),
            "classified_at": terminal_timestamp_value,
            "classification_model": classification_model,
            "classification_claimed_at": None,
            "classification_claimed_by": None,
            "last_classification_error": _safe_failure_reason(
                classification.error_reason
            ),
            "next_classification_attempt_at": None,
            "classification_status": LeadClassificationStatus.FAILED.value,
        }

    if classification.classification_status == LeadClassificationStatus.CLASSIFIED:
        urgency = classification.urgency.value if classification.urgency else None
        return {
            "customer_name": classification.customer_name,
            "email": classification.email,
            "phone": classification.phone,
            "requested_service": classification.requested_service,
            "urgency": urgency,
            "lead_score": classification.lead_score,
            "ai_summary": classification.ai_summary,
            "classification_error": None,
            "classified_at": terminal_timestamp_value,
            "classification_model": classification_model,
            "classification_claimed_at": None,
            "classification_claimed_by": None,
            "last_classification_error": None,
            "next_classification_attempt_at": None,
            "classification_status": LeadClassificationStatus.CLASSIFIED.value,
        }

    raise LeadRepositoryUpdateError("classification result must be classified or failed")


def _integration_update_payload(
    integration_status: LeadIntegrationStatus,
    error_reason: str | None,
    synced_at: datetime | None,
    retry_after_seconds: int | None,
    now: datetime | None,
) -> dict[str, Any]:
    """Build a safe outbound CRM sync tracking update payload."""
    if integration_status == LeadIntegrationStatus.SYNCED:
        return {
            "integration_status": LeadIntegrationStatus.SYNCED.value,
            "integration_last_synced_at": _terminal_timestamp_value(synced_at),
            "integration_error": None,
            "integration_next_attempt_at": None,
        }

    if integration_status == LeadIntegrationStatus.FAILED:
        if retry_after_seconds is None:
            retry_after_seconds = 300
        return {
            "integration_status": LeadIntegrationStatus.FAILED.value,
            "integration_last_synced_at": None,
            "integration_error": _safe_integration_reason(error_reason),
            "integration_next_attempt_at": _retry_timestamp_value(
                now=now,
                retry_after_seconds=retry_after_seconds,
            ),
        }

    return {
        "integration_status": LeadIntegrationStatus.PENDING.value,
        "integration_last_synced_at": None,
        "integration_error": None,
        "integration_next_attempt_at": None,
    }


async def find_by_idempotency(
    db: AsyncClient,
    idempotency_key: str,
    deduplication_bucket: date,
) -> dict[str, Any] | None:
    """Find an existing lead within the current deduplication bucket."""
    try:
        query = await _resolve(db.table("leads"))
        query = await _resolve(query.select(LEAD_SELECT_FIELDS))
        query = await _resolve(query.eq("idempotency_key", idempotency_key))
        query = await _resolve(
            query.eq("deduplication_bucket", deduplication_bucket.isoformat())
        )
        query = await _resolve(query.limit(1))
        response = await _resolve(query.execute())
    except Exception as exc:
        raise LeadRepositoryLookupError from exc

    return _first_row(response)


async def fetch_pending_leads(
    db: AsyncClient,
    limit: int,
) -> list[dict[str, Any]]:
    """Fetch the oldest pending leads for classification work."""
    if limit < 1:
        raise ValueError("limit must be at least 1")

    try:
        query = await _resolve(db.table("leads"))
        query = await _resolve(query.select(PENDING_LEAD_SELECT_FIELDS))
        query = await _resolve(
            query.eq(
                "classification_status",
                LeadClassificationStatus.PENDING.value,
            )
        )
        query = await _resolve(query.order("created_at", desc=False))
        query = await _resolve(query.limit(limit))
        response = await _resolve(query.execute())
        rows = _rows(response)
    except LeadRepositoryUnexpectedResult as exc:
        raise LeadRepositoryLookupError from exc
    except Exception as exc:
        raise LeadRepositoryLookupError from exc

    return rows


async def _count_pending_leads(
    db: AsyncClient,
    max_attempts: int | None = None,
    below_max_attempts: int | None = None,
    backoff_after: datetime | None = None,
) -> int:
    """Count pending leads with optional queue-health filters."""
    try:
        query = await _resolve(db.table("leads"))
        query = await _resolve(query.select("id", count="exact"))
        query = await _resolve(
            query.eq(
                "classification_status",
                LeadClassificationStatus.PENDING.value,
            )
        )
        if max_attempts is not None:
            query = await _resolve(
                query.gte("classification_attempt_count", max_attempts)
            )
        if below_max_attempts is not None:
            query = await _resolve(
                query.lt("classification_attempt_count", below_max_attempts)
            )
        if backoff_after is not None:
            query = await _resolve(
                query.gt(
                    "next_classification_attempt_at",
                    backoff_after.astimezone(UTC).isoformat(),
                )
            )
        query = await _resolve(query.limit(0))
        response = await _resolve(query.execute())
        return _count(response)
    except LeadRepositoryUnexpectedResult as exc:
        raise LeadRepositoryLookupError from exc
    except Exception as exc:
        raise LeadRepositoryLookupError from exc


async def fetch_classification_queue_metrics(
    db: AsyncClient,
    max_attempts: int,
    now: datetime | None = None,
) -> LeadClassificationQueueMetrics:
    """Fetch aggregate queue health counters for classification monitoring."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)

    pending_count = await _count_pending_leads(db=db)
    backoff_count = await _count_pending_leads(
        db=db,
        below_max_attempts=max_attempts,
        backoff_after=current_time,
    )
    exhausted_count = await _count_pending_leads(db=db, max_attempts=max_attempts)

    return LeadClassificationQueueMetrics(
        pending_count=pending_count,
        backoff_count=backoff_count,
        exhausted_count=exhausted_count,
        max_attempts=max_attempts,
    )


async def list_leads(
    db: AsyncClient,
    limit: int,
    offset: int,
    classification_status: str | None = None,
    urgency: str | None = None,
    source: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """List admin-visible leads with optional filters and total count."""
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    if offset < 0:
        raise ValueError("offset must be at least 0")

    try:
        query = await _resolve(db.table("leads"))
        query = await _resolve(query.select(LEAD_PUBLIC_SELECT_FIELDS, count="exact"))
        if classification_status is not None:
            query = await _resolve(
                query.eq("classification_status", classification_status)
            )
        if urgency is not None:
            query = await _resolve(query.eq("urgency", urgency))
        if source is not None:
            query = await _resolve(query.eq("source", source))
        if start_date is not None:
            query = await _resolve(query.gte("created_at", _utc_isoformat(start_date)))
        if end_date is not None:
            query = await _resolve(query.lte("created_at", _utc_isoformat(end_date)))
        query = await _resolve(query.order("created_at", desc=True))
        query = await _resolve(query.range(offset, offset + limit - 1))
        response = await _resolve(query.execute())
        rows = _rows(response)
        total = _count(response)
    except LeadRepositoryUnexpectedResult as exc:
        raise LeadRepositoryLookupError from exc
    except Exception as exc:
        raise LeadRepositoryLookupError from exc

    return rows, total


async def get_lead_by_id(
    db: AsyncClient,
    lead_id: str,
) -> dict[str, Any] | None:
    """Fetch one admin-visible lead by UUID."""
    try:
        query = await _resolve(db.table("leads"))
        query = await _resolve(query.select(LEAD_PUBLIC_SELECT_FIELDS))
        query = await _resolve(query.eq("id", lead_id))
        query = await _resolve(query.limit(1))
        response = await _resolve(query.execute())
    except Exception as exc:
        raise LeadRepositoryLookupError from exc

    return _first_row(response)


async def update_lead_integration_status(
    db: AsyncClient,
    lead_id: str,
    integration_status: LeadIntegrationStatus,
    error_reason: str | None = None,
    synced_at: datetime | None = None,
    retry_after_seconds: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Update outbound CRM sync tracking fields for an existing lead."""
    payload = _integration_update_payload(
        integration_status=integration_status,
        error_reason=error_reason,
        synced_at=synced_at,
        retry_after_seconds=retry_after_seconds,
        now=now,
    )

    try:
        query = await _resolve(db.table("leads"))
        query = await _resolve(query.update(payload))
        query = await _resolve(query.eq("id", lead_id))
        query = await _resolve(query.select(LEAD_INTEGRATION_SELECT_FIELDS))
        response = await _resolve(query.execute())
    except Exception as exc:
        raise LeadRepositoryUpdateError from exc

    row = _first_row(response)
    if row is None:
        raise LeadRepositoryUpdateConflict

    return row


async def claim_pending_leads(
    db: AsyncClient,
    limit: int,
    worker_id: str,
    claim_timeout_seconds: int,
    max_attempts: int,
) -> list[dict[str, Any]]:
    """Atomically claim pending leads for one classification worker."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if not worker_id.strip():
        raise ValueError("worker_id is required")
    if claim_timeout_seconds < 1:
        raise ValueError("claim_timeout_seconds must be at least 1")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    try:
        query = await _resolve(
            db.rpc(
                "claim_pending_leads_for_classification",
                {
                    "p_batch_limit": limit,
                    "p_worker_id": worker_id,
                    "p_claim_timeout_seconds": claim_timeout_seconds,
                    "p_max_attempts": max_attempts,
                },
            )
        )
        query = await _resolve(query.select(CLAIMED_LEAD_SELECT_FIELDS))
        response = await _resolve(query.execute())
        rows = _rows(response)
    except LeadRepositoryUnexpectedResult as exc:
        raise LeadRepositoryLookupError from exc
    except Exception as exc:
        raise LeadRepositoryLookupError from exc

    return rows


async def insert_lead(
    db: AsyncClient,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Insert a lead and return the saved row."""
    try:
        query = await _resolve(db.table("leads"))
        query = await _resolve(query.insert(payload))
        query = await _resolve(query.select(LEAD_SELECT_FIELDS))
        response = await _resolve(query.execute())
    except Exception as exc:
        if _is_unique_constraint_error(exc):
            raise LeadRepositoryUniqueConflict from exc
        raise LeadRepositoryInsertError from exc

    row = _first_row(response)
    if row is None:
        raise LeadRepositoryUnexpectedResult

    return row


async def update_lead_classification(
    db: AsyncClient,
    lead_id: str,
    classification: LeadClassified,
    classification_model: str | None = None,
    classified_at: datetime | None = None,
    claim_owner_id: str | None = None,
) -> dict[str, Any]:
    """Atomically update a pending lead with classification results."""
    payload = _classification_update_payload(
        classification=classification,
        classification_model=classification_model,
        classified_at=classified_at,
    )

    try:
        query = await _resolve(db.table("leads"))
        query = await _resolve(query.update(payload))
        query = await _resolve(query.eq("id", lead_id))
        query = await _resolve(
            query.eq(
                "classification_status",
                LeadClassificationStatus.PENDING.value,
            )
        )
        if claim_owner_id is not None:
            query = await _resolve(query.eq("classification_claimed_by", claim_owner_id))
        query = await _resolve(query.select(LEAD_CLASSIFICATION_SELECT_FIELDS))
        response = await _resolve(query.execute())
    except LeadRepositoryUpdateError:
        raise
    except Exception as exc:
        raise LeadRepositoryUpdateError from exc

    row = _first_row(response)
    if row is None:
        raise LeadRepositoryUpdateConflict

    return row


async def release_lead_classification_claim(
    db: AsyncClient,
    lead_id: str,
    worker_id: str,
    error_reason: str,
    retry_after_seconds: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Release a claimed pending lead for a future retry."""
    payload = {
        "classification_claimed_at": None,
        "classification_claimed_by": None,
        "last_classification_error": _safe_retry_reason(error_reason),
        "next_classification_attempt_at": _retry_timestamp_value(
            now=now,
            retry_after_seconds=retry_after_seconds,
        ),
    }

    try:
        query = await _resolve(db.table("leads"))
        query = await _resolve(query.update(payload))
        query = await _resolve(query.eq("id", lead_id))
        query = await _resolve(
            query.eq(
                "classification_status",
                LeadClassificationStatus.PENDING.value,
            )
        )
        query = await _resolve(query.eq("classification_claimed_by", worker_id))
        query = await _resolve(query.select(LEAD_RETRY_SELECT_FIELDS))
        response = await _resolve(query.execute())
    except LeadRepositoryUpdateError:
        raise
    except Exception as exc:
        raise LeadRepositoryUpdateError from exc

    row = _first_row(response)
    if row is None:
        raise LeadRepositoryUpdateConflict

    return row
