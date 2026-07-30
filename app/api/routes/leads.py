"""Lead inquiry contract endpoint."""

from datetime import UTC, datetime
import logging
from secrets import compare_digest
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from supabase import AsyncClient

from app.config import settings
from app.db.client import get_db
from app.models.classification import LeadClassificationStatus, LeadUrgency
from app.models.lead import LeadCreateRequest, LeadPersistedResponse
from app.models.schemas import LeadListResponse, LeadPublicResponse
from app.repositories.lead_repository import (
    LeadRepositoryLookupError,
    get_lead_by_id,
    list_leads,
)
from app.services.lead_persistence import (
    LeadInsertFailed,
    LeadLookupFailed,
    LeadUnexpectedPersistenceFailure,
    persist_lead,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["leads"])

AdminToken = Annotated[str | None, Header(alias="X-Admin-Token")]
LeadLimit = Annotated[int, Query(ge=1, le=100)]
LeadOffset = Annotated[int, Query(ge=0)]
SourceFilter = Annotated[str | None, Query(min_length=2, max_length=50)]


def require_admin_access(x_admin_token: AdminToken = None) -> None:
    """Require the configured admin token for read-side lead access."""
    expected_token = settings.queue_metrics_token
    if expected_token is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    if x_admin_token is None or not compare_digest(x_admin_token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )


def _public_lead_from_row(row: dict) -> LeadPublicResponse:
    """Map a persisted lead row to the admin-safe public response contract."""
    updated_at = row.get("classified_at") or row["created_at"]
    return LeadPublicResponse(
        id=row["id"],
        source=row["source"],
        customer_name=row.get("customer_name"),
        customer_email=row.get("email"),
        customer_phone=row.get("phone"),
        message=row["raw_message"],
        classification_status=row["classification_status"],
        urgency=row.get("urgency"),
        summary=row.get("ai_summary"),
        classification_attempt_count=row.get("classification_attempt_count") or 0,
        created_at=row["created_at"],
        updated_at=updated_at,
    )


def _utc_datetime(value: datetime) -> datetime:
    """Normalize route datetime filters before comparing them."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@router.get(
    "/leads",
    response_model=LeadListResponse,
    summary="List persisted leads for admin review",
)
async def read_leads(
    classification_status: LeadClassificationStatus | None = Query(default=None),
    urgency: LeadUrgency | None = Query(default=None),
    source: SourceFilter = None,
    start_date: datetime | None = Query(default=None),
    end_date: datetime | None = Query(default=None),
    limit: LeadLimit = 50,
    offset: LeadOffset = 0,
    _: None = Depends(require_admin_access),
    db: AsyncClient = Depends(get_db),
) -> LeadListResponse:
    """Return a filtered, paginated admin-safe lead list."""
    if (
        start_date is not None
        and end_date is not None
        and _utc_datetime(start_date) > _utc_datetime(end_date)
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="start_date must be before or equal to end_date",
        )

    normalized_source = source.strip().lower() if source is not None else None
    try:
        rows, total = await list_leads(
            db=db,
            limit=limit,
            offset=offset,
            classification_status=(
                classification_status.value if classification_status else None
            ),
            urgency=urgency.value if urgency else None,
            source=normalized_source,
            start_date=start_date,
            end_date=end_date,
        )
    except LeadRepositoryLookupError as exc:
        logger.warning("Lead list database failure")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lead lookup failed",
        ) from exc

    return LeadListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[_public_lead_from_row(row) for row in rows],
    )


@router.get(
    "/leads/{lead_id}",
    response_model=LeadPublicResponse,
    summary="Retrieve one persisted lead for admin review",
)
async def read_lead(
    lead_id: UUID,
    _: None = Depends(require_admin_access),
    db: AsyncClient = Depends(get_db),
) -> LeadPublicResponse:
    """Return one admin-safe lead by UUID."""
    try:
        row = await get_lead_by_id(db=db, lead_id=str(lead_id))
    except LeadRepositoryLookupError as exc:
        logger.warning("Lead detail database failure")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lead lookup failed",
        ) from exc

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found",
        )

    return _public_lead_from_row(row)


@router.post(
    "/leads",
    response_model=LeadPersistedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Persist an unstructured lead inquiry",
)
async def create_lead(
    request: LeadCreateRequest,
    response: Response,
    db: AsyncClient = Depends(get_db),
) -> LeadPersistedResponse:
    """Persist the public lead request contract without classification yet."""
    request_id = str(uuid4())
    try:
        lead = await persist_lead(db=db, request=request)
    except (LeadLookupFailed, LeadInsertFailed) as exc:
        logger.warning(
            "Lead persistence database failure category=%s request_id=%s",
            exc.__class__.__name__,
            request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lead persistence failed",
        ) from exc
    except LeadUnexpectedPersistenceFailure as exc:
        logger.error(
            "Lead persistence unexpected failure category=%s request_id=%s",
            exc.__class__.__name__,
            request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Lead persistence failed",
        ) from exc

    logger.info(
        "Lead %s persisted duplicate=%s request_id=%s",
        lead.id,
        lead.duplicate,
        request_id,
    )
    if lead.duplicate:
        response.status_code = status.HTTP_200_OK
    return lead
