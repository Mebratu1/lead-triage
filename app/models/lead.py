"""Lead domain model."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID


class Lead:
    """Lead domain model."""

    def __init__(
        self,
        id: UUID | str,
        email: str,
        first_name: str,
        last_name: str,
        lead_score: int = 0,
        status: str = "new",
        phone: Optional[str] = None,
        company: Optional[str] = None,
        job_title: Optional[str] = None,
        source: Optional[str] = None,
        is_duplicate: bool = False,
        original_lead_id: Optional[UUID | str] = None,
        classification_rationale: Optional[str] = None,
        received_at: Optional[datetime] = None,
        classified_at: Optional[datetime] = None,
        created_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None,
        tags: Optional[list[str]] = None,
    ):
        """Initialize Lead instance."""
        self.id = str(id)
        self.email = email.lower().strip()
        self.first_name = first_name
        self.last_name = last_name
        self.lead_score = lead_score
        self.status = status
        self.phone = phone
        self.company = company
        self.job_title = job_title
        self.source = source
        self.is_duplicate = is_duplicate
        self.original_lead_id = str(original_lead_id) if original_lead_id else None
        self.classification_rationale = classification_rationale
        self.received_at = received_at or datetime.now(timezone.utc)
        self.classified_at = classified_at
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at or datetime.now(timezone.utc)
        self.tags = tags or []

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "phone": self.phone,
            "company": self.company,
            "job_title": self.job_title,
            "lead_score": self.lead_score,
            "status": self.status,
            "tags": self.tags,
            "is_duplicate": self.is_duplicate,
            "original_lead_id": self.original_lead_id,
            "classification_rationale": self.classification_rationale,
            "received_at": self.received_at,
            "classified_at": self.classified_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Lead":
        """Create instance from dictionary."""
        return cls(**data)
