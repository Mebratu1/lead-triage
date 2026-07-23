"""Shared API response models."""

from pydantic import BaseModel


class HealthCheckResponse(BaseModel):
    """Health check response."""

    status: str
    service: str = "lead-triage"
    environment: str
    version: str


class DatabaseHealthResponse(BaseModel):
    """Database health check response."""

    status: str
    database: str
