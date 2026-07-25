# Standard Item and Price Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the cleansed historical quote corpus into human-approved standard-item groups, versioned standard prices, and an internal-price comparison workflow for new quotes.

**Architecture:** Keep every source row and decision append-only. Deterministic normalization, fuzzy search, and an optional hChat embedding adapter produce candidates only; a human decision creates current group membership. Standard-price versions use only rows whose latest cleansing decision is `INCLUDED` and whose latest membership is approved. The existing cleansing review page remains available while new grouping and price-analysis pages share the FastAPI/SQLite backend.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, SQLite, NumPy, RapidFuzz, httpx, React 19, TypeScript, TanStack Query, Vitest.

---

## Scope boundary

This plan includes internal historical-quote matching and standard-price analysis. It does not call DeviceMart, Mouser, or hChat from the home/local environment. DeviceMart/Mouser cache-first market-price collection is the next independent plan. The hChat implementation in this plan is a disabled-by-default adapter boundary plus an OpenAI-compatible contract option and deterministic local mock; the office-side sample contract can replace the request/response codec without changing matching services.

### Task 1: Add append-only standard-item, document-metadata, and price-version models

**Files:**
- Create: `backend/app/catalog/models.py`
- Create: `backend/app/catalog/__init__.py`
- Create: `backend/alembic/versions/0004_standard_item_price_models.py`
- Modify: `backend/app/db/models.py`
- Modify: `backend/app/db/immutability.py`
- Test: `backend/tests/catalog/test_catalog_models.py`
- Test: `backend/tests/test_source_models.py`

- [ ] **Step 1: Write failing model and migration tests**

```python
def test_membership_and_price_history_are_append_only(session, raw_item):
    item = StandardItem()
    version = StandardItemVersion(
        standard_item=item,
        version_number=1,
        canonical_name="BEARING",
        canonical_spec="6204 ZZ",
        canonical_unit="EA",
        created_by="sangwoo",
    )
    membership = ItemMembershipDecision(
        raw_item=raw_item,
        standard_item=item,
        status=MembershipStatus.MATCHED,
        decided_by="sangwoo",
        method="MANUAL",
        evidence_json="{}",
    )
    session.add_all([version, membership])
    session.commit()

    membership.status = MembershipStatus.REJECTED
    with pytest.raises(ImmutableRecordError):
        session.flush()


def test_migration_round_trip_preserves_populated_source_rows(
    migrated_database,
):
    upgrade("head")
    assert table_names() >= {
        "standard_item",
        "standard_item_version",
        "document_metadata_version",
        "item_membership_decision",
        "standard_price_version",
    }
    downgrade("0003")
    assert source_row_count() == 1
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
cd backend
python -m pytest tests/catalog/test_catalog_models.py tests/test_source_models.py -q
```

Expected: collection fails because `app.catalog.models` and migration `0004` do not exist.

- [ ] **Step 3: Implement the catalog models**

Use these contracts:

```python
class MembershipStatus(StrEnum):
    MATCHED = "MATCHED"
    REJECTED = "REJECTED"


class StandardItem(Base):
    __tablename__ = "standard_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class StandardItemVersion(Base):
    __tablename__ = "standard_item_version"
    __table_args__ = (
        UniqueConstraint("standard_item_id", "version_number"),
        CheckConstraint("version_number > 0"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    standard_item_id: Mapped[int] = mapped_column(
        ForeignKey("standard_item.id", ondelete="RESTRICT"),
        index=True,
    )
    version_number: Mapped[int]
    canonical_name: Mapped[str]
    canonical_spec: Mapped[str | None]
    canonical_unit: Mapped[str | None]
    aliases_json: Mapped[str] = mapped_column(default="[]")
    created_by: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class DocumentMetadataVersion(Base):
    __tablename__ = "document_metadata_version"
    __table_args__ = (
        UniqueConstraint("source_document_id", "version_number"),
        CheckConstraint("version_number > 0"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("source_document.id", ondelete="RESTRICT"),
        index=True,
    )
    version_number: Mapped[int]
    supplier_name: Mapped[str | None]
    quote_date: Mapped[date | None]
    project_name: Mapped[str | None]
    decided_by: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class ItemMembershipDecision(Base):
    __tablename__ = "item_membership_decision"
    id: Mapped[int] = mapped_column(primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_quote_item.id", ondelete="RESTRICT"),
        index=True,
    )
    standard_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("standard_item.id", ondelete="RESTRICT"),
        index=True,
    )
    status: Mapped[MembershipStatus]
    candidate_score: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    method: Mapped[str]
    evidence_json: Mapped[str]
    supersedes_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("item_membership_decision.id", ondelete="RESTRICT"),
        unique=True,
    )
    decided_by: Mapped[str]
    decided_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)


class StandardPriceVersion(Base):
    __tablename__ = "standard_price_version"
    __table_args__ = (
        UniqueConstraint("standard_item_id", "version_number"),
        CheckConstraint("observation_count > 0"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    standard_item_id: Mapped[int] = mapped_column(
        ForeignKey("standard_item.id", ondelete="RESTRICT"),
        index=True,
    )
    version_number: Mapped[int]
    observation_count: Mapped[int]
    supplier_count: Mapped[int]
    latest_quote_date: Mapped[date | None]
    minimum_price: Mapped[Decimal] = mapped_column(ExactDecimal())
    median_price: Mapped[Decimal] = mapped_column(ExactDecimal())
    average_price: Mapped[Decimal] = mapped_column(ExactDecimal())
    maximum_price: Mapped[Decimal] = mapped_column(ExactDecimal())
    observation_decision_ids_json: Mapped[str]
    calculation_version: Mapped[str]
    approved_by: Mapped[str]
    approved_at: Mapped[datetime] = mapped_column(UtcDateTime(), default=utc_now)
```

Register all models in `app/db/models.py` and all history tables with the existing ORM and bulk-DML immutability guards. `StandardItem` is a stable identity row; its descriptive state exists only in append-only `StandardItemVersion`.

- [ ] **Step 4: Add Alembic `0004`**

Create all five tables, foreign keys, uniqueness checks, status checks, and lookup indexes. Downgrade must refuse before DDL if a later table outside revision `0004` depends on these tables; otherwise remove them in reverse dependency order without touching source, raw, or cleansing tables.

- [ ] **Step 5: Run focused and migration tests**

Run:

```powershell
cd backend
python -m pytest tests/catalog/test_catalog_models.py tests/test_source_models.py -q
python -m alembic upgrade head
python -m alembic check
```

Expected: all tests pass and Alembic reports no pending model operations.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/catalog backend/app/db backend/alembic backend/tests
git commit -m "feat: add standard item and price history models"
```

### Task 2: Implement deterministic standard-item candidate search

**Files:**
- Create: `backend/app/matching/normalization.py`
- Create: `backend/app/matching/candidates.py`
- Create: `backend/app/matching/__init__.py`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/matching/test_normalization.py`
- Test: `backend/tests/matching/test_candidates.py`

- [ ] **Step 1: Write failing normalization tests**

```python
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  Servo   Motor ", "SERVO MOTOR"),
        ("BEARING（6204-ZZ）", "BEARING (6204 ZZ)"),
        ("AC-MOTOR_400W", "AC MOTOR 400W"),
    ],
)
def test_normalize_search_text(value, expected):
    assert normalize_search_text(value) == expected


def test_model_tokens_are_preserved():
    assert model_tokens("SERVO MOTOR SGMAH-04AAA61 400W") == (
        "400W",
        "SGMAH-04AAA61",
    )
```

- [ ] **Step 2: Write failing compatibility and ranking tests**

```python
def test_unit_conflict_blocks_candidate():
    candidate = make_item(name="BEARING", spec="6204", unit="M")
    result = rank_candidates(
        query=MatchQuery(name="BEARING", spec="6204", unit="EA"),
        items=[candidate],
    )
    assert result == []


def test_model_number_match_ranks_before_name_only_match():
    results = rank_candidates(
        query=MatchQuery(
            name="SERVO MOTOR",
            spec="SGMAH-04AAA61 400W",
            unit="EA",
        ),
        items=[
            make_item(name="SERVO MOTOR", spec="OTHER 400W", unit="EA"),
            make_item(name="AC SERVO", spec="SGMAH-04AAA61", unit="EA"),
        ],
    )
    assert results[0].matched_tokens == ("SGMAH-04AAA61",)
```

