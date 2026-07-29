"""Supabase repository for persisted leads."""

import inspect
from datetime import UTC, date, datetime
from typing import Any

from supabase import AsyncClient

from app.models.classification import LeadClassified, LeadClassificationStatus

LEAD_SELECT_FIELDS = "id,source,classification_status,created_at"
LEAD_CLASSIFICATION_SELECT_FIELDS = (
    "id,source,classification_status,created_at,"
    "customer_name,email,phone,requested_service,urgency,lead_score,ai_summary,"
    "classification_error,classified_at,classification_model"
)
PENDING_LEAD_SELECT_FIELDS = "id,raw_message,source,classification_status,created_at"
SAFE_FAILURE_REASONS = {
    "invalid_json",
    "invalid_json_shape",
    "invalid_classification_payload",
    "classification_client_error",
    "empty_classification_response",
    "invalid_raw_message",
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


def _terminal_timestamp_value(classified_at: datetime | None) -> str:
    terminal_timestamp = classified_at or datetime.now(UTC)
    if terminal_timestamp.tzinfo is None:
        terminal_timestamp = terminal_timestamp.replace(tzinfo=UTC)
    return terminal_timestamp.astimezone(UTC).isoformat()


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
            "classification_status": LeadClassificationStatus.CLASSIFIED.value,
        }

    raise LeadRepositoryUpdateError("classification result must be classified or failed")


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
