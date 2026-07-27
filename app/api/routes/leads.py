"""Lead inquiry contract endpoint."""

import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from supabase import AsyncClient

from app.db.client import get_db
from app.models.lead import LeadCreateRequest, LeadPersistedResponse
from app.services.lead_persistence import (
    LeadInsertFailed,
    LeadLookupFailed,
    LeadUnexpectedPersistenceFailure,
    persist_lead,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["leads"])


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