- [ ] **Step 3: Run tests and verify failure**

Run:

```powershell
cd backend
python -m pytest tests/matching -q
```

Expected: imports fail because the matching package is absent.

- [ ] **Step 4: Implement normalization and deterministic ranking**

`normalize_search_text` must apply Unicode NFKC, trim/collapse whitespace, normalize punctuation without removing alphanumeric model separators inside tokens, and uppercase Latin text. Implement:

```python
@dataclass(frozen=True)
class MatchQuery:
    name: str
    spec: str | None
    unit: str | None


@dataclass(frozen=True)
class CandidateScore:
    standard_item_id: int
    name_score: Decimal
    spec_score: Decimal
    token_score: Decimal
    embedding_score: Decimal | None
    final_score: Decimal
    matched_tokens: tuple[str, ...]
    method: str
```

Use `rapidfuzz.fuzz.WRatio` for name/spec lexical scores. Apply these deterministic gates before ranking:

- incompatible non-empty normalized units: reject;
- conflicting explicit model tokens: reject;
- exact normalized name+spec+unit: final score `1.000000`;
- exact model token plus compatible unit: minimum final score `0.900000`;
- lexical-only candidate below `0.650000`: omit.

Keep weights as named `Decimal` constants and return at most the requested `top_n`, ordered by final score descending and `standard_item_id` ascending.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
cd backend
python -m pytest tests/matching -q
```

Expected: normalization, incompatibility gates, stable ranking, ties, empty specs, and Korean/Latin mixed names pass.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/matching backend/tests/matching backend/pyproject.toml
git commit -m "feat: rank compatible standard item candidates"
```

### Task 3: Add the disabled-by-default hChat embedding adapter and local fallback

**Files:**
- Create: `backend/app/embeddings/base.py`
- Create: `backend/app/embeddings/hchat.py`
- Create: `backend/app/embeddings/mock.py`
- Create: `backend/app/embeddings/index.py`
- Create: `backend/app/embeddings/__init__.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/embeddings/test_hchat_adapter.py`
- Test: `backend/tests/embeddings/test_index.py`

- [ ] **Step 1: Write failing adapter tests**

```python
def test_hchat_is_disabled_without_explicit_configuration():
    client = build_embedding_client(Settings(hchat_embedding_enabled=False))
    with pytest.raises(EmbeddingUnavailableError):
        client.embed(["SERVO MOTOR"])


def test_openai_compatible_codec_uses_configured_key(httpx_mock):
    httpx_mock.add_response(
        json={"data": [{"index": 0, "embedding": [0.0, 1.0]}]},
    )
    client = HChatEmbeddingClient(
        endpoint="https://intranet.invalid/embeddings",
        api_key="office-key",
        model="office-model",
        api_style="openai",
        transport=httpx.Client(
            transport=httpx_mock.transport,
            timeout=1,
        ),
    )
    assert client.embed(["BEARING"]).vectors.shape == (1, 2)
    assert httpx_mock.get_request().headers["Authorization"] == (
        "Bearer office-key"
    )
```

No test may access a real network interface.

- [ ] **Step 2: Write failing index fingerprint tests**

```python
def test_index_rejects_model_or_dimension_mismatch(tmp_path):
    save_index(
        tmp_path / "items.npz",
        item_ids=np.array([1, 2]),
        vectors=np.eye(2, dtype=np.float32),
        metadata=IndexMetadata(
            model="mock-v1",
            dimension=2,
            item_count=2,
            catalog_fingerprint="catalog-a",
            normalization_version="match-v1",
        ),
    )
    with pytest.raises(IndexMismatchError):
        load_index(
            tmp_path / "items.npz",
            expected_model="office-model",
            expected_catalog_fingerprint="catalog-a",
        )
```

- [ ] **Step 3: Implement adapter contracts**

```python
@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: np.ndarray
    model: str
    dimension: int


class EmbeddingClient(Protocol):
    def embed(self, texts: Sequence[str]) -> EmbeddingBatch: ...
```

