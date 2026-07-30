"""Lead inquiry contract endpoint."""

import csv
from datetime import UTC, datetime
from io import StringIO
import logging
from secrets import compare_digest
from typing import Annotated, Iterator
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse
from supabase import AsyncClient

from app.config import settings
from app.db.client import get_db
from app.models.classification import LeadClassificationStatus, LeadUrgency
from app.models.integration import crm_retry_state
from app.models.lead import (
    LeadCreateRequest,
    LeadIntegrationStatus,
    LeadPersistedResponse,
)
from app.models.schemas import LeadListResponse, LeadPublicResponse, LeadSyncResponse
from app.repositories.lead_repository import (
    LeadRepositoryLookupError,
    LeadRepositoryUpdateConflict,
    LeadRepositoryUpdateError,
    get_lead_by_id,
    list_leads,
    update_lead_integration_status,
)
from app.services.crm_sync import (
    CrmConfigurationError,
    CrmDeliveryPermanentError,
    CrmDeliveryRetryableError,
    dispatch_lead_to_crm,
)
from app.services.lead_persistence import (
    LeadInsertFailed,
    LeadLookupFailed,
    LeadUnexpectedPersistenceFailure,
    persist_lead,
)
from app.services.rate_limiter import (
    LeadIntakeRateLimiter,
    TrustedProxyClientIpResolver,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["leads"])

AdminToken = Annotated[str | None, Header(alias="X-Admin-Token")]
LeadLimit = Annotated[int, Query(ge=1, le=100)]
LeadOffset = Annotated[int, Query(ge=0)]
CsvExportLimit = Annotated[int, Query(ge=1, le=1000)]
SourceFilter = Annotated[str | None, Query(min_length=2, max_length=50)]
StatusAliasFilter = Annotated[
    LeadClassificationStatus | None,
    Query(alias="status"),
]

CSV_EXPORT_HEADERS = [
    "ID",
    "Source",
    "Customer Name",
    "Customer Email",
    "Customer Phone",
    "Status",
    "Urgency",
    "Summary",
    "Created At",
]
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")


def require_admin_access(x_admin_token: AdminToken = None) -> None:
    """Require the configured admin token for read-side lead access."""
    expected_token = settings.admin_token
    if expected_token is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    if x_admin_token is None or not compare_digest(x_admin_token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )


async def enforce_lead_intake_rate_limit(request: Request) -> None:
    """Reject abusive public intake traffic before database access."""
    limiter: LeadIntakeRateLimiter = request.app.state.lead_intake_rate_limiter
    resolver: TrustedProxyClientIpResolver = (
        request.app.state.trusted_proxy_client_ip_resolver
    )
    client_key = resolver.resolve(
        peer_host=request.client.host if request.client is not None else None,
        forwarded_for=request.headers.get("X-Forwarded-For"),
    )
    decision = await limiter.check(client_key)
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Lead intake rate limit exceeded",
            headers={"Retry-After": str(decision.retry_after_seconds or 1)},
        )


def _public_lead_from_row(row: dict) -> LeadPublicResponse:
    """Map a persisted lead row to the admin-safe public response contract."""
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
            row.get("integration_status") or LeadIntegrationStatus.PENDING
        ),
        integration_last_synced_at=row.get("integration_last_synced_at"),
        integration_retry_state=crm_retry_state(
            integration_status=(
                row.get("integration_status") or LeadIntegrationStatus.PENDING
            ),
            next_attempt_at=row.get("integration_next_attempt_at"),
            error_reason=row.get("integration_error"),
        ),
        integration_next_attempt_at=row.get("integration_next_attempt_at"),
        integration_retry_attempt_count=(
            row.get("integration_retry_attempt_count") or 0
        ),
        created_at=row["created_at"],
        updated_at=updated_at,
    )


