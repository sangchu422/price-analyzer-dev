"""
hChat 임베딩 어댑터 + 유사도 검색

공개 인터페이스:
  embed(text)            -> List[float]          단일 텍스트 → 3072차원 벡터
  embed_batch(texts)     -> List[List[float]]    배치 처리
  build_index(items)     -> dict                 표준단가 항목 리스트 → 검색 인덱스
  save_index(index, dir) -> None                 인덱스 저장 (vectors.npy + items.json)
  load_index(dir)        -> dict                 인덱스 로드
  search(query, index)   -> List[dict]           코사인 유사도 검색

저장 포맷 (price_analyzer_v2/):
  embed_vectors.npy  — float32 numpy 배열 (1852, 3072) ~22MB
  embed_items.json   — 원본 항목 메타데이터 리스트 ~638KB
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import H_CHAT_BASE_URL, H_CHAT_API_KEY, H_CHAT_PROJECT_ID, EMBED_MODEL


def _client():
    from openai import AzureOpenAI
    headers = {"X-Project-Id": H_CHAT_PROJECT_ID} if H_CHAT_PROJECT_ID else None
    return AzureOpenAI(
        azure_endpoint=H_CHAT_BASE_URL,
        api_key=H_CHAT_API_KEY,
        api_version="2024-10-21",
        default_headers=headers,
    )


def embed(text: str) -> list[float]:
    if not text or not text.strip():
        raise ValueError("text must not be empty")
    resp = _client().embeddings.create(input=[text.strip()], model=EMBED_MODEL)
    return resp.data[0].embedding


def embed_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        raise ValueError("texts must not be empty")
    resp = _client().embeddings.create(input=[t.strip() for t in texts], model=EMBED_MODEL)
    return [d.embedding for d in resp.data]


def _search_text(item: dict) -> str:
    text = f"{item.get('품명', '')} {item.get('규격', '')}".strip()
    return text if text else item.get("표준품목ID", "unknown")


def build_index(items: list[dict]) -> dict[str, Any]:
    texts = [_search_text(i) for i in items]
    vectors = embed_batch(texts)
    return {"vectors": vectors, "items": items}


def save_index(index: dict, directory: "str | Path" = None) -> None:
    import json, numpy as np
    from pathlib import Path
    from config import EMBED_INDEX_PATH
    d = Path(directory) if directory else EMBED_INDEX_PATH.parent
    np.save(d / "embed_vectors.npy", np.array(index["vectors"], dtype=np.float32))
    with open(d / "embed_items.json", "w", encoding="utf-8") as f:
        json.dump(index["items"], f, ensure_ascii=False)


def load_index(directory: "str | Path" = None) -> dict:
    import json, numpy as np
    from pathlib import Path
    from config import EMBED_INDEX_PATH
    d = Path(directory) if directory else EMBED_INDEX_PATH.parent
    vectors = np.load(d / "embed_vectors.npy").tolist()
    with open(d / "embed_items.json", encoding="utf-8") as f:
        items = json.load(f)
    return {"vectors": vectors, "items": items}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def search(query: str, index: dict, top_n: int = 5) -> list[dict]:
    q_vec = embed(query)
    scored = [
        {**item, "score": _cosine(q_vec, vec)}
        for item, vec in zip(index["items"], index["vectors"])
    ]
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_n]
