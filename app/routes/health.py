"""Health check endpoints."""

from secrets import compare_digest
import inspect
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from supabase import AsyncClient

from app.config import settings
from app.db.client import get_db
from app.models.schemas import (
    DatabaseHealthResponse,
    HealthCheckResponse,
    QueueHealthResponse,
)
from app.services.lead_classification_worker import DEFAULT_MAX_CLASSIFICATION_ATTEMPTS
from app.services.lead_persistence import LeadLookupFailed, get_classification_queue_metrics

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


async def _resolve(value):
    """Support real Supabase builders and lightweight test doubles."""
    if inspect.isawaitable(value):
        return await value
    return value


def _authorize_queue_health(authorization: str | None) -> None:
    """Protect queue metrics when a monitoring token is configured."""
    expected_token = settings.queue_metrics_token
    if expected_token is None:
        return

    expected_header = f"Bearer {expected_token}"
    if authorization is None or not compare_digest(authorization, expected_header):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Queue health authorization required",
        )


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


@router.get("/health/queue", response_model=QueueHealthResponse)
async def queue_health_check(
    db: AsyncClient = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> QueueHealthResponse:
    """Return aggregate classification queue counters for monitoring."""
    _authorize_queue_health(authorization)

    try:
        metrics = await get_classification_queue_metrics(
            db=db,
            max_attempts=DEFAULT_MAX_CLASSIFICATION_ATTEMPTS,
        )
    except LeadLookupFailed as exc:
        logger.warning("Queue health check failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Queue health check failed",
        ) from exc

    return QueueHealthResponse(
        status="ok",
        pending_count=metrics.pending_count,
        backoff_count=metrics.backoff_count,
        exhausted_count=metrics.exhausted_count,
        max_attempts=metrics.max_attempts,
    )