Configuration:

```python
hchat_embedding_enabled: bool = False
hchat_embedding_endpoint: str | None = None
hchat_embedding_api_key: SecretStr | None = None
hchat_embedding_model: str | None = None
hchat_embedding_api_style: Literal["openai", "custom"] = "custom"
hchat_embedding_timeout_seconds: float = 10.0
embedding_index_file: Path = Path("backend/.local/standard-items.npz")
```

`api_style="custom"` must raise `EmbeddingContractNotConfiguredError` with a message that directs the office implementer to update only `_build_payload` and `_parse_response` after receiving the hChat sample. `api_style="openai"` may issue HTTP only when `enabled=True` and all settings exist. Never log or serialize the API key.

- [ ] **Step 4: Implement deterministic mock and NumPy index**

`DeterministicMockEmbeddingClient` hashes normalized character trigrams into a fixed-size `float32` vector and L2-normalizes it. It is test/development-only and must identify its model as `local-mock-v1`; production matching labels mock results as unavailable for automatic approval.

Save `.npz` atomically with:

- `item_ids`;
- 2-D normalized `float32` vectors;
- JSON metadata containing model, dimension, item count, catalog fingerprint, normalization version, and creation time.

Reject NaN/Inf vectors, duplicates, dimension mismatch, model mismatch, item-count mismatch, and catalog-fingerprint mismatch before search.

- [ ] **Step 5: Integrate optional embedding scores into candidate ranking**

If the adapter or index is unavailable, return deterministic lexical candidates with `embedding_score=None` and an `embedding_status` value. Embedding similarity may raise a compatible lexical candidate's score but may never bypass unit or model-token conflict gates and may never create `MATCHED` automatically.

- [ ] **Step 6: Run tests**

Run:

```powershell
cd backend
python -m pytest tests/embeddings tests/matching -q
```

Expected: no network access, disabled/custom/openai-compatible paths, fallback, atomic index, and mismatch detection pass.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/embeddings backend/app/core/config.py backend/app/matching backend/tests backend/pyproject.toml
git commit -m "feat: add pluggable hChat embedding boundary"
```

### Task 4: Add human-approved grouping and metadata APIs

**Files:**
- Create: `backend/app/catalog/service.py`
- Create: `backend/app/api/catalog.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/catalog/test_catalog_service.py`
- Test: `backend/tests/api/test_catalog_api.py`

- [ ] **Step 1: Write failing candidate and approval API tests**

```python
def test_candidate_api_never_auto_matches(client, included_raw_item):
    response = client.get(
        f"/api/catalog/raw-items/{included_raw_item.id}/candidates"
    )
    assert response.status_code == 200
    assert response.json()["match_status"] == "CANDIDATE"
    assert response.json()["candidates"]
    assert current_membership_count() == 0


def test_match_approval_uses_optimistic_concurrency(
    client,
    included_raw_item,
    standard_item,
):
    body = {
        "standard_item_id": standard_item.id,
        "status": "MATCHED",
        "expected_current_decision_id": None,
        "candidate_score": "0.920000",
        "method": "MANUAL_CANDIDATE",
        "evidence": {"matched_tokens": ["6204"]},
        "decided_by": "buyer-1",
    }
    assert client.post(
        f"/api/catalog/raw-items/{included_raw_item.id}/memberships",
        json=body,
    ).status_code == 201
    assert client.post(
        f"/api/catalog/raw-items/{included_raw_item.id}/memberships",
        json=body,
    ).status_code == 409
```

- [ ] **Step 2: Write failing metadata and standard-item version tests**

```python
def test_metadata_and_item_edits_append_versions(client, source_document):
    first_body = {
        "supplier_name": "SUPPLIER A",
        "quote_date": "2026-07-01",
        "project_name": "PUNE LINE",
        "expected_current_version_id": None,
        "decided_by": "buyer-1",
    }
    first = client.post(
        f"/api/catalog/documents/{source_document.id}/metadata",
        json=first_body,
    )
    second = client.post(
        f"/api/catalog/documents/{source_document.id}/metadata",
        json={
            **first_body,
            "supplier_name": "SUPPLIER A CO.",
            "expected_current_version_id": first.json()["id"],
        },
    )
    assert second.json()["version_number"] == 2
    assert metadata_history_count() == 2