def _sync_response_from_row(
    row: dict,
    detail: str,
    retry_after_seconds: int | None = None,
) -> LeadSyncResponse:
    """Map sync tracking fields to the admin-safe response contract."""
    return LeadSyncResponse(
        id=row["id"],
        integration_status=row["integration_status"],
        integration_last_synced_at=row.get("integration_last_synced_at"),
        integration_retry_state=crm_retry_state(
            integration_status=row["integration_status"],
            next_attempt_at=row.get("integration_next_attempt_at"),
            error_reason=row.get("integration_error"),
        ),
        integration_next_attempt_at=row.get("integration_next_attempt_at"),
        integration_retry_attempt_count=(
            row.get("integration_retry_attempt_count") or 0
        ),
        retry_after_seconds=retry_after_seconds,
        detail=detail,
    )


async def _record_sync_failure(
    *,
    db: AsyncClient,
    lead_id: UUID,
    error_reason: str,
    retry_after_seconds: int | None,
) -> dict:
    """Store sanitized integration failure state or return a safe service error."""
    try:
        return await update_lead_integration_status(
            db=db,
            lead_id=str(lead_id),
            integration_status=LeadIntegrationStatus.FAILED,
            error_reason=error_reason,
            retry_after_seconds=retry_after_seconds,
            now=datetime.now(UTC),
            reset_retry_attempts=True,
        )
    except (LeadRepositoryUpdateConflict, LeadRepositoryUpdateError) as exc:
        logger.warning("Lead sync failure tracking update failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lead sync failed",
        ) from exc


def _utc_datetime(value: datetime) -> datetime:
    """Normalize route datetime filters before comparing them."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _validate_date_range(
    start_date: datetime | None,
    end_date: datetime | None,
) -> None:
    """Reject reversed date ranges consistently across lead read endpoints."""
    if (
        start_date is not None
        and end_date is not None
        and _utc_datetime(start_date) > _utc_datetime(end_date)
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="start_date must be before or equal to end_date",
        )


def _classification_status_filter(
    classification_status: LeadClassificationStatus | None,
    status_filter: LeadClassificationStatus | None,
) -> LeadClassificationStatus | None:
    """Resolve the canonical and shorthand status filters safely."""
    if (
        classification_status is not None
        and status_filter is not None
        and classification_status != status_filter
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="classification_status and status filters must match",
        )

    return classification_status or status_filter


def _csv_datetime(value: datetime) -> str:
    """Render timestamps consistently in exported CSV data."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _safe_csv_cell(value: object) -> str:
    """Render one safe CSV cell for spreadsheet-oriented exports."""
    if value is None:
        return ""

    text = str(value)
    stripped = text.lstrip()
    if stripped.startswith(CSV_FORMULA_PREFIXES):
        return f"'{text}"
    return text


def _lead_to_csv_record(row: dict) -> dict[str, str]:
    """Map one public lead row to safe CSV columns."""
    lead = _public_lead_from_row(row)
    return {
        "ID": _safe_csv_cell(lead.id),
        "Source": _safe_csv_cell(lead.source),
        "Customer Name": _safe_csv_cell(lead.customer_name),
        "Customer Email": _safe_csv_cell(lead.customer_email),
        "Customer Phone": _safe_csv_cell(lead.customer_phone),
        "Status": _safe_csv_cell(lead.classification_status),
        "Urgency": _safe_csv_cell(lead.urgency),
        "Summary": _safe_csv_cell(lead.summary),
        "Created At": _safe_csv_cell(_csv_datetime(lead.created_at)),
    }


def _csv_line(record: dict[str, str]) -> str:
    """Serialize one CSV record to a single response chunk."""
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_EXPORT_HEADERS)
    writer.writerow(record)
    return output.getvalue()


def _lead_csv_stream(rows: list[dict]) -> Iterator[str]:
    """Stream safe CSV export rows without exposing internal fields."""
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_EXPORT_HEADERS)
    writer.writeheader()
    yield output.getvalue()

    for row in rows:
        yield _csv_line(_lead_to_csv_record(row))


