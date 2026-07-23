"""Health check endpoints."""

from fastapi import APIRouter

from app.config import settings
from app.models.schemas import HealthCheckResponse

router = APIRouter(tags=["health"])


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
