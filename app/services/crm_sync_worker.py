"""Concurrent-safe processing for due outbound CRM retries."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from supabase import AsyncClient

from app.models.integration import (
    CrmSyncBatchResult,
    CrmSyncOutcome,
    CrmSyncWorkItemResult,
)
from app.models.lead import LeadIntegrationStatus
from app.models.schemas import LeadPublicResponse
from app.repositories.lead_repository import (
    LeadRepositoryLookupError,
    LeadRepositoryUpdateConflict,
    LeadRepositoryUpdateError,
    claim_due_leads_for_crm_sync,
    update_lead_integration_status,
)
from app.services.crm_sync import (
    CrmConfigurationError,
    CrmDeliveryPermanentError,
    CrmDeliveryRetryableError,
    LeadCrmDispatcher,
)

logger = logging.getLogger(__name__)

DEFAULT_CRM_RETRY_BATCH_SIZE = 10
MAX_CRM_RETRY_BATCH_SIZE = 100
DEFAULT_CRM_RETRY_BASE_SECONDS = 60
DEFAULT_CRM_RETRY_MAX_SECONDS = 3600
DEFAULT_CRM_RETRY_MAX_ATTEMPTS = 5
DEFAULT_CRM_CLAIM_TIMEOUT_SECONDS = 300


class CrmSyncBatchFetchFailed(RuntimeError):
    """Raised when due CRM retries cannot be claimed."""


def exponential_retry_delay(
    *,
    retry_attempt_count: int,
    base_seconds: int,
    max_seconds: int,
) -> int:
    """Return a capped exponential delay after a failed retry attempt."""
    if retry_attempt_count < 1:
        raise ValueError("retry_attempt_count must be at least 1")
    if base_seconds < 1:
        raise ValueError("base_seconds must be at least 1")
    if max_seconds < base_seconds:
        raise ValueError("max_seconds must be at least base_seconds")
    delay = base_seconds
    for _attempt in range(retry_attempt_count):
        if delay >= max_seconds:
            return max_seconds
        delay = min(delay * 2, max_seconds)
    return delay


def _worker_id(worker_id: str | None) -> str:
    if worker_id and worker_id.strip():
        return worker_id.strip()
    return f"crm-sync-{uuid4()}"


def _public_lead(row: dict[str, Any]) -> LeadPublicResponse:
    updated_at = row.get("classified_at") or row["created_at"]
    return LeadPublicResponse(
        id=row["id"],
        source=row["source"],
        customer_name=row.get("customer_name"),
        customer_email=row.get("email"),
        customer_phone=row.get("phone"),
        message=row["raw_message"],
        classification_status=row["classification_status"],
        urgency=row.get("urgency"),
        summary=row.get("ai_summary"),
        classification_attempt_count=row.get("classification_attempt_count") or 0,
        integration_status=(
            row.get("integration_status") or LeadIntegrationStatus.FAILED
        ),
        integration_last_synced_at=row.get("integration_last_synced_at"),
        created_at=row["created_at"],
        updated_at=updated_at,
    )


def _summarize(
    fetched: int,
    results: list[CrmSyncWorkItemResult],
) -> CrmSyncBatchResult:
    return CrmSyncBatchResult(
        fetched=fetched,
        synced=sum(item.outcome == CrmSyncOutcome.SYNCED for item in results),
        retry_scheduled=sum(
            item.outcome == CrmSyncOutcome.RETRY_SCHEDULED for item in results
        ),
        permanent_failed=sum(
            item.outcome == CrmSyncOutcome.PERMANENT_FAILED for item in results
        ),
        exhausted=sum(item.outcome == CrmSyncOutcome.EXHAUSTED for item in results),
        skipped=sum(item.outcome == CrmSyncOutcome.SKIPPED for item in results),
        errors=sum(item.outcome == CrmSyncOutcome.ERROR for item in results),
        results=results,
    )


async def _store_outcome(
    *,
    db: AsyncClient,
    lead_id: str,
    worker_id: str,
    integration_status: LeadIntegrationStatus,
    error_reason: str | None = None,
    retry_after_seconds: int | None = None,
    completed_at: datetime,
) -> None:
    await update_lead_integration_status(
        db=db,
        lead_id=lead_id,
        integration_status=integration_status,
        error_reason=error_reason,
        retry_after_seconds=retry_after_seconds,
        synced_at=(
            completed_at
            if integration_status == LeadIntegrationStatus.SYNCED
            else None
        ),
        now=completed_at,
        claim_owner_id=worker_id,
    )


async def process_due_crm_sync_batch(
    *,
    db: AsyncClient,
    dispatcher: LeadCrmDispatcher,
    limit: int = DEFAULT_CRM_RETRY_BATCH_SIZE,
    worker_id: str | None = None,
    claim_timeout_seconds: int = DEFAULT_CRM_CLAIM_TIMEOUT_SECONDS,
    retry_base_seconds: int = DEFAULT_CRM_RETRY_BASE_SECONDS,
    retry_max_seconds: int = DEFAULT_CRM_RETRY_MAX_SECONDS,
    max_attempts: int = DEFAULT_CRM_RETRY_MAX_ATTEMPTS,
    completed_at: datetime | None = None,
) -> CrmSyncBatchResult:
    """Claim and process one bounded batch of due CRM webhook retries."""
    if limit < 1 or limit > MAX_CRM_RETRY_BATCH_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_CRM_RETRY_BATCH_SIZE}")
    if retry_base_seconds < 1:
        raise ValueError("retry_base_seconds must be at least 1")
    if retry_max_seconds < retry_base_seconds:
        raise ValueError("retry_max_seconds must be at least retry_base_seconds")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    active_worker_id = _worker_id(worker_id)
    finished_at = completed_at or datetime.now(UTC)
    try:
        claimed_leads = await claim_due_leads_for_crm_sync(
            db=db,
            limit=limit,
            worker_id=active_worker_id,
            claim_timeout_seconds=claim_timeout_seconds,
            max_attempts=max_attempts,
        )
    except LeadRepositoryLookupError as exc:
        raise CrmSyncBatchFetchFailed from exc

    results: list[CrmSyncWorkItemResult] = []
    for row in claimed_leads:
        lead_id = str(row.get("id", "unknown"))
        try:
            retry_attempt_count = int(
                row.get("integration_retry_attempt_count") or 0
            )
            if retry_attempt_count < 1:
                raise ValueError("claimed retry attempt count must be positive")
            lead = _public_lead(row)
        except (KeyError, TypeError, ValueError):
            try:
                await _store_outcome(
                    db=db,
                    lead_id=lead_id,
                    worker_id=active_worker_id,
                    integration_status=LeadIntegrationStatus.FAILED,
                    error_reason="crm_invalid_payload",
                    completed_at=finished_at,
                )
            except (LeadRepositoryUpdateConflict, LeadRepositoryUpdateError):
                results.append(
                    CrmSyncWorkItemResult(
                        lead_id=lead_id,
                        outcome=CrmSyncOutcome.ERROR,
                        error_reason="crm_update_failed",
                    )
                )
                continue
            results.append(
                CrmSyncWorkItemResult(
                    lead_id=lead_id,
                    outcome=CrmSyncOutcome.PERMANENT_FAILED,
                    error_reason="crm_invalid_payload",
                )
            )
            continue

        try:
            await dispatcher.sync_lead(lead)
        except CrmDeliveryRetryableError:
            exhausted = retry_attempt_count >= max_attempts
            retry_after_seconds = None
            error_reason = "crm_retry_exhausted"
            outcome = CrmSyncOutcome.EXHAUSTED
            if not exhausted:
                retry_after_seconds = exponential_retry_delay(
                    retry_attempt_count=retry_attempt_count,
                    base_seconds=retry_base_seconds,
                    max_seconds=retry_max_seconds,
                )
                error_reason = "crm_retryable_failure"
                outcome = CrmSyncOutcome.RETRY_SCHEDULED
            try:
                await _store_outcome(
                    db=db,
                    lead_id=lead_id,
                    worker_id=active_worker_id,
                    integration_status=LeadIntegrationStatus.FAILED,
                    error_reason=error_reason,
                    retry_after_seconds=retry_after_seconds,
                    completed_at=finished_at,
                )
            except (LeadRepositoryUpdateConflict, LeadRepositoryUpdateError):
                results.append(
                    CrmSyncWorkItemResult(
                        lead_id=lead_id,
                        outcome=CrmSyncOutcome.ERROR,
                        error_reason="crm_update_failed",
                    )
                )
                continue
            results.append(
                CrmSyncWorkItemResult(
                    lead_id=lead_id,
                    outcome=outcome,
                    error_reason=error_reason,
                    retry_after_seconds=retry_after_seconds,
                )
            )
            continue
        except CrmDeliveryPermanentError:
            delivery_failure_reason = "crm_permanent_failure"
        except CrmConfigurationError:
            delivery_failure_reason = "crm_not_configured"
        except Exception:
            delivery_failure_reason = "crm_dispatch_failed"
        else:
            delivery_failure_reason = None

        if delivery_failure_reason is not None:
            try:
                await _store_outcome(
                    db=db,
                    lead_id=lead_id,
                    worker_id=active_worker_id,
                    integration_status=LeadIntegrationStatus.FAILED,
                    error_reason=delivery_failure_reason,
                    completed_at=finished_at,
                )
            except (LeadRepositoryUpdateConflict, LeadRepositoryUpdateError):
                results.append(
                    CrmSyncWorkItemResult(
                        lead_id=lead_id,
                        outcome=CrmSyncOutcome.ERROR,
                        error_reason="crm_update_failed",
                    )
                )
            else:
                results.append(
                    CrmSyncWorkItemResult(
                        lead_id=lead_id,
                        outcome=CrmSyncOutcome.PERMANENT_FAILED,
                        error_reason=delivery_failure_reason,
                    )
                )
            continue

        try:
            await _store_outcome(
                db=db,
                lead_id=lead_id,
                worker_id=active_worker_id,
                integration_status=LeadIntegrationStatus.SYNCED,
                completed_at=finished_at,
            )
        except LeadRepositoryUpdateConflict:
            results.append(
                CrmSyncWorkItemResult(
                    lead_id=lead_id,
                    outcome=CrmSyncOutcome.SKIPPED,
                    error_reason="crm_claim_lost",
                )
            )
        except LeadRepositoryUpdateError:
            results.append(
                CrmSyncWorkItemResult(
                    lead_id=lead_id,
                    outcome=CrmSyncOutcome.ERROR,
                    error_reason="crm_update_failed",
                )
            )
        else:
            results.append(
                CrmSyncWorkItemResult(
                    lead_id=lead_id,
                    outcome=CrmSyncOutcome.SYNCED,
                )
            )

    return _summarize(fetched=len(claimed_leads), results=results)
