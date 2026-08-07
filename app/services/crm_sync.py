"""Outbound CRM synchronization boundary."""

from __future__ import annotations

from typing import Mapping, Protocol
from urllib.parse import quote

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
                    "integration_retry_state",
                    "integration_next_attempt_at",
                    "integration_retry_attempt_count",
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


class HubSpotCrmDispatcher:
    """Upsert admin-safe leads as HubSpot contacts by their unique email address."""

    contacts_url = "https://api.hubapi.com/crm/objects/2026-03/contacts"

    def __init__(
        self,
        *,
        access_token: str,
        timeout_seconds: float,
        property_map: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.access_token = access_token
        self.timeout_seconds = timeout_seconds
        self.property_map = dict(property_map or {})
        self.client = client

    @staticmethod
    def _split_name(name: str | None) -> tuple[str | None, str | None]:
        if name is None or not name.strip():
            return None, None
        first_name, *remaining_name_parts = name.strip().split(maxsplit=1)
        return first_name, remaining_name_parts[0] if remaining_name_parts else None

    def _properties_for(
        self,
        lead: LeadPublicResponse,
        *,
        normalized_email: str,
        is_create: bool,
    ) -> dict[str, str]:
        """Map standard contact fields plus explicitly configured custom fields."""
        first_name, last_name = self._split_name(lead.customer_name)
        properties = {"email": normalized_email}
        if is_create:
            properties["lifecyclestage"] = "lead"
        if first_name is not None:
            properties["firstname"] = first_name
        if last_name is not None:
            properties["lastname"] = last_name
        if lead.customer_phone:
            properties["phone"] = lead.customer_phone

        source_values = {
            "id": str(lead.id),
            "source": lead.source,
            "name": lead.customer_name,
            "email": normalized_email,
            "phone": lead.customer_phone,
            "message": lead.message,
            "urgency": lead.urgency,
            "summary": lead.summary,
            "created_at": lead.created_at.isoformat(),
        }
        for source, property_name in self.property_map.items():
            value = source_values.get(source)
            if value is not None and str(value).strip():
                properties[property_name] = str(value)
        return properties

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs: object,
    ) -> httpx.Response:
        request_kwargs = {
            "headers": self._headers(),
            "timeout": self.timeout_seconds,
            **kwargs,
        }
        try:
            if self.client is not None:
                return await self.client.request(method, url, **request_kwargs)
            async with httpx.AsyncClient() as client:
                return await client.request(method, url, **request_kwargs)
        except httpx.TransportError as exc:
            raise CrmDeliveryRetryableError("hubspot_transport_failed") from exc

    @staticmethod
    def _raise_for_unsuccessful_response(response: httpx.Response) -> None:
        if 200 <= response.status_code < 300:
            return
        if response.status_code == 429 or response.status_code >= 500:
            raise CrmDeliveryRetryableError(
                "hubspot_retryable_response",
                status_code=response.status_code,
            )
        raise CrmDeliveryPermanentError(
            "hubspot_permanent_response",
            status_code=response.status_code,
        )

    async def _contact_id_for_email(self, email: str) -> str | None:
        response = await self._request(
            "GET",
            f"{self.contacts_url}/{quote(email, safe='')}",
            params={"idProperty": "email"},
        )
        if response.status_code == 404:
            return None
        self._raise_for_unsuccessful_response(response)
        try:
            contact_id = response.json().get("id")
        except (TypeError, ValueError):
            contact_id = None
        if not isinstance(contact_id, str) or not contact_id:
            raise CrmDeliveryRetryableError("hubspot_invalid_response")
        return contact_id

    async def _update_contact(
        self, contact_id: str, properties: dict[str, str]
    ) -> None:
        response = await self._request(
            "PATCH",
            f"{self.contacts_url}/{quote(contact_id, safe='')}",
            json={"properties": properties},
        )
        self._raise_for_unsuccessful_response(response)

    async def sync_lead(self, lead: LeadPublicResponse) -> None:
        """Create or update one contact, preserving email-based idempotency."""
        if lead.customer_email is None or not lead.customer_email.strip():
            raise CrmDeliveryPermanentError("hubspot_email_required")

        normalized_email = lead.customer_email.strip()
        contact_id = await self._contact_id_for_email(normalized_email)
        if contact_id is not None:
            await self._update_contact(
                contact_id,
                self._properties_for(
                    lead,
                    normalized_email=normalized_email,
                    is_create=False,
                ),
            )
            return

        response = await self._request(
            "POST",
            self.contacts_url,
            json={
                "properties": self._properties_for(
                    lead,
                    normalized_email=normalized_email,
                    is_create=True,
                )
            },
        )
        if response.status_code == 409:
            contact_id = await self._contact_id_for_email(normalized_email)
            if contact_id is None:
                raise CrmDeliveryRetryableError("hubspot_conflict_without_contact")
            await self._update_contact(
                contact_id,
                self._properties_for(
                    lead,
                    normalized_email=normalized_email,
                    is_create=False,
                ),
            )
            return
        self._raise_for_unsuccessful_response(response)


def configured_crm_dispatcher(
    client: httpx.AsyncClient | None = None,
) -> LeadCrmDispatcher:
    """Build the configured CRM dispatcher or fail closed."""
    if settings.crm_provider == "hubspot":
        if settings.hubspot_access_token is None:
            raise CrmConfigurationError("HubSpot access token is required")
        return HubSpotCrmDispatcher(
            access_token=settings.hubspot_access_token,
            timeout_seconds=settings.crm_webhook_timeout_seconds,
            property_map=settings.hubspot_property_map,
            client=client,
        )
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
