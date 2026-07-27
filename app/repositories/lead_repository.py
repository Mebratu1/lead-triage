"""Supabase repository for persisted leads."""

import inspect
from datetime import date
from typing import Any

from supabase import AsyncClient

LEAD_SELECT_FIELDS = "id,source,classification_status,created_at"


class LeadRepositoryError(Exception):
    """Base repository error."""


class LeadRepositoryLookupError(LeadRepositoryError):
    """Raised when a lead lookup fails."""


class LeadRepositoryInsertError(LeadRepositoryError):
    """Raised when lead insertion fails."""


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
