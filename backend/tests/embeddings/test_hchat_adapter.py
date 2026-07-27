from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.embeddings.base import (
    EmbeddingContractError,
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
        Settings(hchat_embedding_enabled=True, _env_file=None),
        transport=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(EmbeddingUnavailableError, match="not fully configured"):
        client.embed(["SERVO MOTOR"])
    assert requests == []


def test_azure_custom_codec_uses_api_key_header_not_bearer() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]},
        )

    client = HChatEmbeddingClient(
        endpoint="https://intranet.invalid/embeddings",
        api_key=SecretStr("office-key"),
        model="office-model",
        api_style="custom",
        transport=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.embed(["BEARING"])

    assert "api-key" in requests[0].headers
    assert requests[0].headers["api-key"] == "office-key"
    assert "Authorization" not in requests[0].headers


def test_azure_custom_codec_sends_input_only_payload_without_model() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0, 0.0]}, {"index": 1, "embedding": [0.0, 1.0]}]},
        )

    client = HChatEmbeddingClient(
        endpoint="https://intranet.invalid/embeddings",
        api_key=SecretStr("office-key"),
        model="office-model",
        api_style="custom",
        transport=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.embed(["BEARING", "MOTOR"])

    payload = json.loads(requests[0].content)
    assert payload == {"input": ["BEARING", "MOTOR"]}
    assert "model" not in payload


def test_azure_custom_codec_parses_azure_response_and_preserves_order() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
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
        api_style="custom",
        transport=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    batch = client.embed(["BEARING", "MOTOR"])

    assert batch.vectors.tolist() == [[0.0, 1.0], [1.0, 0.0]]
    assert batch.model == "office-model"
    assert batch.dimension == 2


def test_azure_custom_codec_sends_project_id_header_when_configured() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]},
        )

    client = HChatEmbeddingClient(
        endpoint="https://intranet.invalid/embeddings",
        api_key=SecretStr("office-key"),
        model="office-model",
        api_style="custom",
        project_id="my-project-id",
        transport=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.embed(["BEARING"])

    assert requests[0].headers.get("X-Project-Id") == "my-project-id"


def test_azure_custom_codec_omits_project_id_header_when_not_configured() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]},
        )

    client = HChatEmbeddingClient(
        endpoint="https://intranet.invalid/embeddings",
        api_key=SecretStr("office-key"),
        model="office-model",
        api_style="custom",
        transport=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.embed(["BEARING"])

    assert "X-Project-Id" not in requests[0].headers


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


def make_openai_client(response_payload: object) -> HChatEmbeddingClient:
    return HChatEmbeddingClient(
        endpoint="https://intranet.invalid/embeddings",
        api_key="office-key",
        model="office-model",
        api_style="openai",
        transport=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=response_payload)
            )
        ),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"data": []},
        {"data": [{"index": 0, "embedding": []}]},
        {
            "data": [
                {"index": 0, "embedding": [1.0, 0.0]},
                {"index": 1, "embedding": [0.0, 1.0]},
            ]
        },
        {
            "data": [
                {
                    "index": 0,
                    "embedding": [10**1000, 0],
                }
            ]
        },
    ],
)
def test_malformed_openai_batch_raises_safe_contract_error(
    payload: object,
) -> None:
    client = make_openai_client(payload)

    with pytest.raises(EmbeddingContractError):
        client.embed(["BEARING"])


@pytest.mark.parametrize("response_model", ["different-office-model", None])
def test_openai_response_model_must_match_when_present(
    response_model: object,
) -> None:
    client = make_openai_client(
        {
            "model": response_model,
            "data": [{"index": 0, "embedding": [1.0, 0.0]}],
        }
    )

    with pytest.raises(EmbeddingContractError, match="model"):
        client.embed(["BEARING"])


def test_openai_response_without_model_remains_compatible() -> None:
    client = make_openai_client(
        {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
    )

    assert client.embed(["BEARING"]).model == "office-model"
