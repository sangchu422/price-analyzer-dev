"""
embedder.py TDD — 실행: python -m pytest tests/test_embedder.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "price_analyzer_v2"))

import pytest
from pipeline.embedder import embed, embed_batch, search, build_index


# ── embed ────────────────────────────────────────────────────────────────────

def test_embed_returns_3072_dimensional_vector():
    vec = embed("CONTROL PANEL")
    assert isinstance(vec, list)
    assert len(vec) == 3072
    assert all(isinstance(v, float) for v in vec)


def test_embed_empty_string_raises():
    with pytest.raises(ValueError):
        embed("")


# ── embed_batch ──────────────────────────────────────────────────────────────

def test_embed_batch_returns_same_count_as_input():
    texts = ["서보모터", "감속기 1/20", "LM 가이드"]
    vecs = embed_batch(texts)
    assert len(vecs) == 3
    assert all(len(v) == 3072 for v in vecs)


def test_embed_batch_empty_list_raises():
    with pytest.raises(ValueError):
        embed_batch([])


# ── build_index / search ─────────────────────────────────────────────────────

@pytest.fixture
def small_index():
    items = [
        {"표준품목ID": "STD-0001", "품명": "서보모터", "규격": "750W", "단가_평균": 500000},
        {"표준품목ID": "STD-0002", "품명": "감속기", "규격": "1/20", "단가_평균": 300000},
        {"표준품목ID": "STD-0003", "품명": "LM 가이드", "규격": "HGR20", "단가_평균": 80000},
    ]
    return build_index(items)


def test_search_returns_top_n(small_index):
    results = search("서보모터 750W", small_index, top_n=2)
    assert len(results) == 2


def test_search_result_has_required_fields(small_index):
    results = search("서보모터", small_index, top_n=1)
    r = results[0]
    assert "표준품목ID" in r
    assert "품명" in r
    assert "score" in r
    assert 0.0 <= r["score"] <= 1.0


def test_search_most_relevant_is_first(small_index):
    results = search("서보모터 750W", small_index, top_n=3)
    assert results[0]["표준품목ID"] == "STD-0001"