@router.get(
    "/leads",
    response_model=LeadListResponse,
    summary="List persisted leads for admin review",
)
async def read_leads(
    classification_status: LeadClassificationStatus | None = Query(default=None),
    status_filter: StatusAliasFilter = None,
    urgency: LeadUrgency | None = Query(default=None),
    source: SourceFilter = None,
    start_date: datetime | None = Query(default=None),
    end_date: datetime | None = Query(default=None),
    limit: LeadLimit = 50,
    offset: LeadOffset = 0,
    _: None = Depends(require_admin_access),
    db: AsyncClient = Depends(get_db),
) -> LeadListResponse:
    """Return a filtered, paginated admin-safe lead list."""
    _validate_date_range(start_date=start_date, end_date=end_date)
    effective_classification_status = _classification_status_filter(
        classification_status=classification_status,
        status_filter=status_filter,
    )

    normalized_source = source.strip().lower() if source is not None else None
    try:
        rows, total = await list_leads(
            db=db,
            limit=limit,
            offset=offset,
            classification_status=(
                effective_classification_status.value
                if effective_classification_status
                else None
            ),
            urgency=urgency.value if urgency else None,
            source=normalized_source,
            start_date=start_date,
            end_date=end_date,
        )
    except LeadRepositoryLookupError as exc:
        logger.warning("Lead list database failure")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lead lookup failed",
        ) from exc

    return LeadListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[_public_lead_from_row(row) for row in rows],
    )


@router.get(
    "/leads/export/csv",
    summary="Export persisted leads as admin-safe CSV",
)
async def export_leads_csv(
    classification_status: LeadClassificationStatus | None = Query(default=None),
    status_filter: StatusAliasFilter = None,
    urgency: LeadUrgency | None = Query(default=None),
    source: SourceFilter = None,
    start_date: datetime | None = Query(default=None),
    end_date: datetime | None = Query(default=None),
    limit: CsvExportLimit = 1000,
    offset: LeadOffset = 0,
    _: None = Depends(require_admin_access),
    db: AsyncClient = Depends(get_db),
) -> StreamingResponse:
    """Return a filtered CSV export with only admin-safe lead fields."""
    _validate_date_range(start_date=start_date, end_date=end_date)
    effective_classification_status = _classification_status_filter(
        classification_status=classification_status,
        status_filter=status_filter,
    )

    normalized_source = source.strip().lower() if source is not None else None
    try:
        rows, _total = await list_leads(
            db=db,
            limit=limit,
            offset=offset,
            classification_status=(
                effective_classification_status.value
                if effective_classification_status
                else None
            ),
            urgency=urgency.value if urgency else None,
            source=normalized_source,
            start_date=start_date,
            end_date=end_date,
        )
    except LeadRepositoryLookupError as exc:
        logger.warning("Lead CSV export database failure")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lead export failed",
        ) from exc

    return StreamingResponse(
        _lead_csv_stream(rows),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                'attachment; filename="classified_leads_export.csv"'
            )
        },
    )


