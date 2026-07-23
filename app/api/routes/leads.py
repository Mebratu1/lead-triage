"""Lead inquiry contract endpoint."""

import logging
from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException, status
from supabase import AsyncClient

from app.db.client import get_db
from app.models.lead import LeadCreateRequest, LeadPersistedResponse
from app.services.lead_persistence import LeadPersistenceError, persist_lead

logger = logging.getLogger(__name__)

router = APIRouter(tags=["leads"])


@router.post(
    "/leads",
    response_model=LeadPersistedResponse,
    status_code=HTTPStatus.ACCEPTED,
    summary="Persist an unstructured lead inquiry",
)
async def create_lead(
    request: LeadCreateRequest,
    db: AsyncClient = Depends(get_db),
) -> LeadPersistedResponse:
    """Persist the public lead request contract without classification yet."""
    try:
        response = await persist_lead(db=db, request=request)
    except LeadPersistenceError as exc:
        logger.warning("Lead persistence failed: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lead persistence failed",
        ) from exc

    logger.info(
        "Lead %s with persistence_status=%s",
        response.id,
        response.persistence_status,
    )
    return response