```

- [ ] **Step 3: Implement catalog service projections**

Implement latest-by-ID projections for:

- current standard-item version;
- current document metadata version;
- current raw-item membership;
- unmatched included raw items;
- group member observations.

All mutating service functions use caller-owned transactions, append new rows, and use SQLite `BEGIN IMMEDIATE` plus `expected_current_*_id` for manual concurrent decisions.

- [ ] **Step 4: Implement API endpoints**

Provide typed Pydantic contracts:

- `GET /api/catalog/unmatched?after_id=&limit=&search=`;
- `GET /api/catalog/raw-items/{id}/candidates?top_n=`;
- `POST /api/catalog/standard-items`;
- `POST /api/catalog/standard-items/{id}/versions`;
- `POST /api/catalog/raw-items/{id}/memberships`;
- `GET /api/catalog/standard-items/{id}/members`;
- `POST /api/catalog/documents/{id}/metadata`.

Candidate responses include deterministic component scores, compatibility gates, embedding status/model, exact source provenance, and the current cleansing decision. Stale writes return `409 STALE_CATALOG_DECISION`. Invalid or currently excluded raw items return `409 RAW_ITEM_NOT_INCLUDED`.

- [ ] **Step 5: Run focused and full API tests**

Run:

```powershell
cd backend
python -m pytest tests/catalog tests/api/test_catalog_api.py -q
python -m pytest -q
```

Expected: append-only histories, current projections, concurrency conflicts, source evidence, and no automatic merge pass.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/catalog backend/app/api/catalog.py backend/app/main.py backend/tests
git commit -m "feat: expose human approved item grouping"
```

### Task 5: Calculate and approve versioned standard prices

**Files:**
- Create: `backend/app/pricing/service.py`
- Create: `backend/app/pricing/__init__.py`
- Create: `backend/app/api/pricing.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/pricing/test_standard_prices.py`
- Test: `backend/tests/api/test_pricing_api.py`

- [ ] **Step 1: Write failing calculation tests**

```python
def test_price_draft_uses_only_latest_included_matched_rows(
    session,
    standard_item,
    priced_members,
):
    draft = calculate_standard_price(session, standard_item.id)
    assert draft.observation_count == 3
    assert draft.prices == PriceStatistics(
        minimum=Decimal("100"),
        median=Decimal("120"),
        average=Decimal("140"),
        maximum=Decimal("200"),
    )
    assert draft.supplier_count == 2
    assert draft.latest_quote_date == date(2026, 7, 20)


def test_later_exclusion_removes_observation_without_deleting_old_version(
    session,
    approved_standard_price,
    excluded_later,
):
    draft = calculate_standard_price(
        session,
        approved_standard_price.standard_item_id,
    )
    assert excluded_later.clean_decision_id not in draft.decision_ids
    assert session.get(
        StandardPriceVersion,
        approved_standard_price.id,
    ) is not None
```

- [ ] **Step 2: Implement exact statistics**

Use sorted `Decimal` observations. For an even count, median is the exact mean of the two middle values. Average uses exact sum/count and is quantized to six decimal places with `ROUND_HALF_UP` only at persistence. Include:

- latest cleansing decision must be `INCLUDED`;
- latest membership must be `MATCHED` to the target;
- unit must remain compatible with the current standard-item version;
- unit price must be positive and storage-safe;
- each contributing cleansing-decision ID is stored in sorted order;
- supplier count ignores missing metadata instead of inventing a supplier;
- latest quote date ignores missing dates.

Return a deterministic catalog fingerprint over current item version, member decision IDs, and cleansing decision IDs.

- [ ] **Step 3: Write approval API tests**

