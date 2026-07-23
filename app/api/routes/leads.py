"""Lead inquiry contract endpoint."""

import logging
from http import HTTPStatus

from fastapi import APIRouter

from app.models.lead import LeadAcceptedResponse, LeadCreateRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["leads"])


@router.post(
    "/leads",
    response_model=LeadAcceptedResponse,
    status_code=HTTPStatus.ACCEPTED,
    summary="Accept an unstructured lead inquiry",
)
async def create_lead(request: LeadCreateRequest) -> LeadAcceptedResponse:
    """Validate the public lead request contract without persistence yet."""
    logger.info("Accepted lead inquiry contract check from source=%s", request.source)
    return LeadAcceptedResponse(
        source=request.source,
        message=request.message,
    )
