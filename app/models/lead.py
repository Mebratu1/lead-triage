"""Lead request and temporary response models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LeadCreateRequest(BaseModel):
    """Public contract for an unstructured lead inquiry."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "source": "website",
                    "message": (
                        "I need emergency plumbing service today. "
                        "Please call me at 301-555-0144."
                    ),
                }
            ]
        },
    )

    source: str = Field(default="website", min_length=2, max_length=50)
    message: str = Field(min_length=10, max_length=5000)

    @field_validator("source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        """Normalize source labels for consistent downstream storage."""
        return value.strip().lower()

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        """Reject empty inquiries after whitespace trimming."""
        if not value.strip():
            raise ValueError("message must contain non-whitespace text")
        return value


class LeadAcceptedResponse(BaseModel):
    """Temporary response while classification and persistence are deferred."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "status": "accepted",
                "source": "website",
                "message": "I need emergency plumbing service today.",
                "classification_status": "pending",
            }
        },
    )

    status: Literal["accepted"] = "accepted"
    source: str
    message: str
    classification_status: Literal["pending"] = "pending"
