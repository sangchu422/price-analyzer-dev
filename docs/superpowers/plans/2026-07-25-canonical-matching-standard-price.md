# Canonical Matching and Standard Price Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Group normalized quote expressions into reviewable canonical items, connect the hChat embedding adapter safely, and generate versioned internal standard prices from approved observations.

**Architecture:** Matching is a four-stage service: normalized exact match, approved alias match, RapidFuzz candidate search, then optional hChat vector search. Unit/spec compatibility is a mandatory gate, and embeddings alone never produce an automatic match. Approved `INCLUDED` decisions become price observations; recalculation appends a standard-price version and preserves all prior versions.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, SQLite, RapidFuzz, NumPy, OpenAI-compatible Azure client, pytest, React, TypeScript, TanStack Query.

---

## File map

- `backend/app/catalog/models.py`: canonical items and approved aliases.
- `backend/app/matching/models.py`: candidates, decisions, and score evidence.
- `backend/app/pricing/models.py`: observations and standard-price versions.
- `backend/app/matching/normalize.py`: deterministic item/spec/unit normalization.
- `backend/app/matching/compatibility.py`: unit and spec gates.
- `backend/app/matching/keyword.py`: exact, alias, and fuzzy candidates.
- `backend/app/embeddings/provider.py`: embedding provider protocol.
- `backend/app/embeddings/hchat.py`: hChat adapter.
- `backend/app/embeddings/index.py`: NumPy index plus metadata validation.
- `backend/app/matching/service.py`: `MATCHED`/`CANDIDATE`/`NO_MATCH` decision.
- `backend/app/pricing/service.py`: observation projection and append-only standard-price calculation.
- `backend/app/api/matching.py`: matching and review APIs.
- `backend/app/api/pricing.py`: standard-price history APIs.
- `frontend/src/pages/GroupingReviewPage.tsx`: candidate review.
- `frontend/src/pages/StandardPricePage.tsx`: price range, evidence, and version history.

### Task 1: Add canonical, matching, and price-version tables

**Files:**
- Create: `backend/app/catalog/models.py`
- Create: `backend/app/matching/models.py`
- Create: `backend/app/pricing/models.py`
- Create: `backend/alembic/versions/0002_catalog_matching_pricing.py`
- Create: `backend/tests/catalog/test_models.py`

- [ ] **Step 1: Write the failing relationship test**

```python
def test_canonical_item_keeps_alias_match_and_price_history(session):
    item = CanonicalItem(
        name="SERVO MOTOR",
        spec="750W",
        unit="EA",
        category="전기",
    )
    item.aliases.append(ItemAlias(alias_text="서보모터 0.75KW", approved_by="sangwoo"))
    item.price_versions.append(
        StandardPriceVersion(
            price_min=480000,
            price_median=500000,
            price_avg=510000,
            price_max=550000,
            observation_count=4,
            vendor_count=3,
            rule_version="price-v1",
            change_reason="초기 산출",
        )
    )
    session.add(item)
    session.commit()
    assert item.aliases[0].alias_text == "서보모터 0.75KW"
    assert item.price_versions[0].price_median == 500000
```

- [ ] **Step 2: Verify failure**

Run: `cd backend && python -m pytest tests/catalog/test_models.py -q`

Expected: FAIL importing catalog models.

- [ ] **Step 3: Implement focused SQLAlchemy models**

