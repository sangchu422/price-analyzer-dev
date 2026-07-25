from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.embeddings.base import (
    EmbeddingContractNotConfiguredError,
    EmbeddingUnavailableError,
)
from app.embeddings.hchat import HChatEmbeddingClient, build_embedding_client


def test_hchat_is_disabled_without_explicit_configuration() -> None:
    client = build_embedding_client(Settings(hchat_embedding_enabled=False))

    with pytest.raises(EmbeddingUnavailableError, match="disabled"):
        client.embed(["SERVO MOTOR"])


def test_enabled_hchat_requires_every_setting_before_http_is_possible() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    client = build_embedding_client(
        Settings(hchat_embedding_enabled=True),
        transport=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(EmbeddingUnavailableError, match="not fully configured"):
        client.embed(["SERVO MOTOR"])
    assert requests == []


def test_custom_codec_is_a_safe_office_side_extension_point() -> None:
    client = HChatEmbeddingClient(
        endpoint="https://intranet.invalid/embeddings",
        api_key=SecretStr("office-key"),
        model="office-model",
        api_style="custom",
        transport=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: pytest.fail("custom codec must not issue HTTP")
            )
        ),
    )

    with pytest.raises(
        EmbeddingContractNotConfiguredError,
        match="_build_payload.*_parse_response",
    ):
        client.embed(["BEARING"])


def test_openai_compatible_codec_uses_configured_key_and_restores_order() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [1.0, 0.0]},
                    {"index": 0, "embedding": [0.0, 1.0]},
                ]
            },
        )

    client = HChatEmbeddingClient(
        endpoint="https://intranet.invalid/embeddings",
        api_key=SecretStr("office-key"),
        model="office-model",
        api_style="openai",
        transport=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    batch = client.embed(["BEARING", "MOTOR"])

    assert batch.vectors.tolist() == [[0.0, 1.0], [1.0, 0.0]]
    assert batch.vectors.dtype.name == "float32"
    assert batch.model == "office-model"
    assert batch.dimension == 2
    assert requests[0].headers["Authorization"] == "Bearer office-key"
    assert json.loads(requests[0].content) == {
        "input": ["BEARING", "MOTOR"],
        "model": "office-model",
    }


def test_direct_adapter_accepts_plain_string_key_from_office_sample() -> None:
    client = HChatEmbeddingClient(
        endpoint="https://intranet.invalid/embeddings",
        api_key="office-key",
        model="office-model",
        api_style="openai",
        transport=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "data": [
                            {"index": 0, "embedding": [1.0, 0.0]}
                        ]
                    },
                )
            )
        ),
    )

    assert client.embed(["BEARING"]).dimension == 2


def test_secret_is_not_exposed_by_settings_or_client_repr() -> None:
    secret = "never-print-this-office-key"
    settings = Settings(
        hchat_embedding_enabled=True,
        hchat_embedding_endpoint="https://intranet.invalid/embeddings",
        hchat_embedding_api_key=secret,
        hchat_embedding_model="office-model",
        hchat_embedding_api_style="openai",
    )
    client = build_embedding_client(settings)

    assert secret not in repr(settings)
    assert secret not in repr(client)
    assert secret not in json.dumps(
        settings.model_dump(mode="json"),
        ensure_ascii=False,
    )


def test_network_or_contract_failure_is_reported_as_unavailable() -> None:
    client = HChatEmbeddingClient(
        endpoint="https://intranet.invalid/embeddings",
        api_key=SecretStr("office-key"),
        model="office-model",
        api_style="openai",
        transport=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: (_ for _ in ()).throw(
                    httpx.ConnectError("office network unavailable")
                )
            )
        ),
    )

    with pytest.raises(EmbeddingUnavailableError, match="unavailable"):
        client.embed(["BEARING"])
