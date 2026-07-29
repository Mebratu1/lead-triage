"""Lead classification contracts separate from intake models."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LeadClassificationStatus(StrEnum):
    """Lifecycle status for AI lead classification output."""

    PENDING = "pending"
    CLASSIFIED = "classified"
    FAILED = "failed"


class LeadClassificationPersistenceStatus(StrEnum):
    """Persistence outcome for one attempted classification work item."""

    SAVED = "saved"
    SKIPPED = "skipped"
    ERROR = "error"


class LeadUrgency(StrEnum):
    """Supported urgency labels produced by classification."""

    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class LeadClassificationPayload(BaseModel):
    """Strict JSON payload expected from the model response."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    customer_name: str | None
    email: str | None
    phone: str | None
    requested_service: str | None
    urgency: LeadUrgency | None
    lead_score: int | None = Field(ge=0, le=100)
    ai_summary: str | None

    @field_validator(
        "customer_name",
        "email",
        "phone",
        "requested_service",
        "ai_summary",
        mode="before",
    )
    @classmethod
    def blank_strings_to_none(cls, value: Any) -> Any:
        """Treat blank model strings as missing fields."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


class LeadClassified(BaseModel):
    """Validated output from the isolated lead classification domain."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "customer_name": "Jane Doe",
                "email": "jane@example.com",
                "phone": "301-555-0144",
                "requested_service": "emergency plumbing",
                "urgency": "hot",
                "lead_score": 92,
                "ai_summary": "Customer needs emergency plumbing service today.",
                "classification_status": "classified",
                "error_reason": None,
            }
        },
    )

    customer_name: str | None = None
    email: str | None = None
    phone: str | None = None
    requested_service: str | None = None
    urgency: LeadUrgency | None = None
    lead_score: int | None = Field(default=None, ge=0, le=100)
    ai_summary: str | None = None
    classification_status: LeadClassificationStatus = LeadClassificationStatus.CLASSIFIED
    error_reason: str | None = None

    @field_validator(
        "customer_name",
        "email",
        "phone",
        "requested_service",
        "ai_summary",
        mode="before",
    )
    @classmethod
    def blank_strings_to_none(cls, value: Any) -> Any:
        """Treat blank model strings as missing fields."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @classmethod
    def from_payload(cls, payload: LeadClassificationPayload) -> "LeadClassified":
        """Build a classified result from validated model payload only."""
        return cls(
            customer_name=payload.customer_name,
            email=payload.email,
            phone=payload.phone,
            requested_service=payload.requested_service,
            urgency=payload.urgency,
            lead_score=payload.lead_score,
            ai_summary=payload.ai_summary,
            classification_status=LeadClassificationStatus.CLASSIFIED,
            error_reason=None,
        )

    @classmethod
    def failed(cls, reason: str) -> "LeadClassified":
        """Build a safe failed classification without fake extracted values."""
        return cls(
            customer_name=None,
            email=None,
            phone=None,
            requested_service=None,
            urgency=None,
            lead_score=None,
            ai_summary=None,
            classification_status=LeadClassificationStatus.FAILED,
            error_reason=reason,
        )


class LeadClassificationWorkItemResult(BaseModel):
    """Result for one pending lead attempted by classification orchestration."""

    model_config = ConfigDict(extra="forbid")

    lead_id: str
    classification_status: LeadClassificationStatus | None = None
    persistence_status: LeadClassificationPersistenceStatus
    error_reason: str | None = None


class LeadClassificationBatchResult(BaseModel):
    """Summary for one bounded classification batch."""

    model_config = ConfigDict(extra="forbid")

    fetched: int
    saved: int
    classified: int
    failed: int
    skipped: int
    errors: int
    results: list[LeadClassificationWorkItemResult]
