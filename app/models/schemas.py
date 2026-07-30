"""Shared API response models."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.lead import LeadIntegrationStatus


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


class LeadPublicResponse(BaseModel):
    """Admin-safe public view of a persisted lead."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    source: str
    customer_name: str | None
    customer_email: str | None
    customer_phone: str | None
    message: str
    classification_status: Literal["pending", "classified", "failed"]
    urgency: Literal["hot", "warm", "cold"] | None
    summary: str | None
    classification_attempt_count: int
    integration_status: LeadIntegrationStatus
    integration_last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LeadListResponse(BaseModel):
    """Paginated admin lead list response."""

    model_config = ConfigDict(extra="forbid")

    total: int
    limit: int
    offset: int
    items: list[LeadPublicResponse]


class LeadSyncResponse(BaseModel):
    """Admin-safe response for one outbound lead sync attempt."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    integration_status: LeadIntegrationStatus
    integration_last_synced_at: datetime | None
    retry_after_seconds: int | None = None
    detail: str
