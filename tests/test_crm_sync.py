"""Tests for signed outbound CRM webhook delivery."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from app.models.lead import LeadIntegrationStatus
from app.models.schemas import LeadPublicResponse
from app.services.crm_sync import (
    CrmDeliveryPermanentError,
    CrmDeliveryRetryableError,
    HubSpotCrmDispatcher,
    SignedWebhookCrmDispatcher,
    configured_crm_dispatcher,
)

WEBHOOK_SECRET = "crm-test-signing-secret-with-32-bytes"


def lead_response() -> LeadPublicResponse:
    """Build one admin-safe CRM payload."""
    return LeadPublicResponse(
        id=UUID("11111111-1111-4111-8111-111111111111"),
        source="website",
        customer_name="Maria Customer",
        customer_email="maria@example.com",
        customer_phone="301-555-0144",
        message="I need emergency plumbing today.",
        classification_status="classified",
        urgency="hot",
        summary="Customer needs plumbing help.",
        classification_attempt_count=1,
        integration_status=LeadIntegrationStatus.PENDING,
        integration_last_synced_at=None,
        created_at=datetime(2026, 7, 30, 12, tzinfo=UTC),
        updated_at=datetime(2026, 7, 30, 12, 5, tzinfo=UTC),
    )


async def deliver_with_handler(handler) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        dispatcher = SignedWebhookCrmDispatcher(
            url="https://crm.example.test/webhooks/leads",
            secret=WEBHOOK_SECRET,
            timeout_seconds=2.5,
            client=client,
        )
        await dispatcher.sync_lead(lead_response())


class TestSignedWebhookCrmDispatcher:
    """HMAC, idempotency, timeout, and status handling tests."""

    @pytest.mark.unit
    def test_sends_canonical_signed_payload_with_lead_id_idempotency(self):
        """Test body bytes and replay/idempotency headers are aligned."""
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["request"] = request
            return httpx.Response(204)

        asyncio.run(deliver_with_handler(handler))

        request = captured["request"]
        assert isinstance(request, httpx.Request)
        body = request.content
        parsed_body = json.loads(body)
        assert parsed_body["event"] == "lead.sync"
        assert parsed_body["lead"]["id"] == "11111111-1111-4111-8111-111111111111"
        assert "integration_status" not in parsed_body["lead"]
        assert "integration_last_synced_at" not in parsed_body["lead"]
        assert (
            request.headers["Idempotency-Key"]
            == "11111111-1111-4111-8111-111111111111"
        )
        assert request.headers["X-LeadTriage-Event"] == "lead.sync"
        timestamp = request.headers["X-LeadTriage-Timestamp"]
        expected_digest = hmac.new(
            WEBHOOK_SECRET.encode(),
            timestamp.encode("ascii") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        assert (
            request.headers["X-LeadTriage-Signature"]
            == f"sha256={expected_digest}"
        )
        assert request.extensions["timeout"] == {
            "connect": 2.5,
            "read": 2.5,
            "write": 2.5,
            "pool": 2.5,
        }

    @pytest.mark.unit
    @pytest.mark.parametrize("status_code", [429, 500, 503])
    def test_429_and_5xx_are_retryable(self, status_code: int):
        """Test only transient HTTP responses enter the retry queue."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code, text="private upstream response")

        with pytest.raises(CrmDeliveryRetryableError) as raised:
            asyncio.run(deliver_with_handler(handler))

        assert raised.value.status_code == status_code
        assert "private upstream response" not in str(raised.value)

    @pytest.mark.unit
    @pytest.mark.parametrize("status_code", [301, 400, 401, 422])
    def test_redirects_and_other_4xx_are_permanent(self, status_code: int):
        """Test redirects and non-429 client errors are not retried."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code)

        with pytest.raises(CrmDeliveryPermanentError) as raised:
            asyncio.run(deliver_with_handler(handler))

        assert raised.value.status_code == status_code

    @pytest.mark.unit
    def test_transport_timeout_is_retryable_without_leaking_details(self):
        """Test strict timeout failures are sanitized and retryable."""
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout(
                "private timeout detail with customer data",
                request=request,
            )

        with pytest.raises(CrmDeliveryRetryableError) as raised:
            asyncio.run(deliver_with_handler(handler))

        assert str(raised.value) == "crm_transport_failed"
        assert "customer data" not in str(raised.value)


async def deliver_hubspot_with_handler(
    handler,
    *,
    property_map: dict[str, str] | None = None,
    lead: LeadPublicResponse | None = None,
) -> None:
    """Deliver one lead through a transport-isolated HubSpot adapter."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        dispatcher = HubSpotCrmDispatcher(
            access_token="hubspot-private-app-token",
            timeout_seconds=2.5,
            property_map=property_map,
            client=client,
        )
        await dispatcher.sync_lead(lead or lead_response())


