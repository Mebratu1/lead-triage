"""ASGI middleware for request-level HTTP safeguards."""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodySizeLimitMiddleware:
    """Reject HTTP request bodies that exceed a configured byte limit."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = _content_length(scope)
        if content_length is not None and content_length > self.max_bytes:
            await _send_request_too_large(send)
            return

        buffered_messages: list[Message] = []
        received_bytes = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                buffered_messages.append(message)
                continue

            received_bytes += len(message.get("body", b""))
            if received_bytes > self.max_bytes:
                await _send_request_too_large(send)
                return

            buffered_messages.append(message)
            if not message.get("more_body", False):
                break

        async def replay_receive() -> Message:
            if buffered_messages:
                return buffered_messages.pop(0)
            return await receive()

        await self.app(scope, replay_receive, send)


def _content_length(scope: Scope) -> int | None:
    """Return a valid declared content length, if the client provided one."""
    for name, value in scope.get("headers", []):
        if name.lower() != b"content-length":
            continue
        try:
            declared_length = int(value)
        except ValueError:
            return None
        return declared_length if declared_length >= 0 else None
    return None


async def _send_request_too_large(send: Send) -> None:
    """Send a compact, safe 413 response without calling the application."""
    body = b'{"detail":"Request body too large"}'
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
