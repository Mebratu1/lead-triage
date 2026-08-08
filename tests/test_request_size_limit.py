"""Tests for raw HTTP request-size enforcement."""

from __future__ import annotations

import asyncio

import pytest

from app.middleware import RequestBodySizeLimitMiddleware


@pytest.mark.unit
def test_request_size_limit_rejects_oversized_stream_without_content_length():
    """Test streamed request chunks cannot bypass the configured byte limit."""
    app_called = False
    received_messages = iter(
        [
            {"type": "http.request", "body": b"abcd", "more_body": True},
            {"type": "http.request", "body": b"efg", "more_body": False},
        ]
    )
    sent_messages: list[dict] = []

    async def app(scope, receive, send) -> None:
        nonlocal app_called
        app_called = True

    async def receive() -> dict:
        return next(received_messages)

    async def send(message: dict) -> None:
        sent_messages.append(message)

    middleware = RequestBodySizeLimitMiddleware(app, max_bytes=6)
    asyncio.run(
        middleware(
            {"type": "http", "headers": []},
            receive,
            send,
        )
    )

    assert app_called is False
    assert sent_messages == [
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", b"35"),
            ],
        },
        {
            "type": "http.response.body",
            "body": b'{"detail":"Request body too large"}',
        },
    ]
