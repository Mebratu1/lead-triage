"""Models for outbound CRM retry processing."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from app.models.lead import LeadIntegrationStatus

CrmRetryState = Literal["scheduled", "manual", "exhausted"]


class CrmSyncOutcome(StrEnum):
    """Per-lead outcome from one CRM retry attempt."""

    SYNCED = "synced"
    RETRY_SCHEDULED = "retry_scheduled"
    PERMANENT_FAILED = "permanent_failed"
    EXHAUSTED = "exhausted"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass(frozen=True)
class CrmSyncWorkItemResult:
    """Sanitized outcome for one claimed CRM retry."""

    lead_id: str
    outcome: CrmSyncOutcome
    error_reason: str | None = None
    retry_after_seconds: int | None = None


@dataclass(frozen=True)
class CrmSyncBatchResult:
    """Aggregate result for one bounded CRM retry batch."""

    fetched: int
    synced: int
    retry_scheduled: int
    permanent_failed: int
    exhausted: int
    skipped: int
    errors: int
    results: list[CrmSyncWorkItemResult]


def crm_retry_state(
    *,
    integration_status: LeadIntegrationStatus | str,
    next_attempt_at: object | None,
    error_reason: str | None,
) -> CrmRetryState | None:
    """Derive a safe retry state without exposing internal error details."""
    if integration_status != LeadIntegrationStatus.FAILED:
        return None
    if next_attempt_at is not None:
        return "scheduled"
    if error_reason == "crm_retry_exhausted":
        return "exhausted"
    return "manual"
