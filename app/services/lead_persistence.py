"""Idempotent Supabase persistence for lead inquiries."""

import hashlib
import inspect
from typing import Any, Literal

from supabase import AsyncClient

from app.models.lead import LeadCreateRequest, LeadPersistedResponse

CLASSIFICATION_PENDING: Literal["pending"] = "pending"
IDEMPOTENCY_VERSION = "lead:v1"


class LeadPersistenceError(Exception):
    """Base error for safe lead persistence failures."""


class LeadLookupFailed(LeadPersistenceError):
    """Raised when the existing-lead lookup fails."""


class LeadInsertFailed(LeadPersistenceError):
    """Raised when a lead insert fails."""


async def _resolve(value: Any) -> Any:
    """Support real Supabase builders and lightweight test doubles."""
    if inspect.isawaitable(value):
        return await value
    return value


def normalize_source(source: str) -> str:
    """Normalize source before hashing or saving."""
    return source.strip().lower()


def normalize_message(message: str) -> str:
    """Normalize message text for stable duplicate detection."""
    return " ".join(message.strip().split())


def generate_idempotency_key(source: str, message: str) -> str:
    """Create a deterministic key from normalized lead input."""
    normalized_source = normalize_source(source)
    normalized_message = normalize_message(message)
    digest = hashlib.sha256(
        f"{IDEMPOTENCY_VERSION}\n{normalized_source}\n{normalized_message}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"{IDEMPOTENCY_VERSION}:{digest}"


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
    )


def _to_response(
    row: dict[str, Any],
    persistence_status: Literal["created", "deduplicated"],
) -> LeadPersistedResponse:
    return LeadPersistedResponse(
        id=row["id"],
        source=row["source"],
        classification_status=row["classification_status"],
        persistence_status=persistence_status,
    )


async def _find_existing_lead(
    db: AsyncClient,
    idempotency_key: str,
) -> dict[str, Any] | None:
    try:
        query = await _resolve(db.table("leads"))
        query = await _resolve(query.select("id,source,classification_status"))
        query = await _resolve(query.eq("idempotency_key", idempotency_key))
        query = await _resolve(query.limit(1))
        response = await _resolve(query.execute())
    except Exception as exc:
        raise LeadLookupFailed from exc

    return _first_row(response)


async def persist_lead(
    db: AsyncClient,
    request: LeadCreateRequest,
) -> LeadPersistedResponse:
    """Persist a lead once, returning existing rows for duplicate submissions."""
    source = normalize_source(request.source)
    raw_message = request.message
    idempotency_key = generate_idempotency_key(
        source=source,
        message=raw_message,
    )

    existing_lead = await _find_existing_lead(db, idempotency_key)
    if existing_lead is not None:
        return _to_response(existing_lead, persistence_status="deduplicated")

    payload = {
        "idempotency_key": idempotency_key,
        "source": source,
        "raw_message": raw_message,
        "classification_status": CLASSIFICATION_PENDING,
    }

    try:
        query = await _resolve(db.table("leads"))
        query = await _resolve(query.insert(payload))
        response = await _resolve(query.execute())
    except Exception as exc:
        if _is_unique_constraint_error(exc):
            existing_after_race = await _find_existing_lead(db, idempotency_key)
            if existing_after_race is not None:
                return _to_response(
                    existing_after_race,
                    persistence_status="deduplicated",
                )
        raise LeadInsertFailed from exc

    inserted_lead = _first_row(response)
    if inserted_lead is None:
        raise LeadInsertFailed

    return _to_response(inserted_lead, persistence_status="created")
