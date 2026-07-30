"""Outbound CRM synchronization boundary."""

from __future__ import annotations

from typing import Protocol

import httpx

from app.config import settings
from app.models.schemas import LeadPublicResponse
from app.services.signed_webhook import post_signed_json


class CrmConfigurationError(RuntimeError):
    """Raised when CRM delivery is attempted without a complete configuration."""


class CrmDeliveryError(RuntimeError):
    """Base class for sanitized outbound delivery failures."""

    def __init__(self, reason: str, status_code: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


class CrmDeliveryRetryableError(CrmDeliveryError):
    """Raised for timeouts, network failures, HTTP 429, and HTTP 5xx."""


class CrmDeliveryPermanentError(CrmDeliveryError):
    """Raised for non-retryable HTTP responses."""


class LeadCrmDispatcher(Protocol):
    """Interface for CRM/webhook delivery implementations."""

    async def sync_lead(self, lead: LeadPublicResponse) -> None:
        """Send one admin-safe lead payload to an external CRM."""


class SignedWebhookCrmDispatcher:
    """Deliver admin-safe leads through a signed HTTPS webhook."""

    def __init__(
        self,
        *,
        url: str,
        secret: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = url
        self.secret = secret
        self.timeout_seconds = timeout_seconds
        self.client = client

    async def sync_lead(self, lead: LeadPublicResponse) -> None:
        """Send one lead with HMAC, timestamp, and lead-ID idempotency headers."""
        payload = {
            "event": "lead.sync",
            "lead": lead.model_dump(
                mode="json",
                exclude={
                    "integration_status",
                    "integration_last_synced_at",
                },
            ),
        }
        try:
            response = await post_signed_json(
                url=self.url,
                secret=self.secret,
                event_type="lead.sync",
                idempotency_key=str(lead.id),
                payload=payload,
                timeout_seconds=self.timeout_seconds,
                client=self.client,
            )
        except httpx.TransportError as exc:
            raise CrmDeliveryRetryableError("crm_transport_failed") from exc

        if 200 <= response.status_code < 300:
            return
        if response.status_code == 429 or response.status_code >= 500:
            raise CrmDeliveryRetryableError(
                "crm_retryable_response",
                status_code=response.status_code,
            )
        raise CrmDeliveryPermanentError(
            "crm_permanent_response",
            status_code=response.status_code,
        )


def configured_crm_dispatcher(
    client: httpx.AsyncClient | None = None,
) -> SignedWebhookCrmDispatcher:
    """Build the configured CRM dispatcher or fail closed."""
    if settings.crm_webhook_url is None or settings.crm_webhook_secret is None:
        raise CrmConfigurationError("CRM webhook URL and secret are required")
    return SignedWebhookCrmDispatcher(
        url=settings.crm_webhook_url,
        secret=settings.crm_webhook_secret,
        timeout_seconds=settings.crm_webhook_timeout_seconds,
        client=client,
    )


async def dispatch_lead_to_crm(
    lead: LeadPublicResponse,
    dispatcher: LeadCrmDispatcher | None = None,
) -> None:
    """Dispatch one lead through the configured CRM delivery boundary."""
    if dispatcher is not None:
        await dispatcher.sync_lead(lead)
        return

    await configured_crm_dispatcher().sync_lead(lead)
