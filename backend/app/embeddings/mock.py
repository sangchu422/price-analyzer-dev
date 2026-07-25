from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from app.embeddings.base import EmbeddingBatch
from app.matching.normalization import normalize_search_text


@dataclass(frozen=True)
class DeterministicMockEmbeddingClient:
    """Local test index only; it is not a production semantic model."""

    dimension: int = 128
    model: str = "local-mock-v1"

    def __post_init__(self) -> None:
        if self.dimension <= 0:
            raise ValueError("dimension must be positive")

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        vectors = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for row, text in enumerate(texts):
            normalized = f"  {normalize_search_text(text)}  "
            trigrams = (
                normalized[index : index + 3]
                for index in range(max(1, len(normalized) - 2))
            )
            for trigram in trigrams:
                digest = hashlib.sha256(trigram.encode("utf-8")).digest()
                bucket = int.from_bytes(digest[:8], "big") % self.dimension
                sign = 1.0 if digest[8] & 1 else -1.0
                vectors[row, bucket] += sign
            norm = np.linalg.norm(vectors[row])
            if norm:
                vectors[row] /= norm
        return EmbeddingBatch(
            vectors=vectors,
            model=self.model,
            dimension=self.dimension,
        )