```python
def test_approve_price_requires_unchanged_draft_fingerprint(
    client,
    price_draft,
):
    response = client.post(
        f"/api/pricing/standard-items/{price_draft.item_id}/versions",
        json={
            "expected_fingerprint": price_draft.fingerprint,
            "approved_by": "buyer-1",
        },
    )
    assert response.status_code == 201
    assert response.json()["version_number"] == 1


def test_changed_member_set_returns_conflict(client, stale_price_draft):
    response = client.post(
        f"/api/pricing/standard-items/{stale_price_draft.item_id}/versions",
        json={
            "expected_fingerprint": stale_price_draft.fingerprint,
            "approved_by": "buyer-1",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "PRICE_DRAFT_CHANGED"
```

- [ ] **Step 4: Implement pricing API**

Provide:

- `GET /api/pricing/standard-items/{id}/draft`;
- `GET /api/pricing/standard-items/{id}/versions`;
- `POST /api/pricing/standard-items/{id}/versions`.

Responses include min/median/average/max as decimal strings, observation count, supplier count, latest quote date, contributing relative source references, excluded/review-required counts for context, calculation version, and fingerprint. Approval appends a version and never updates old versions.

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
cd backend
python -m pytest tests/pricing tests/api/test_pricing_api.py -q
python -m pytest -q
```

Expected: exact arithmetic, current-decision filtering, stale draft conflicts, one-observation groups, missing metadata, and history pass.

Commit:

```powershell
git add backend/app/pricing backend/app/api/pricing.py backend/app/main.py backend/tests
git commit -m "feat: create auditable standard price versions"
```

### Task 6: Compare a new quote document with internal standard prices

**Files:**
- Create: `backend/app/analysis/service.py`
- Create: `backend/app/analysis/__init__.py`
- Create: `backend/app/api/analysis.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/analysis/test_quote_analysis.py`
- Test: `backend/tests/api/test_analysis_api.py`

- [ ] **Step 1: Write failing comparison tests**

```python
def test_matched_line_compares_against_latest_approved_price(
    session,
    source_document,
    matched_line,
    approved_price,
):
    result = analyze_document(session, source_document.id)
    line = result.lines[0]
    assert line.match_status == "MATCHED"
    assert line.quote_unit_price == Decimal("150")
    assert line.reference_price == approved_price.median_price
    assert line.variance_amount == Decimal("30")
    assert line.variance_percent == Decimal("25.000000")
    assert line.assessment == "HIGH"


def test_candidate_does_not_apply_price_automatically(
    session,
    source_document,
    candidate_only_line,
):
    line = analyze_document(session, source_document.id).lines[0]
    assert line.match_status == "CANDIDATE"
    assert line.reference_price is None
    assert line.assessment == "REVIEW_REQUIRED"