```python
# backend/app/catalog/models.py
class CanonicalItem(Base):
    __tablename__ = "canonical_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, index=True)
    spec: Mapped[str] = mapped_column(String, default="")
    unit: Mapped[str] = mapped_column(String, default="")
    category: Mapped[str] = mapped_column(String, default="")
    maker_constraint: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    aliases: Mapped[list["ItemAlias"]] = relationship(back_populates="canonical_item")
    price_versions: Mapped[list["StandardPriceVersion"]] = relationship(
        back_populates="canonical_item",
        order_by="StandardPriceVersion.valid_from",
    )


class ItemAlias(Base):
    __tablename__ = "item_alias"
    __table_args__ = (UniqueConstraint("alias_text", "canonical_item_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_item_id: Mapped[int] = mapped_column(ForeignKey("canonical_item.id"))
    alias_text: Mapped[str] = mapped_column(String, index=True)
    alias_type: Mapped[str] = mapped_column(String, default="APPROVED")
    approved_by: Mapped[str] = mapped_column(String)
    approved_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    canonical_item: Mapped[CanonicalItem] = relationship(back_populates="aliases")
```

```python
# backend/app/matching/models.py
class MatchDisposition(StrEnum):
    MATCHED = "MATCHED"
    CANDIDATE = "CANDIDATE"
    NO_MATCH = "NO_MATCH"


class GroupingCandidate(Base):
    __tablename__ = "grouping_candidate"
    id: Mapped[int] = mapped_column(primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(ForeignKey("raw_quote_item.id"))
    canonical_item_id: Mapped[int | None] = mapped_column(ForeignKey("canonical_item.id"))
    exact_score: Mapped[float] = mapped_column(default=0)
    fuzzy_score: Mapped[float] = mapped_column(default=0)
    embedding_score: Mapped[float | None]
    unit_compatible: Mapped[bool]
    spec_compatible: Mapped[bool]
    disposition: Mapped[MatchDisposition] = mapped_column(Enum(MatchDisposition))
    rules_version: Mapped[str] = mapped_column(String)
    decided_by: Mapped[str] = mapped_column(String, default="SYSTEM")
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

```python
# backend/app/pricing/models.py
class PriceObservation(Base):
    __tablename__ = "price_observation"
    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_item_id: Mapped[int] = mapped_column(ForeignKey("canonical_item.id"))
    clean_decision_id: Mapped[int] = mapped_column(ForeignKey("clean_decision.id"), unique=True)
    unit_price: Mapped[float]
    quantity: Mapped[float | None]
    unit: Mapped[str]
    vendor: Mapped[str | None]
    quote_date: Mapped[date | None]
    included: Mapped[bool] = mapped_column(default=True)


class StandardPriceVersion(Base):
    __tablename__ = "standard_price_version"
    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_item_id: Mapped[int] = mapped_column(ForeignKey("canonical_item.id"))
    price_min: Mapped[float]
    price_median: Mapped[float]
    price_avg: Mapped[float]
    price_max: Mapped[float]
    observation_count: Mapped[int]
    vendor_count: Mapped[int]
    latest_quote_date: Mapped[date | None]
    rule_version: Mapped[str]
    valid_from: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    change_reason: Mapped[str]
    canonical_item: Mapped["CanonicalItem"] = relationship(back_populates="price_versions")
```

- [ ] **Step 4: Add migration and run tests**

Run: `cd backend && alembic upgrade head && python -m pytest tests/catalog/test_models.py -q`

Expected: migration `0002` applies and the relationship test passes.

- [ ] **Step 5: Commit**

```bash
git add backend/app/catalog backend/app/matching backend/app/pricing backend/alembic backend/tests/catalog
git commit -m "feat: add canonical matching and standard price schema"
```

### Task 2: Normalize names, specifications, and units

**Files:**
- Create: `backend/app/matching/normalize.py`
- Create: `backend/app/matching/compatibility.py`
- Create: `backend/tests/matching/test_normalize.py`
- Create: `backend/tests/matching/test_compatibility.py`

- [ ] **Step 1: Write failing normalization tests**

```python
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  Servo   Motor ", "SERVO MOTOR"),
        ("LM-GUIDE(HGR20)", "LM GUIDE (HGR20)"),
        ("0.75 kW", "750W"),
    ],
)
def test_normalize_search_text(value, expected):
    assert normalize_text(value) == expected


