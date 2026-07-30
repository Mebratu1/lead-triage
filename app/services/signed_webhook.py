"""Reusable HMAC-SHA256 signing for outbound JSON webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

import httpx

SIGNATURE_HEADER = "X-LeadTriage-Signature"
TIMESTAMP_HEADER = "X-LeadTriage-Timestamp"
EVENT_HEADER = "X-LeadTriage-Event"
IDEMPOTENCY_HEADER = "Idempotency-Key"


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    """Serialize a payload deterministically for signing and transport."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def signed_webhook_headers(
    body: bytes,
    secret: str,
    event_type: str,
    idempotency_key: str,
    timestamp: datetime | None = None,
) -> dict[str, str]:
    """Build replay-aware HMAC headers for one canonical JSON body."""
    if not secret:
        raise ValueError("webhook secret is required")
    if not event_type.strip():
        raise ValueError("event type is required")
    if not idempotency_key.strip():
        raise ValueError("idempotency key is required")

    signed_at = timestamp or datetime.now(UTC)
    unix_timestamp = str(int(signed_at.timestamp()))
    signed_content = unix_timestamp.encode("ascii") + b"." + body
    digest = hmac.new(
        secret.encode("utf-8"),
        signed_content,
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        EVENT_HEADER: event_type,
        IDEMPOTENCY_HEADER: idempotency_key,
        TIMESTAMP_HEADER: unix_timestamp,
        SIGNATURE_HEADER: f"sha256={digest}",
    }


def strict_timeout(timeout_seconds: float) -> httpx.Timeout:
    """Apply one strict upper bound to every HTTP timeout phase."""
    return httpx.Timeout(
        connect=timeout_seconds,
        read=timeout_seconds,
        write=timeout_seconds,
        pool=timeout_seconds,
    )


async def post_signed_json(
    *,
    url: str,
    secret: str,
    event_type: str,
    idempotency_key: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    client: httpx.AsyncClient | None = None,
    timestamp: datetime | None = None,
) -> httpx.Response:
    """POST canonical signed JSON without redirects or implicit retries."""
    body = canonical_json_bytes(payload)
    headers = signed_webhook_headers(
        body=body,
        secret=secret,
        event_type=event_type,
        idempotency_key=idempotency_key,
        timestamp=timestamp,
    )
    timeout = strict_timeout(timeout_seconds)
    if client is not None:
        return await client.post(
            url,
            content=body,
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
        )

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
    ) as active_client:
        return await active_client.post(
            url,
            content=body,
            headers=headers,
        )
