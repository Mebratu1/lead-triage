"""Tests for signed outbound CRM webhook delivery."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from app.models.lead import LeadIntegrationStatus
from app.models.schemas import LeadPublicResponse
from app.services.crm_sync import (
    CrmDeliveryPermanentError,
    CrmDeliveryRetryableError,
    SignedWebhookCrmDispatcher,
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
