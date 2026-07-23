"""Lead ingestion and retrieval endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from supabase import AsyncClient

from app.config import settings
from app.db.client import get_db
from app.models.schemas import LeadIngest, LeadResponse
from app.services.classifier import ClassificationService
from app.services.dedup import DeduplicationService
from app.services.lead_service import LeadService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leads", tags=["leads"])


async def get_lead_service(db: AsyncClient = Depends(get_db)) -> LeadService:
    """Dependency injection for lead service."""
    try:
        classifier = ClassificationService(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
        )
        dedup_service = DeduplicationService(
            db=db,
            dedup_window_days=settings.dedup_window_days,
        )
        return LeadService(db=db, classifier=classifier, dedup_service=dedup_service)
    except Exception as e:
        logger.error(f"Failed to initialize lead service: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Service initialization failed",
        )


@router.post("/ingest", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
async def ingest_lead(
    lead_data: LeadIngest,
    lead_service: LeadService = Depends(get_lead_service),
) -> LeadResponse:
    """
    Ingest a new lead.

    **Process:**
    1. Validate request with Pydantic
    2. Check for duplicates (email)
    3. If duplicate: mark as duplicate, link to original
    4. If new: classify using OpenAI GPT-4
    5. Store in Supabase PostgreSQL
    6. Return classified lead

    **Returns:**
    - 201 Created: Lead successfully ingested and classified
    - 400 Bad Request: Invalid input
    - 500 Internal Server Error: Database or LLM error

    **Example:**
    ```json
    {
        "email": "john@example.com",
        "first_name": "John",
        "last_name": "Doe",
        "phone": "+1-555-123-4567",
        "company": "Acme Corp",
        "job_title": "CTO"
    }
    ```
    """
    try:
        logger.info(f"Ingesting lead: {lead_data.email}")
        lead = await lead_service.ingest_lead(lead_data, source="api")

        return LeadResponse(
            id=lead.id,
            email=lead.email,
            first_name=lead.first_name,
            last_name=lead.last_name,
            phone=lead.phone,
            company=lead.company,
            job_title=lead.job_title,
            lead_score=lead.lead_score,
            status=lead.status,
            tags=lead.tags,
            is_duplicate=lead.is_duplicate,
            original_lead_id=lead.original_lead_id,
            received_at=lead.received_at,
            classified_at=lead.classified_at,
            created_at=lead.created_at,
            updated_at=lead.updated_at,
        )

    except ValueError as e:
        logger.error(f"Validation error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to ingest lead: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process lead. Please try again later.",
        )


@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: str,
    lead_service: LeadService = Depends(get_lead_service),
) -> LeadResponse:
    """
    Retrieve a lead by ID.

    **Args:**
    - lead_id: UUID of the lead

    **Returns:**
    - 200 OK: Lead details
    - 404 Not Found: Lead doesn't exist
    - 500 Internal Server Error: Database error

    **Example response:**
    ```json
    {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "email": "john@example.com",
        "first_name": "John",
        "last_name": "Doe",
        "lead_score": 85,
        "status": "qualified",
        "tags": ["sales_ready", "high_priority"],
        ...
    }
    ```
    """
    try:
        logger.info(f"Retrieving lead: {lead_id}")
        lead = await lead_service.get_lead(lead_id)

        if not lead:
            logger.warning(f"Lead not found: {lead_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Lead {lead_id} not found",
            )

        return LeadResponse(
            id=lead.id,
            email=lead.email,
            first_name=lead.first_name,
            last_name=lead.last_name,
            phone=lead.phone,
            company=lead.company,
            job_title=lead.job_title,
            lead_score=lead.lead_score,
            status=lead.status,
            tags=lead.tags,
            is_duplicate=lead.is_duplicate,
            original_lead_id=lead.original_lead_id,
            received_at=lead.received_at,
            classified_at=lead.classified_at,
            created_at=lead.created_at,
            updated_at=lead.updated_at,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to retrieve lead {lead_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve lead",
        )

