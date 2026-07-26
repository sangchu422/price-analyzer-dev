"""Bound submission request bodies before multipart parsing or spooling."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import settings


ASGIMessage = dict[str, Any]
Receive = Callable[[], Awaitable[ASGIMessage]]
Send = Callable[[ASGIMessage], Awaitable[None]]


class SubmissionBodyLimitMiddleware:
    """Pre-read one bounded multipart body, then replay it to FastAPI."""

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

        messages: list[ASGIMessage] = []
        size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message.get("type") == "http.request":
                size += len(message.get("body", b""))
                if size > maximum:
                    await _send_too_large(send, maximum)
                    return
                if not message.get("more_body", False):
                    break
            elif message.get("type") == "http.disconnect":
                return

        index = 0

        async def replay() -> ASGIMessage:
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return {
                "type": "http.request",
                "body": b"",
                "more_body": False,
            }

        await self.app(scope, replay, send)


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