@router.post(
    "/leads/{lead_id}/sync",
    response_model=LeadSyncResponse,
    summary="Track outbound CRM synchronization for one lead",
)
async def sync_lead_to_crm(
    lead_id: UUID,
    response: Response,
    _: None = Depends(require_admin_access),
    db: AsyncClient = Depends(get_db),
) -> LeadSyncResponse:
    """Dispatch one classified lead through the CRM boundary and track the result."""
    try:
        row = await get_lead_by_id(db=db, lead_id=str(lead_id))
    except LeadRepositoryLookupError as exc:
        logger.warning("Lead sync lookup database failure")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lead sync failed",
        ) from exc

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found",
        )

    lead = _public_lead_from_row(row)
    if lead.classification_status != LeadClassificationStatus.CLASSIFIED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Lead must be classified before CRM sync",
        )

    try:
        await dispatch_lead_to_crm(lead)
    except CrmConfigurationError as exc:
        logger.warning(
            "Lead CRM sync rejected because integration is not configured lead_id=%s",
            lead_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CRM integration is not configured",
        ) from exc
    except CrmDeliveryRetryableError as exc:
        retry_after_seconds = settings.crm_retry_base_seconds
        logger.warning(
            "Lead CRM sync received retryable failure lead_id=%s "
            "retry_after_seconds=%s status_code=%s",
            lead_id,
            retry_after_seconds,
            exc.status_code,
        )
        failed_row = await _record_sync_failure(
            db=db,
            lead_id=lead_id,
            error_reason="crm_retryable_failure",
            retry_after_seconds=retry_after_seconds,
        )
        response.status_code = status.HTTP_502_BAD_GATEWAY
        return _sync_response_from_row(
            failed_row,
            detail="CRM sync failed; retry scheduled",
            retry_after_seconds=retry_after_seconds,
        )
    except CrmDeliveryPermanentError as exc:
        logger.warning(
            "Lead CRM sync received permanent failure lead_id=%s status_code=%s",
            lead_id,
            exc.status_code,
        )
        failed_row = await _record_sync_failure(
            db=db,
            lead_id=lead_id,
            error_reason="crm_permanent_failure",
            retry_after_seconds=None,
        )
        response.status_code = status.HTTP_502_BAD_GATEWAY
        return _sync_response_from_row(
            failed_row,
            detail="CRM sync rejected; manual intervention required",
        )
    except Exception as exc:
        logger.warning(
            "Lead CRM sync dispatch failed lead_id=%s retry_state=manual error_type=%s",
            lead_id,
            exc.__class__.__name__,
        )
        failed_row = await _record_sync_failure(
            db=db,
            lead_id=lead_id,
            error_reason="crm_dispatch_failed",
            retry_after_seconds=None,
        )

        response.status_code = status.HTTP_502_BAD_GATEWAY
        return _sync_response_from_row(
            failed_row,
            detail="CRM sync failed; manual retry required",
            retry_after_seconds=None,
        )

    try:
        synced_row = await update_lead_integration_status(
            db=db,
            lead_id=str(lead_id),
            integration_status=LeadIntegrationStatus.SYNCED,
            synced_at=datetime.now(UTC),
            reset_retry_attempts=True,
        )
    except LeadRepositoryUpdateConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found",
        ) from exc
    except LeadRepositoryUpdateError as exc:
        logger.warning("Lead sync tracking update failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lead sync failed",
        ) from exc

    logger.info("Lead CRM sync tracked lead_id=%s status=synced", lead_id)
    return _sync_response_from_row(synced_row, detail="Lead synced")


@router.get(
    "/leads/{lead_id}",
    response_model=LeadPublicResponse,
    summary="Retrieve one persisted lead for admin review",
)
async def read_lead(
    lead_id: UUID,
    _: None = Depends(require_admin_access),
    db: AsyncClient = Depends(get_db),
) -> LeadPublicResponse:
    """Return one admin-safe lead by UUID."""
    try:
        row = await get_lead_by_id(db=db, lead_id=str(lead_id))
    except LeadRepositoryLookupError as exc:
        logger.warning("Lead detail database failure")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lead lookup failed",
        ) from exc

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lead not found",
        )

    return _public_lead_from_row(row)


@router.post(
    "/leads",
    response_model=LeadPersistedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Persist an unstructured lead inquiry",
)
async def create_lead(
    request: LeadCreateRequest,
    response: Response,
    _: None = Depends(enforce_lead_intake_rate_limit),
    db: AsyncClient = Depends(get_db),
) -> LeadPersistedResponse:
    """Persist the public lead request contract without classification yet."""
    request_id = str(uuid4())
    try:
        lead = await persist_lead(db=db, request=request)
    except (LeadLookupFailed, LeadInsertFailed) as exc:
        logger.warning(
            "Lead persistence database failure category=%s request_id=%s",
            exc.__class__.__name__,
            request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lead persistence failed",
        ) from exc
    except LeadUnexpectedPersistenceFailure as exc:
        logger.error(
            "Lead persistence unexpected failure category=%s request_id=%s",
            exc.__class__.__name__,
            request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Lead persistence failed",
        ) from exc

    logger.info(
        "Lead %s persisted duplicate=%s request_id=%s",
        lead.id,
        lead.duplicate,
        request_id,
    )
    if lead.duplicate:
        response.status_code = status.HTTP_200_OK
    return lead
