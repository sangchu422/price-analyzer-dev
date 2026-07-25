from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

import numpy as np


class EmbeddingError(RuntimeError):
    """Base error for optional embedding support."""


class EmbeddingUnavailableError(EmbeddingError):
    """Embedding cannot be used, so callers should retain lexical behavior."""


class EmbeddingContractNotConfiguredError(EmbeddingUnavailableError):
    """The office-specific hChat request/response contract is not configured."""


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: np.ndarray
    model: str
    dimension: int

    def __post_init__(self) -> None:
        vectors = np.asarray(self.vectors, dtype=np.float32)
        if vectors.ndim != 2:
            raise ValueError("embedding vectors must be a 2-D array")
        if vectors.shape[1] != self.dimension:
            raise ValueError("embedding dimension does not match vectors")
        if not self.model.strip():
            raise ValueError("embedding model must not be blank")
        if not np.isfinite(vectors).all():
            raise ValueError("embedding vectors must contain only finite values")
        object.__setattr__(self, "vectors", vectors)


class EmbeddingClient(Protocol):
    def embed(self, texts: Sequence[str]) -> EmbeddingBatch: ...
