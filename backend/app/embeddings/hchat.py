from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

import httpx
import numpy as np
from pydantic import SecretStr

from app.core.config import Settings
from app.embeddings.base import (
    EmbeddingBatch,
    EmbeddingClient,
    EmbeddingContractError,
    EmbeddingContractNotConfiguredError,
    EmbeddingUnavailableError,
)


@dataclass(repr=False)
class UnavailableEmbeddingClient:
    reason: str

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        del texts
        raise EmbeddingUnavailableError(self.reason)


@dataclass(repr=False)
class HChatEmbeddingClient:
    endpoint: str
    api_key: SecretStr | str
    model: str
    api_style: Literal["openai", "custom"] = "custom"
    timeout_seconds: float = 10.0
    transport: httpx.Client | None = None
    _client: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.endpoint.strip() or not self.model.strip():
            raise ValueError("endpoint and model must not be blank")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if isinstance(self.api_key, str):
            self.api_key = SecretStr(self.api_key)
        self._client = self.transport or httpx.Client(
            timeout=self.timeout_seconds,
        )

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        values = list(texts)
        if not values:
            return EmbeddingBatch(
                vectors=np.empty((0, 0), dtype=np.float32),
                model=self.model,
                dimension=0,
            )
        if self.api_style == "custom":
            raise EmbeddingContractNotConfiguredError(
                "The custom hChat contract is not configured. After receiving "
                "the office sample, update only _build_payload and "
                "_parse_response."
            )
        try:
            response = self._client.post(
                self.endpoint,
                headers={
                    "Authorization": (
                        f"Bearer {self.api_key.get_secret_value()}"
                    )
                },
                json=self._build_payload(values),
            )
            response.raise_for_status()
            return self._parse_response(response.json(), len(values))
        except (
            EmbeddingContractError,
            EmbeddingContractNotConfiguredError,
        ):
            raise
        except (
            httpx.HTTPError,
            IndexError,
            KeyError,
            OverflowError,
            TypeError,
            ValueError,
        ) as exc:
            raise EmbeddingUnavailableError(
                "hChat embedding is unavailable; deterministic lexical "
                "matching remains active"
            ) from exc

    def _build_payload(self, texts: list[str]) -> dict[str, Any]:
        if self.api_style != "openai":
            raise EmbeddingContractNotConfiguredError(
                "Update only _build_payload and _parse_response for custom "
                "hChat"
            )
        return {"input": texts, "model": self.model}

    def _parse_response(
        self,
        payload: Any,
        expected_count: int,
    ) -> EmbeddingBatch:
        if self.api_style != "openai":
            raise EmbeddingContractNotConfiguredError(
                "Update only _build_payload and _parse_response for custom "
                "hChat"
            )
        try:
            if not isinstance(payload, dict):
                raise TypeError("embedding response must be an object")
            if (
                "model" in payload
                and payload["model"] != self.model
            ):
                raise EmbeddingContractError(
                    "embedding response model does not match configured model"
                )
            rows = payload["data"]
            if not isinstance(rows, list) or len(rows) != expected_count:
                raise ValueError("embedding response item count mismatch")
            if any(
                not isinstance(row, dict)
                or type(row.get("index")) is not int
                for row in rows
            ):
                raise ValueError("embedding response rows are invalid")
            ordered = sorted(rows, key=lambda row: row["index"])
            if [row["index"] for row in ordered] != list(
                range(expected_count)
            ):
                raise ValueError("embedding response indexes are invalid")
            vectors = np.asarray(
                [row["embedding"] for row in ordered],
                dtype=np.float32,
            )
            if (
                vectors.ndim != 2
                or vectors.shape[0] != expected_count
                or vectors.shape[1] <= 0
            ):
                raise ValueError("embedding response vectors are invalid")
            return EmbeddingBatch(
                vectors=vectors,
                model=self.model,
                dimension=vectors.shape[1],
            )
        except EmbeddingContractError:
            raise
        except (
            IndexError,
            KeyError,
            OverflowError,
            TypeError,
            ValueError,
        ) as exc:
            raise EmbeddingContractError(
                "embedding response violates the OpenAI-compatible contract"
            ) from exc


def build_embedding_client(
    settings: Settings,
    *,
    transport: httpx.Client | None = None,
) -> EmbeddingClient:
    if not settings.hchat_embedding_enabled:
        return UnavailableEmbeddingClient(
            "hChat embedding is disabled by default"
        )
    if not (
        settings.hchat_embedding_endpoint
        and settings.hchat_embedding_api_key
        and settings.hchat_embedding_model
    ):
        return UnavailableEmbeddingClient(
            "hChat embedding is enabled but not fully configured"
        )
    return HChatEmbeddingClient(
        endpoint=settings.hchat_embedding_endpoint,
        api_key=settings.hchat_embedding_api_key,
        model=settings.hchat_embedding_model,
        api_style=settings.hchat_embedding_api_style,
        timeout_seconds=settings.hchat_embedding_timeout_seconds,
        transport=transport,
    )