```

- [ ] **Step 2: Implement analysis rules**

For each current parsed row of a document:

- latest cleansing `EXCLUDED`: return `EXCLUDED`, no comparison;
- latest cleansing `REVIEW_REQUIRED`: return `REVIEW_REQUIRED`, no comparison;
- current approved membership and current standard price: compare;
- candidates without approved membership: `CANDIDATE`, no price application;
- no candidate: `NO_MATCH`, mark future market-price lookup;
- approved membership without approved price version: `MATCHED_NO_PRICE`.

Use median as the default reference, while returning min/average/max. Assessment thresholds are explicit configuration:

```python
price_variance_review_percent: Decimal = Decimal("10")
price_variance_high_percent: Decimal = Decimal("20")
```

`LOW` is below `-10%`, `WITHIN_RANGE` is between `-10%` and `10%`, `REVIEW` is above `10%` through `20%`, and `HIGH` is above `20%`. Preserve exact source, membership, standard-item version, and standard-price version IDs.

- [ ] **Step 3: Implement typed analysis endpoints**

- `GET /api/analysis/documents`;
- `GET /api/analysis/documents/{id}`;
- `POST /api/analysis/documents/{id}/refresh-candidates`.

The list returns logical documents with raw/current-status counts and whether analysis is ready. Detail supports cursor pagination and filters for assessment/match status. Refresh creates no membership; it only returns or caches replaceable candidate evidence.

- [ ] **Step 4: Run tests and commit**

Run:

```powershell
cd backend
python -m pytest tests/analysis tests/api/test_analysis_api.py -q
python -m pytest -q
```

Expected: matched/candidate/no-match/excluded/review paths, exact variance, missing prices, pagination, and provenance pass.

Commit:

```powershell
git add backend/app/analysis backend/app/api/analysis.py backend/app/main.py backend/tests
git commit -m "feat: compare quotes with internal standard prices"
```

### Task 7: Add grouping and price-analysis pages without removing cleansing review

**Files:**
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/components/AppNavigation.tsx`
- Create: `frontend/src/pages/GroupingReviewPage.tsx`
- Create: `frontend/src/pages/StandardPricesPage.tsx`
- Create: `frontend/src/pages/QuoteAnalysisPage.tsx`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/pages/GroupingReviewPage.test.tsx`
- Test: `frontend/src/pages/StandardPricesPage.test.tsx`
- Test: `frontend/src/pages/QuoteAnalysisPage.test.tsx`
- Test: `frontend/src/pages/CleansingReviewPage.test.tsx`

- [ ] **Step 1: Write failing navigation-preservation test**

```tsx
it("keeps the cleansing review page available", async () => {
  renderApp("/cleansing");
  expect(
    await screen.findByRole("heading", { name: "BEARING" }),
  ).toBeVisible();
  expect(
    screen.getByRole("link", { name: "정제 검토" }),
  ).toHaveAttribute("aria-current", "page");
});
```

- [ ] **Step 2: Write failing grouping workflow test**

```tsx
it("shows candidate evidence and submits a human match", async () => {
  renderApp("/grouping");
  await user.click(
    await screen.findByRole("button", { name: /BEARING 6204/ }),
  );
  expect(screen.getByText("단위 호환")).toBeVisible();
  expect(screen.getByText("모델 토큰 6204")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "이 품목으로 확정" }));
  expect(lastRequestBody()).toMatchObject({
    status: "MATCHED",
    expected_current_decision_id: null,
  });
});
```

- [ ] **Step 3: Implement restrained application navigation**

Use the existing visual system. Add four utility destinations:

- 정제 검토;
- 품목 그룹핑;
- 표준단가;
- 견적 비교.

Do not turn the application into a card dashboard. Desktop uses a compact left/top navigation and a primary work surface; mobile uses a horizontally scrollable labeled navigation with visible focus. Preserve all existing cleansing behavior and URLs.

- [ ] **Step 4: Implement grouping review**

The left list contains unmatched included rows. The inspector shows raw/normalized values, source provenance, deterministic scores, matched model tokens, unit/spec gates, and embedding availability. Actions:

- create a new standard item and match;
- match an existing candidate;
- reject current candidates/no match;
- edit document supplier/date metadata.

Every mutation sends the expected current decision/version ID, locks stale data, handles `409`, and advances focus predictably.

- [ ] **Step 5: Implement standard-price page**

Show current standard items, current canonical version, member count, draft vs approved version, min/median/average/max, supplier count, latest quote date, and contributing source rows. `표준단가 버전 승인` requires actor and displays the immutable version history.

- [ ] **Step 6: Implement quote-analysis page**

Select a source document and show each line's quote price, match status, canonical item, reference median/range, variance, assessment, and evidence links. `CANDIDATE` and `NO_MATCH` rows must never show an applied reference price. Filters operate server-side and keep the shell mounted during loading/errors.

- [ ] **Step 7: Run frontend and backend regression**

Run:

```powershell
cd frontend
npm test -- --run
npm run build
npm run lint
npm audit --audit-level=high

