from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from app.embeddings.base import EmbeddingUnavailableError
from app.embeddings.index import (
    EmbeddingIndex,
    IndexMetadata,
    IndexMismatchError,
    load_index,
    save_index,
)
from app.embeddings.mock import DeterministicMockEmbeddingClient
from app.matching.candidates import CandidateItem, MatchQuery, rank_candidates


def metadata(*, model: str = "mock-v1", count: int = 2) -> IndexMetadata:
    return IndexMetadata(
        model=model,
        dimension=2,
        item_count=count,
        catalog_fingerprint="catalog-a",
        normalization_version="match-v1",
        created_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )


def test_index_rejects_model_or_catalog_mismatch(tmp_path) -> None:
    path = tmp_path / "items.npz"
    save_index(
        path,
        item_ids=np.array([1, 2]),
        vectors=np.eye(2, dtype=np.float32),
        metadata=metadata(),
    )

    with pytest.raises(IndexMismatchError, match="model"):
        load_index(
            path,
            expected_model="office-model",
            expected_catalog_fingerprint="catalog-a",
        )
    with pytest.raises(IndexMismatchError, match="catalog"):
        load_index(
            path,
            expected_model="mock-v1",
            expected_catalog_fingerprint="catalog-b",
        )


@pytest.mark.parametrize(
    ("item_ids", "vectors", "meta", "message"),
    [
        (
            np.array([1, 1]),
            np.eye(2, dtype=np.float32),
            metadata(),
            "duplicate",
        ),
        (
            np.array([1, 2]),
            np.array([[np.nan, 0.0], [0.0, 1.0]], dtype=np.float32),
            metadata(),
            "finite",
        ),
        (
            np.array([1, 2]),
            np.eye(2, dtype=np.float32),
            metadata(count=3),
            "item count",
        ),
    ],
)
def test_save_rejects_invalid_index(
    tmp_path,
    item_ids: np.ndarray,
    vectors: np.ndarray,
    meta: IndexMetadata,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        save_index(
            tmp_path / "items.npz",
            item_ids=item_ids,
            vectors=vectors,
            metadata=meta,
        )


def test_save_is_atomic_and_load_normalizes_vectors(tmp_path) -> None:
    path = tmp_path / "items.npz"
    save_index(
        path,
        item_ids=np.array([2, 1]),
        vectors=np.array([[3.0, 0.0], [0.0, 4.0]], dtype=np.float64),
        metadata=metadata(),
    )

    loaded = load_index(
        path,
        expected_model="mock-v1",
        expected_catalog_fingerprint="catalog-a",
    )

    assert loaded.item_ids.tolist() == [2, 1]
    assert loaded.vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(loaded.vectors, axis=1), 1.0)
    assert not list(tmp_path.glob("*.tmp"))


def test_mock_embeddings_are_deterministic_normalized_and_labeled() -> None:
    client = DeterministicMockEmbeddingClient(dimension=32)

    first = client.embed(["Servo   Motor", "BEARING"])
    second = client.embed(["SERVO MOTOR", "BEARING"])

    assert first.model == "local-mock-v1"
    assert first.dimension == 32
    assert first.vectors.dtype == np.float32
    assert np.array_equal(first.vectors, second.vectors)
    assert np.allclose(np.linalg.norm(first.vectors, axis=1), 1.0)


class UnavailableClient:
    def embed(self, texts):
        raise EmbeddingUnavailableError("disabled locally")


def test_candidate_ranking_gracefully_falls_back_to_lexical() -> None:
    result = rank_candidates(
        query=MatchQuery(name="BALL BEARING", spec=None, unit="EA"),
        items=[
            CandidateItem(
                standard_item_id=1,
                name="BALL BEARING",
                unit="EA",
            )
        ],
        embedding_client=UnavailableClient(),
        embedding_index=None,
    )[0]

    assert result.embedding_score is None
    assert result.embedding_status == "UNAVAILABLE"
    assert result.final_score == result.name_score


class StaticClient:
    def __init__(self, vector: np.ndarray, model: str = "office-model"):
        self.vector = vector
        self.model = model

    def embed(self, texts):
        from app.embeddings.base import EmbeddingBatch

        return EmbeddingBatch(
            vectors=np.repeat(self.vector[None, :], len(texts), axis=0),
            model=self.model,
            dimension=self.vector.shape[0],
        )


def test_embedding_can_raise_score_but_not_bypass_unit_conflict() -> None:
    index = EmbeddingIndex(
        item_ids=np.array([1], dtype=np.int64),
        vectors=np.array([[1.0, 0.0]], dtype=np.float32),
        metadata=IndexMetadata(
            model="office-model",
            dimension=2,
            item_count=1,
            catalog_fingerprint="catalog-a",
            normalization_version="match-v1",
            created_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        ),
    )
    client = StaticClient(np.array([1.0, 0.0], dtype=np.float32))

    boosted = rank_candidates(
        query=MatchQuery(name="ROTARY SUPPORT", spec=None, unit="EA"),
        items=[
            CandidateItem(
                standard_item_id=1,
                name="BALL BEARING",
                unit="EA",
            )
        ],
        embedding_client=client,
        embedding_index=index,
    )[0]
    blocked = rank_candidates(
        query=MatchQuery(name="ROTARY SUPPORT", spec=None, unit="M"),
        items=[
            CandidateItem(
                standard_item_id=1,
                name="BALL BEARING",
                unit="EA",
            )
        ],
        embedding_client=client,
        embedding_index=index,
    )

    assert boosted.embedding_score == pytest.approx(1)
    assert boosted.embedding_status == "AVAILABLE"
    assert boosted.method == "EMBEDDING_CANDIDATE_V1"
    assert blocked == []


def test_mock_index_is_never_labeled_available_for_automatic_use() -> None:
    mock = DeterministicMockEmbeddingClient(dimension=16)
    query_vector = mock.embed(["BALL BEARING"]).vectors
    index = EmbeddingIndex(
        item_ids=np.array([1]),
        vectors=query_vector,
        metadata=IndexMetadata(
            model="local-mock-v1",
            dimension=16,
            item_count=1,
            catalog_fingerprint="catalog-a",
            normalization_version="match-v1",
            created_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        ),
    )

    result = rank_candidates(
        query=MatchQuery(name="BALL BEARING", spec=None, unit="EA"),
        items=[CandidateItem(standard_item_id=1, name="BALL BEARING", unit="EA")],
        embedding_client=mock,
        embedding_index=index,
    )[0]

    assert result.embedding_status == "MOCK_ONLY"
