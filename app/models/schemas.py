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


class QueueHealthResponse(BaseModel):
    """Classification queue health response."""

    status: str
    pending_count: int
    backoff_count: int
    exhausted_count: int
    max_attempts: int