cd ../backend
python -m pytest -q
```

Expected: all page workflows, accessibility states, API contracts, and the existing cleansing page pass.

- [ ] **Step 8: Commit**

```powershell
git add frontend
git commit -m "feat: add standard item and quote analysis workspace"
```

### Task 8: Migrate the local corpus into standard-item workflows

**Files:**
- Create: `backend/app/catalog/cli.py`
- Create: `backend/tests/integration/test_standard_item_pipeline.py`
- Modify: `backend/app/cli.py`
- Modify: `docs/HANDOFF_2026-07-24.md`
- Modify: `docs/README_실행방법.txt`

- [ ] **Step 1: Add catalog CLI commands**

Add:

```powershell
python -m app.cli catalog-seed --database-file <db>
python -m app.cli embedding-index --database-file <db>
python -m app.cli standard-price-drafts --database-file <db>
```

`catalog-seed` may auto-create a standard item only for exact normalized name+spec+unit groups with at least two currently included rows and no unit/model-token conflicts. It records each auto-seeded membership with `method="EXACT_RULE_V1"` and evidence. Every fuzzy or embedding result remains unmatched for human review.

`embedding-index` refuses to call hChat unless explicitly enabled. With disabled/custom-unconfigured hChat it exits with an informative code and leaves deterministic matching available; `--mock` builds a clearly labeled local test index.

`standard-price-drafts` reports drafts but never approves versions.

- [ ] **Step 2: Write the full-pipeline integration test**

```python
def test_source_to_standard_price_to_analysis_pipeline(
    session,
    sample_quote_corpus,
):
    ingest_corpus(session, sample_quote_corpus)
    seed_exact_catalog(session)
    item = approve_remaining_candidate(session)
    price = approve_standard_price(session, item.id)
    analysis = analyze_document(session, sample_quote_corpus.new_quote_id)

    assert price.observation_count == 2
    assert analysis.lines[0].standard_price_version_id == price.id
    assert analysis.lines[0].assessment == "HIGH"
```

Also assert a semantic-only candidate stays `CANDIDATE`, a unit conflict stays `NO_MATCH`, and rerunning ingestion/seeding/index/drafts adds no duplicate decisions or versions.

- [ ] **Step 3: Run against an ignored copy of the real local DB**

Create a new ignored DB under `backend/.local/`; do not alter `corpus-audit-clean.sqlite3`. Ingest the same 48-file corpus, seed exact groups, and report:

- included rows eligible for grouping;
- exact groups and exact memberships created;
- unmatched rows;
- conflicts held for review;
- standard-price drafts available;
- groups missing supplier/date metadata;
- embedding status `DISABLED` locally.

Do not auto-approve standard-price versions in the real corpus. Do not call hChat or external sites.

- [ ] **Step 4: Update handoff and execution docs**

Document:

- the cleansing review page remains for the facilities-purchasing team;
- exact groups are rule-created and fuzzy/semantic candidates require approval;
- how office Claude should insert the hChat sample into the adapter codec and run only mocked contract tests first;
- standard-price versions require explicit approval;
- market-price DB and DeviceMart/Mouser remain the next plan;
- observed real counts and unresolved metadata/parser limitations.

- [ ] **Step 5: Complete verification**

Run:

```powershell
cd backend
python -m pytest -q
python -m alembic upgrade head
python -m alembic check

cd ../frontend
npm test -- --run
npm run build
npm run lint
npm audit --audit-level=high

git diff --check
git status --short
```

Expected: all tests pass, migrations are current, ignored local DB/index/report files are not staged, and quote originals remain unchanged.

- [ ] **Step 6: Commit**

```powershell
git add backend frontend docs
git commit -m "feat: migrate local data into standard price analysis"
```

## Plan self-review

- **Spec coverage:** Sections 7 and 8 of the approved design map to Tasks 1–8. Exact/fuzzy/embedding candidate order, unit/spec compatibility, human approval, index fingerprinting, included-only standard prices, immutable price versions, and source-to-analysis flow are covered.
- **Scope boundary:** DeviceMart, Mouser, market evidence capture, and live market fallback are excluded and remain a separate deployable plan. hChat is never called locally and the unknown custom contract has one isolated codec boundary.
- **No automatic semantic merge:** Embedding and fuzzy scores only create candidates. The only automatic grouping allowed is exact normalized name+spec+unit with at least two included observations and no conflicts.
- **Type consistency:** `StandardItem`, `StandardItemVersion`, `DocumentMetadataVersion`, `ItemMembershipDecision`, `StandardPriceVersion`, `MembershipStatus`, `CandidateScore`, and `EmbeddingClient` keep the same names across models, services, APIs, UI, and CLI.
- **Auditability:** Every manual state change is append-only and concurrency-checked. Every approved price stores contributing decision IDs and a deterministic fingerprint. Existing cleansing review and original evidence remain untouched.
- **Placeholder scan:** Every implementation step names concrete behavior, files, commands, assertions, and error outcomes; no unfinished markers remain.
