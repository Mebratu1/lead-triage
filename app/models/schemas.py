"""Pydantic models for request/response validation."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class LeadIngest(BaseModel):
    """Lead ingestion request model."""

    email: EmailStr
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    company: Optional[str] = Field(None, max_length=255)
    job_title: Optional[str] = Field(None, max_length=255)
    source: Optional[str] = Field(None, max_length=100)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        """Normalize email to lowercase."""
        return v.lower().strip()

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        """Remove non-digit characters from phone."""
        if v:
            return "".join(c for c in v if c.isdigit())
        return None


class LeadResponse(BaseModel):
    """Lead response model."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    first_name: str
    last_name: str
    phone: Optional[str]
    company: Optional[str]
    job_title: Optional[str]
    lead_score: int
    status: str
    tags: list[str] = Field(default_factory=list)
    is_duplicate: bool
    original_lead_id: Optional[str] = None
    received_at: datetime
    classified_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class LeadClassification(BaseModel):
    """Lead classification result model."""

    lead_id: str
    lead_score: int = Field(..., ge=0, le=100)
    status: str = Field(..., pattern="^(new|qualified|disqualified|processing_error)$")
    tags: list[str] = Field(default_factory=list)
    rationale: str = Field(..., min_length=10)


class DuplicateCheckResult(BaseModel):
    """Duplicate check result."""

    is_duplicate: bool
    original_lead_id: Optional[str] = None
    match_type: Optional[str] = None
    similarity_score: Optional[float] = None


class HealthCheckResponse(BaseModel):
    """Health check response."""

    status: str
    service: str = "lead-triage"
    environment: str
    version: str
