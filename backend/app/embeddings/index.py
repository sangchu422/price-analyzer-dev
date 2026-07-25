from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np


class IndexMismatchError(ValueError):
    """Persisted index does not match the active catalog/model."""


@dataclass(frozen=True)
class IndexMetadata:
    model: str
    dimension: int
    item_count: int
    catalog_fingerprint: str
    normalization_version: str
    created_at: datetime


@dataclass(frozen=True)
class EmbeddingIndex:
    item_ids: np.ndarray
    vectors: np.ndarray
    metadata: IndexMetadata

    def scores(self, query_vector: np.ndarray) -> dict[int, float]:
        vector = np.asarray(query_vector, dtype=np.float32)
        if vector.shape != (self.metadata.dimension,):
            raise IndexMismatchError("query dimension mismatch")
        norm = np.linalg.norm(vector)
        if not norm or not np.isfinite(norm):
            raise IndexMismatchError("query vector must be finite and non-zero")
        similarities = self.vectors @ (vector / norm)
        return {
            int(item_id): float(score)
            for item_id, score in zip(
                self.item_ids,
                similarities,
                strict=True,
            )
        }


def _validated_arrays(
    item_ids: np.ndarray,
    vectors: np.ndarray,
    metadata: IndexMetadata,
) -> tuple[np.ndarray, np.ndarray]:
    ids = np.asarray(item_ids, dtype=np.int64)
    matrix = np.asarray(vectors, dtype=np.float32)
    if ids.ndim != 1:
        raise ValueError("item ids must be a 1-D array")
    if matrix.ndim != 2:
        raise ValueError("vectors must be a 2-D array")
    if len(ids) != metadata.item_count or len(ids) != len(matrix):
        raise ValueError("item count does not match arrays")
    if matrix.shape[1] != metadata.dimension:
        raise ValueError("vector dimension does not match metadata")
    if len(set(ids.tolist())) != len(ids):
        raise ValueError("duplicate item ids are not allowed")
    if not np.isfinite(matrix).all():
        raise ValueError("vectors must contain only finite values")
    norms = np.linalg.norm(matrix, axis=1)
    if np.any(norms == 0):
        raise ValueError("vectors must be non-zero")
    return ids, (matrix / norms[:, None]).astype(np.float32)


def save_index(
    path: Path,
    *,
    item_ids: np.ndarray,
    vectors: np.ndarray,
    metadata: IndexMetadata,
) -> None:
    ids, matrix = _validated_arrays(item_ids, vectors, metadata)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata_payload = asdict(metadata)
    metadata_payload["created_at"] = metadata.created_at.isoformat()
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = temporary.name
            np.savez_compressed(
                temporary,
                item_ids=ids,
                vectors=matrix,
                metadata=np.array(
                    json.dumps(metadata_payload, sort_keys=True)
                ),
            )
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)


def load_index(
    path: Path,
    *,
    expected_model: str,
    expected_catalog_fingerprint: str,
) -> EmbeddingIndex:
    try:
        with np.load(Path(path), allow_pickle=False) as archive:
            ids = archive["item_ids"]
            vectors = archive["vectors"]
            raw_metadata = archive["metadata"].item()
    except (OSError, ValueError, KeyError) as exc:
        raise IndexMismatchError("embedding index is unreadable") from exc
    try:
        values = json.loads(str(raw_metadata))
        values["created_at"] = datetime.fromisoformat(values["created_at"])
        metadata = IndexMetadata(**values)
    except (TypeError, ValueError, KeyError) as exc:
        raise IndexMismatchError("embedding index metadata is invalid") from exc
    if metadata.model != expected_model:
        raise IndexMismatchError("embedding index model mismatch")
    if metadata.catalog_fingerprint != expected_catalog_fingerprint:
        raise IndexMismatchError("embedding index catalog fingerprint mismatch")
    try:
        ids, matrix = _validated_arrays(ids, vectors, metadata)
    except ValueError as exc:
        raise IndexMismatchError(str(exc)) from exc
    return EmbeddingIndex(item_ids=ids, vectors=matrix, metadata=metadata)