class TestHubSpotCrmDispatcher:
    """HubSpot contact mapping, upsert behavior, and failure classification tests."""

    @pytest.mark.unit
    def test_creates_contact_with_standard_and_explicit_custom_properties(self):
        """Test a missing email contact is created with only configured custom fields."""
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(404)
            return httpx.Response(201, json={"id": "123"})

        asyncio.run(
            deliver_hubspot_with_handler(
                handler,
                property_map={
                    "id": "lead_triage_id",
                    "source": "lead_source",
                    "urgency": "lead_urgency",
                    "summary": "lead_summary",
                },
            )
        )

        assert [request.method for request in requests] == ["GET", "POST"]
        assert requests[0].url.params["idProperty"] == "email"
        assert requests[0].headers["Authorization"] == "Bearer hubspot-private-app-token"
        payload = json.loads(requests[1].content)
        assert payload == {
            "properties": {
                "email": "maria@example.com",
                "firstname": "Maria",
                "lastname": "Customer",
                "phone": "301-555-0144",
                "lifecyclestage": "lead",
                "lead_triage_id": "11111111-1111-4111-8111-111111111111",
                "lead_source": "website",
                "lead_urgency": "hot",
                "lead_summary": "Customer needs plumbing help.",
            }
        }
        assert "message" not in json.dumps(payload)
        assert requests[1].extensions["timeout"] == {
            "connect": 2.5,
            "read": 2.5,
            "write": 2.5,
            "pool": 2.5,
        }

    @pytest.mark.unit
    def test_updates_existing_contact_by_email(self):
        """Test a known email is updated instead of creating a duplicate contact."""
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json={"id": "hubspot-contact-123"})
            return httpx.Response(200)

        asyncio.run(deliver_hubspot_with_handler(handler))

        assert [request.method for request in requests] == ["GET", "PATCH"]
        assert requests[1].url.path.endswith("/hubspot-contact-123")
        update_payload = json.loads(requests[1].content)
        assert update_payload["properties"]["email"] == "maria@example.com"
        assert "lifecyclestage" not in update_payload["properties"]

    @pytest.mark.unit
    def test_normalizes_email_once_for_lookup_and_all_mapped_properties(self):
        """Test one normalized email value drives lookup and the outbound payload."""
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(404)
            return httpx.Response(201, json={"id": "123"})

        lead = lead_response().model_copy(
            update={"customer_email": "  maria@example.com  "}
        )
        asyncio.run(
            deliver_hubspot_with_handler(
                handler,
                property_map={"email": "lead_email"},
                lead=lead,
            )
        )

        assert str(requests[0].url).startswith(
            "https://api.hubapi.com/crm/objects/2026-03/contacts/"
            "maria%40example.com"
        )
        payload = json.loads(requests[1].content)["properties"]
        assert payload["email"] == "maria@example.com"
        assert payload["lead_email"] == "maria@example.com"

    @pytest.mark.unit
    def test_create_conflict_refetches_and_updates_contact(self):
        """Test a create race resolves by refetching the email contact and updating it."""
        requests: list[httpx.Request] = []
        get_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal get_count
            requests.append(request)
            if request.method == "GET":
                get_count += 1
                if get_count == 1:
                    return httpx.Response(404)
                return httpx.Response(200, json={"id": "hubspot-contact-456"})
            if request.method == "POST":
                return httpx.Response(409)
            return httpx.Response(200)

        asyncio.run(deliver_hubspot_with_handler(handler))

        assert [request.method for request in requests] == [
            "GET",
            "POST",
            "GET",
            "PATCH",
        ]
        assert requests[-1].url.path.endswith("/hubspot-contact-456")

    @pytest.mark.unit
    def test_missing_email_requires_manual_intervention_without_a_request(self):
        """Test HubSpot never creates an unaddressable contact without an email."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(500)

        lead = lead_response().model_copy(update={"customer_email": None})
        with pytest.raises(CrmDeliveryPermanentError, match="hubspot_email_required"):
            asyncio.run(deliver_hubspot_with_handler(handler, lead=lead))

        assert call_count == 0

    @pytest.mark.unit
    @pytest.mark.parametrize("status_code", [429, 500, 503])
    def test_transient_hubspot_responses_are_retryable(self, status_code: int):
        """Test retry-safe CRM status classification is shared by the HubSpot adapter."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code, text="private upstream response")

        with pytest.raises(CrmDeliveryRetryableError) as raised:
            asyncio.run(deliver_hubspot_with_handler(handler))

        assert raised.value.status_code == status_code
        assert "private upstream response" not in str(raised.value)

    @pytest.mark.unit
    def test_hubspot_transport_errors_are_retryable_without_secret_leaks(self):
        """Test transport errors do not leak an upstream token or customer details."""
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout(
                "hubspot-private-app-token maria@example.com",
                request=request,
            )

        with pytest.raises(CrmDeliveryRetryableError) as raised:
            asyncio.run(deliver_hubspot_with_handler(handler))

        assert str(raised.value) == "hubspot_transport_failed"
        assert "hubspot-private-app-token" not in str(raised.value)

    @pytest.mark.unit
    def test_configured_dispatcher_selects_hubspot_only_when_opted_in(
        self, monkeypatch
    ):
        """Test the configured sync boundary uses HubSpot only after explicit selection."""
        monkeypatch.setattr(
            "app.services.crm_sync.settings",
            SimpleNamespace(
                crm_provider="hubspot",
                hubspot_access_token="hubspot-private-app-token",
                crm_webhook_timeout_seconds=2.5,
                hubspot_property_map={"source": "lead_source"},
            ),
        )

        dispatcher = configured_crm_dispatcher()

        assert isinstance(dispatcher, HubSpotCrmDispatcher)
        assert dispatcher.property_map == {"source": "lead_source"}