def test_unit_alias():
    assert normalize_unit("개") == "EA"
    assert normalize_unit("pcs") == "EA"
    assert normalize_unit("SET") == "SET"
```

- [ ] **Step 2: Implement deterministic normalization**

```python
# backend/app/matching/normalize.py
import re

UNIT_ALIASES = {
    "개": "EA",
    "PCS": "EA",
    "PC": "EA",
    "EA": "EA",
    "세트": "SET",
    "SET": "SET",
    "M": "M",
    "KG": "KG",
}


def normalize_text(value: str | None) -> str:
    text = (value or "").upper().strip()
    text = re.sub(r"(?<!\d)0\.75\s*KW\b", "750W", text)
    text = re.sub(r"[-_/]+", " ", text)
    text = re.sub(r"\s*\(\s*", " (", text)
    text = re.sub(r"\s*\)\s*", ") ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_unit(value: str | None) -> str:
    key = normalize_text(value)
    return UNIT_ALIASES.get(key, key)


def search_text(name: str, spec: str, maker: str = "") -> str:
    return " ".join(filter(None, (normalize_text(name), normalize_text(spec), normalize_text(maker))))
```

- [ ] **Step 3: Implement mandatory compatibility gates**

```python
# backend/app/matching/compatibility.py
MODEL_TOKEN = re.compile(r"\b[A-Z]{1,6}[- ]?\d[A-Z0-9-]*\b")
MEASURE_TOKEN = re.compile(r"\b\d+(?:\.\d+)?\s*(?:V|W|KW|MM|CM|M|KG|A)\b")


def compatible_units(left: str, right: str) -> bool:
    return not left or not right or normalize_unit(left) == normalize_unit(right)


def compatible_specs(left: str, right: str) -> bool:
    left_norm, right_norm = normalize_text(left), normalize_text(right)
    left_tokens = set(MODEL_TOKEN.findall(left_norm) + MEASURE_TOKEN.findall(left_norm))
    right_tokens = set(MODEL_TOKEN.findall(right_norm) + MEASURE_TOKEN.findall(right_norm))
    return not left_tokens or not right_tokens or left_tokens == right_tokens
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/matching/test_normalize.py tests/matching/test_compatibility.py -q`

Expected: normalization aliases pass and conflicting model/measurement tokens are rejected.

- [ ] **Step 5: Commit**

```bash
git add backend/app/matching backend/tests/matching
git commit -m "feat: normalize quote items and enforce compatibility gates"
```

### Task 3: Add exact, alias, and fuzzy candidate search

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/app/matching/keyword.py`
- Create: `backend/tests/matching/test_keyword.py`

- [ ] **Step 1: Add RapidFuzz**

Add `"rapidfuzz>=3.13,<4"` to backend runtime dependencies.

- [ ] **Step 2: Write candidate ranking tests**

```python
def test_exact_alias_is_automatic_match(catalog):
    result = keyword_candidates("서보모터 0.75KW", catalog)
    assert result[0].canonical_item_id == catalog.servo.id
    assert result[0].exact_score == 1.0


def test_fuzzy_result_is_candidate_not_match(catalog):
    result = keyword_candidates("서보 모타", catalog)
    assert result[0].fuzzy_score > 0.7
    assert result[0].automatic is False
```

- [ ] **Step 3: Implement keyword candidates**

```python
# backend/app/matching/keyword.py
from dataclasses import dataclass
from rapidfuzz.fuzz import WRatio


@dataclass(frozen=True)
class KeywordCandidate:
    canonical_item_id: int
    exact_score: float
    fuzzy_score: float
    automatic: bool


def keyword_candidates(query: str, catalog: list[CatalogEntry], limit: int = 20):
    normalized = normalize_text(query)
    ranked = []
    for entry in catalog:
        approved = {normalize_text(entry.name), *(normalize_text(a) for a in entry.aliases)}
        exact = float(normalized in approved)
        fuzzy = max(WRatio(normalized, candidate) / 100 for candidate in approved)
        ranked.append(KeywordCandidate(entry.id, exact, fuzzy, automatic=bool(exact)))
    return sorted(ranked, key=lambda row: (row.exact_score, row.fuzzy_score), reverse=True)[:limit]
```

- [ ] **Step 4: Run tests and commit**

Run: `cd backend && python -m pytest tests/matching/test_keyword.py -q`

Expected: exact/alias matches are distinguishable from fuzzy candidates.

```bash
git add backend/pyproject.toml backend/app/matching/keyword.py backend/tests/matching/test_keyword.py
git commit -m "feat: add exact alias and fuzzy catalog search"
```

### Task 4: Replace the unsafe embedding index with a validated NumPy index

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/app/embeddings/provider.py`
- Create: `backend/app/embeddings/hchat.py`
- Create: `backend/app/embeddings/index.py`
- Create: `backend/tests/embeddings/test_index.py`
- Create: `backend/tests/embeddings/test_hchat.py`

- [ ] **Step 1: Add dependencies**

Add `"numpy>=2.3,<3"` and `"openai>=1.97,<2"` to runtime dependencies.

- [ ] **Step 2: Write metadata and vectorized-search tests**

```python
def test_load_rejects_wrong_model(tmp_path):
    save_index(tmp_path, vectors=np.eye(2, dtype=np.float32), item_ids=[1, 2], metadata=metadata(model="model-a"))
    with pytest.raises(IndexMismatch, match="model"):
        load_index(tmp_path, expected=metadata(model="model-b"))


def test_search_returns_no_match_below_threshold():
    index = EmbeddingIndex(np.eye(2, dtype=np.float32), [1, 2], metadata(dimensions=2))
    assert index.search(np.array([0.1, 0.1], dtype=np.float32), threshold=0.95) == []
```

- [ ] **Step 3: Define provider and hChat adapter**

```python
# backend/app/embeddings/provider.py
from typing import Protocol
import numpy as np


class EmbeddingProvider(Protocol):
    model_name: str

    def embed(self, texts: list[str]) -> np.ndarray:
        ...
```

```python
# backend/app/embeddings/hchat.py
class HChatEmbeddingProvider:
    def __init__(self, settings: Settings, client=None):
        self.model_name = settings.embed_model
        self.client = client or AzureOpenAI(
            azure_endpoint=settings.h_chat_base_url,
            api_key=settings.h_chat_api_key,
            api_version="2024-10-21",
            default_headers={"X-Project-Id": settings.h_chat_project_id},
        )

    def embed(self, texts: list[str]) -> np.ndarray:
        cleaned = [text.strip() for text in texts if text.strip()]
        if len(cleaned) != len(texts):
            raise ValueError("embedding text must not be blank")
        response = self.client.embeddings.create(input=cleaned, model=self.model_name)
        return np.asarray([row.embedding for row in response.data], dtype=np.float32)
```

- [ ] **Step 4: Implement normalized matrix search and metadata validation**

```python
# backend/app/embeddings/index.py
@dataclass(frozen=True)
class IndexMetadata:
    model: str
    dimensions: int
    item_count: int
    catalog_sha256: str
    rules_version: str
    created_at: str


class EmbeddingIndex:
    def __init__(self, vectors: np.ndarray, item_ids: list[int], metadata: IndexMetadata):
        if vectors.ndim != 2 or vectors.shape != (metadata.item_count, metadata.dimensions):
            raise IndexMismatch("vector shape")
        if len(item_ids) != metadata.item_count:
            raise IndexMismatch("item count")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        self.vectors = vectors / np.clip(norms, 1e-12, None)
        self.item_ids = np.asarray(item_ids)
        self.metadata = metadata

    def search(self, query: np.ndarray, threshold: float, limit: int = 10):
        query = query.astype(np.float32)
        query = query / max(float(np.linalg.norm(query)), 1e-12)
        scores = self.vectors @ query
        order = np.argsort(scores)[::-1]
        return [
            (int(self.item_ids[index]), float(scores[index]))
            for index in order[:limit]
            if float(scores[index]) >= threshold
        ]
```

Persist vectors as `float32 .npy`, item IDs as JSON, and metadata as JSON. On load, compare model, dimensions, item count, catalog hash, and rules version before constructing the index. Never call `.tolist()` on the vector matrix.

- [ ] **Step 5: Mock the API client in all unit tests**

```python
def test_hchat_adapter_uses_configured_model():
    client = Mock()
    client.embeddings.create.return_value.data = [
        SimpleNamespace(embedding=[1.0, 0.0])
    ]
    provider = HChatEmbeddingProvider(settings_for_test(), client=client)
    vectors = provider.embed(["SERVO MOTOR 750W"])
    assert vectors.shape == (1, 2)
    client.embeddings.create.assert_called_once_with(
        input=["SERVO MOTOR 750W"],
        model=settings_for_test().embed_model,
    )
```

- [ ] **Step 6: Run tests and performance check**

Run: `cd backend && python -m pytest tests/embeddings -q`

Expected: no real network call occurs.

Run: `cd backend && python -m pytest tests/performance/test_vector_search.py -q`

Expected: 2,000-item local search completes under 100 ms on the development machine.

- [ ] **Step 7: Commit**

```bash
git add backend/pyproject.toml backend/app/embeddings backend/tests/embeddings backend/tests/performance
git commit -m "feat: add validated vectorized hChat embedding index"
```

### Task 5: Produce safe `MATCHED`, `CANDIDATE`, and `NO_MATCH` decisions

**Files:**
- Create: `backend/app/matching/service.py`
- Create: `backend/tests/matching/test_service.py`

- [ ] **Step 1: Write decision-boundary tests**

```python
def test_exact_alias_and_compatible_spec_is_matched(service):
    result = service.match(item("서보모터", "750W", "EA"))
    assert result.disposition == MatchDisposition.MATCHED


def test_embedding_only_is_candidate(service):
    service.embedding_results = [(7, 0.93)]
    result = service.match(item("모터 구동장치", "", "EA"))
    assert result.disposition == MatchDisposition.CANDIDATE


def test_unit_conflict_is_no_match(service):
    result = service.match(item("전선", "2.5SQ", "M"), candidate_unit="EA")
    assert result.disposition == MatchDisposition.NO_MATCH
```

- [ ] **Step 2: Implement the decision policy**

```python
MATCH_RULES_VERSION = "match-v1"


def decide_candidate(exact: float, fuzzy: float, embedding: float | None, unit_ok: bool, spec_ok: bool):
    if not unit_ok or not spec_ok:
        return MatchDisposition.NO_MATCH
    if exact == 1.0:
        return MatchDisposition.MATCHED
    if fuzzy >= 0.88 or (embedding is not None and embedding >= 0.82):
        return MatchDisposition.CANDIDATE
    return MatchDisposition.NO_MATCH
```

`MatchingService.match()` writes every evaluated candidate with all scores and gate results. An embedding outage records `embedding_score=None` and continues with exact/fuzzy results.

- [ ] **Step 3: Run tests and commit**

Run: `cd backend && python -m pytest tests/matching -q`

Expected: no Top-N result is forced into `MATCHED`.

```bash
git add backend/app/matching/service.py backend/tests/matching/test_service.py
git commit -m "feat: enforce safe hybrid item matching decisions"
```

### Task 6: Calculate observations and append-only standard-price versions

**Files:**
- Create: `backend/app/pricing/service.py`
- Create: `backend/tests/pricing/test_service.py`

- [ ] **Step 1: Write calculation and history tests**

```python
def test_one_observation_is_a_valid_standard_price():
    summary = summarize([observation(1000, vendor="A")])
    assert summary.price_min == 1000
    assert summary.price_median == 1000
    assert summary.price_avg == 1000
    assert summary.price_max == 1000
    assert summary.observation_count == 1


def test_recalculation_appends_version(session, canonical_item):
    first = recalculate(session, canonical_item.id, "초기 산출")
    add_observation(session, canonical_item.id, 1200)
    second = recalculate(session, canonical_item.id, "신규 견적 승인")
    assert second.id != first.id
    assert len(canonical_item.price_versions) == 2
```

- [ ] **Step 2: Implement price summary**

```python
# backend/app/pricing/service.py
from dataclasses import dataclass
from statistics import fmean, median


@dataclass(frozen=True)
class PriceSummary:
    price_min: float
    price_median: float
    price_avg: float
    price_max: float
    observation_count: int
    vendor_count: int


def summarize(observations: list[PriceObservation]) -> PriceSummary:
    included = [row for row in observations if row.included]
    if not included:
        raise ValueError("no included price observations")
    prices = [row.unit_price for row in included]
    vendors = {row.vendor for row in included if row.vendor}
    return PriceSummary(
        min(prices),
        median(prices),
        fmean(prices),
        max(prices),
        len(prices),
        len(vendors),
    )
```

`project_observations()` selects only the latest `CleanDecision` per raw item with status `INCLUDED` and an approved `MATCHED` canonical item. `recalculate()` appends `StandardPriceVersion`; it never updates an earlier row.

- [ ] **Step 3: Run tests and commit**

Run: `cd backend && python -m pytest tests/pricing -q`

Expected: one-record policy, included-only filtering, vendor counts, and history tests pass.

```bash
git add backend/app/pricing backend/tests/pricing
git commit -m "feat: build versioned internal standard prices"
```

### Task 7: Add matching review and price-history APIs

**Files:**
- Create: `backend/app/api/matching.py`
- Create: `backend/app/api/pricing.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/api/test_matching_api.py`
- Create: `backend/tests/api/test_pricing_api.py`

- [ ] **Step 1: Add API tests**

```python
def test_candidate_requires_explicit_approval(client, candidate):
    response = client.post(
        f"/api/matching/candidates/{candidate.id}/approve",
        json={"canonical_item_id": candidate.canonical_item_id, "reviewed_by": "sangwoo"},
    )
    assert response.status_code == 201
    assert response.json()["disposition"] == "MATCHED"


def test_standard_price_history_includes_evidence(client, canonical_item):
    response = client.get(f"/api/pricing/items/{canonical_item.id}/history")
    body = response.json()
    assert body["versions"][0]["observation_count"] >= 1
    assert body["versions"][0]["evidence"][0]["source_row"] == 12
```

- [ ] **Step 2: Implement endpoint contracts**

Provide:

- `POST /api/matching/raw-items/{id}/run`
- `GET /api/matching/candidates?disposition=CANDIDATE`
- `POST /api/matching/candidates/{id}/approve`
- `POST /api/matching/candidates/{id}/reject`
- `GET /api/pricing/items/{id}/current`
- `GET /api/pricing/items/{id}/history`
- `POST /api/pricing/items/{id}/recalculate`

Approval appends a new grouping decision and optionally creates an approved alias; rejection appends `NO_MATCH`. Recalculation requires a non-empty `change_reason`.

- [ ] **Step 3: Run API tests and commit**

Run: `cd backend && python -m pytest tests/api/test_matching_api.py tests/api/test_pricing_api.py -q`

Expected: approval, rejection, evidence, and version-history tests pass.

```bash
git add backend/app/api backend/app/main.py backend/tests/api
git commit -m "feat: expose matching review and standard price history APIs"
```

### Task 8: Add React grouping and standard-price screens

**Files:**
- Create: `frontend/src/pages/GroupingReviewPage.tsx`
- Create: `frontend/src/pages/StandardPricePage.tsx`
- Create: `frontend/src/pages/GroupingReviewPage.test.tsx`
- Create: `frontend/src/pages/StandardPricePage.test.tsx`

- [ ] **Step 1: Write UI tests**

```tsx
it("does not present an embedding-only candidate as confirmed", async () => {
  render(<GroupingReviewPage />);
  expect(await screen.findByText("검토 후보")).toBeVisible();
  expect(screen.queryByText("자동 매칭 완료")).not.toBeInTheDocument();
});

it("shows all standard price statistics and evidence count", async () => {
  render(<StandardPricePage itemId={1} />);
  expect(await screen.findByText("최저")).toBeVisible();
  expect(screen.getByText("중앙")).toBeVisible();
  expect(screen.getByText("평균")).toBeVisible();
  expect(screen.getByText("최고")).toBeVisible();
  expect(screen.getByText("근거 4건")).toBeVisible();
});
```

- [ ] **Step 2: Implement review and history views**

The grouping screen renders exact, fuzzy, and embedding scores separately, shows unit/spec compatibility, and provides approve/reject actions. The standard-price screen renders min/median/avg/max, observation count, vendor count, latest quote date, version selector, and source links.

```tsx
<dl className="score-grid">
  <dt>문자열</dt><dd>{percent(candidate.fuzzy_score)}</dd>
  <dt>의미</dt><dd>{candidate.embedding_score == null ? "사용 불가" : percent(candidate.embedding_score)}</dd>
  <dt>단위</dt><dd>{candidate.unit_compatible ? "호환" : "충돌"}</dd>
  <dt>규격</dt><dd>{candidate.spec_compatible ? "호환" : "충돌"}</dd>
</dl>
```

- [ ] **Step 3: Run frontend tests and build**

Run: `cd frontend && npm test -- --run && npm run build`

Expected: review safety and price-history tests pass; production build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src
git commit -m "feat: add grouping review and standard price history UI"
```

### Task 9: Rebuild the catalog and embedding index from approved data

**Files:**
- Modify: `backend/app/cli.py`
- Create: `backend/tests/integration/test_matching_pipeline.py`

- [ ] **Step 1: Add CLI commands**

```python
subcommands = {
    "build-catalog": build_catalog_from_approved_groups,
    "build-embedding-index": build_embedding_index,
    "recalculate-standard-prices": recalculate_all_standard_prices,
}
```

`build-embedding-index` refuses to run without hChat credentials, writes vectors/items/metadata atomically through temporary files, then renames them after validation.

- [ ] **Step 2: Add end-to-end integration test**

```python
def test_approved_quote_to_standard_price(session, seeded_clean_item, fake_embeddings):
    candidate = run_match(session, seeded_clean_item.id, fake_embeddings)
    approve(session, candidate.id, reviewed_by="sangwoo")
    project_observations(session)
    version = recalculate(session, candidate.canonical_item_id, "통합 테스트")
    assert version.observation_count == 1
    assert version.price_median == seeded_clean_item.latest_decision.unit_price
```

- [ ] **Step 3: Run full verification**

Run: `cd backend && python -m pytest -q`

Expected: all tests pass without a live API key.

Run: `cd frontend && npm test -- --run && npm run build`

Expected: all frontend tests and build pass.

- [ ] **Step 4: Commit**

```bash
git add backend frontend
git commit -m "feat: connect approved grouping to standard price versions"
```

## Plan self-review

- Spec coverage: normalization, aliases, fuzzy search, hChat adapter, index metadata, matrix search, compatibility gates, three-way disposition, one-record standard prices, evidence, and append-only history are covered.
- Deferred to the third plan: market products, market cache, Mouser, DeviceMart, and market evidence.
- Placeholder scan: all behavior-changing steps include concrete contracts, tests, commands, and expected outcomes.
- Type consistency: `CanonicalItem`, `GroupingCandidate`, `MatchDisposition`, `PriceObservation`, and `StandardPriceVersion` are used consistently.
