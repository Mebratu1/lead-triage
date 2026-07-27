"""Health check endpoints."""

import inspect
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from supabase import AsyncClient

from app.config import settings
from app.db.client import get_db
from app.models.schemas import DatabaseHealthResponse, HealthCheckResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


async def _resolve(value):
    """Support real Supabase builders and lightweight test doubles."""
    if inspect.isawaitable(value):
        return await value
    return value


@router.get("/health", response_model=HealthCheckResponse)
async def health_check() -> HealthCheckResponse:
    """Health check endpoint."""
    return HealthCheckResponse(
        status="ok",
        environment=settings.environment,
        version=settings.api_version,
    )


@router.get("/", response_model=HealthCheckResponse)
async def root() -> HealthCheckResponse:
    """Root endpoint."""
    return HealthCheckResponse(
        status="ok",
        environment=settings.environment,
        version=settings.api_version,
    )


@router.get("/health/database", response_model=DatabaseHealthResponse)
async def database_health_check(
    db: AsyncClient = Depends(get_db),
) -> DatabaseHealthResponse:
    """Check that the configured Supabase client can query the leads table."""
    try:
        query = await _resolve(db.table("leads"))
        query = await _resolve(
            query.select(
                "id,idempotency_key,deduplication_bucket,source,"
                "raw_message,classification_status,created_at"
            )
        )
        query = await _resolve(query.limit(1))
        await _resolve(query.execute())
    except Exception as exc:
        logger.warning("Database health check failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database health check failed",
        ) from exc

    return DatabaseHealthResponse(status="ok", database="connected")
