"""Stream-bound submission bodies before multipart spooling completes."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import settings


ASGIMessage = dict[str, Any]
Receive = Callable[[], Awaitable[ASGIMessage]]
Send = Callable[[ASGIMessage], Awaitable[None]]


class SubmissionBodyLimitMiddleware:
    """Count receive chunks without retaining or replaying request bodies."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive: Receive, send: Send) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/api/submissions"
        ):
            await self.app(scope, receive, send)
            return

        maximum = settings.submission_request_max_bytes
        content_length = _content_length(scope)
        if content_length is not None and content_length > maximum:
            await _send_too_large(send, maximum)
            return

        size = 0

        async def limited_receive() -> ASGIMessage:
            nonlocal size
            message = await receive()
            if message.get("type") == "http.request":
                size += len(message.get("body", b""))
                if size > maximum:
                    raise _RequestBodyTooLarge
            return message

        response_started = False

        async def tracked_send(message: ASGIMessage) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyTooLarge:
            if response_started:
                raise
            await _send_too_large(send, maximum)


class _RequestBodyTooLarge(OSError):
    pass


def _content_length(scope) -> int | None:
    for name, value in scope.get("headers", ()):
        if name.lower() != b"content-length":
            continue
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


async def _send_too_large(send: Send, maximum: int) -> None:
    body = json.dumps(
        {
            "detail": {
                "error_code": "REQUEST_BODY_TOO_LARGE",
                "message": (
                    f"multipart request exceeds {maximum} bytes"
                ),
            }
        }
    ).encode("utf-8")
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
