from __future__ import annotations

import asyncio
import json

from app.core.config import settings
from app.middleware.submission_size import SubmissionBodyLimitMiddleware


def _run_middleware(messages, monkeypatch, *, maximum: int):
    monkeypatch.setattr(settings, "submission_request_max_bytes", maximum)
    received: list[bytes] = []
    sent: list[dict] = []
    remaining = iter(messages)

    async def receive():
        return next(remaining)

    async def send(message):
        sent.append(message)

    async def downstream(scope, receive, send):
        while True:
            message = await receive()
            received.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204})
        await send({"type": "http.response.body", "body": b""})

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/submissions",
        "headers": [],
    }
    asyncio.run(
        SubmissionBodyLimitMiddleware(downstream)(scope, receive, send)
    )
    return received, sent


def test_fragmented_body_streams_to_downstream_without_replay_buffer(
    monkeypatch,
) -> None:
    received, sent = _run_middleware(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ],
        monkeypatch,
        maximum=6,
    )

    assert received == [b"abc", b"def"]
    assert sent[0]["status"] == 204


def test_fragmented_body_without_content_length_aborts_at_limit(
    monkeypatch,
) -> None:
    received, sent = _run_middleware(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"defg", "more_body": False},
        ],
        monkeypatch,
        maximum=6,
    )

    assert received == [b"abc"]
    assert sent[0]["status"] == 413
    payload = json.loads(sent[1]["body"])
    assert payload["detail"]["error_code"] == "REQUEST_BODY_TOO_LARGE"
