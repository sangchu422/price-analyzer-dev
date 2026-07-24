# Market Price Cache and Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cache-first DeviceMart and Mouser market-price lookup for internally unmatched items while preserving every URL, response, screenshot, price tier, and collection decision.

**Architecture:** Source adapters return a shared product/snapshot/evidence contract and never write directly to the database. `MarketLookupService` searches valid SQLite snapshots first, calls configured adapters only on a miss or expiry, and stores results transactionally. Mouser uses its official Search API; DeviceMart starts with auditable CSV/Excel import and a separately enabled HTML collector after an access-policy check.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, SQLite, httpx, Pydantic, Playwright, pytest, React, TypeScript, TanStack Query.

---

## File map

- `backend/app/market/models.py`: market products, snapshots, evidence, and lookup runs.
- `backend/app/market/contracts.py`: shared adapter input/output types.
- `backend/app/market/repository.py`: cache and evidence persistence.
- `backend/app/market/service.py`: cache-first lookup orchestration.
- `backend/app/market/comparison.py`: comparable price-tier selection and range summary.
- `backend/app/market/adapters/base.py`: adapter protocol.
- `backend/app/market/adapters/mouser.py`: official Mouser Search API client.
- `backend/app/market/adapters/devicemart_import.py`: CSV/Excel import.
- `backend/app/market/adapters/devicemart_html.py`: explicitly enabled HTML collector.
- `backend/app/market/evidence.py`: atomic JSON/HTML/image evidence storage.
- `backend/app/api/market.py`: lookup, cache, and evidence APIs.
- `frontend/src/pages/MarketPricePage.tsx`: cached/live source results and evidence viewer.
- `backend/app/cli.py`: explicit local batch collection commands.

### Task 1: Add market product, snapshot, evidence, and lookup-run tables

**Files:**
- Create: `backend/app/market/models.py`
- Create: `backend/alembic/versions/0003_market_price.py`
- Create: `backend/tests/market/test_models.py`

- [ ] **Step 1: Write the failing evidence relationship test**

```python
def test_market_snapshot_keeps_tier_and_original_evidence(session):
    product = MarketProduct(
        source="MOUSER",
        source_product_id="123-MPN",
        manufacturer_part_number="MPN-1",
        name="SERVO CONNECTOR",
        product_url="https://www.mouser.kr/example",
    )
    snapshot = MarketPriceSnapshot(
        product=product,
        currency="KRW",
        unit_price=1200,
        minimum_quantity=10,
        stock=500,
        collected_at=datetime(2026, 7, 25, 10, 0),
        expires_at=datetime(2026, 8, 1, 10, 0),
    )
    snapshot.evidence.append(
        MarketEvidence(
            kind="JSON",
            storage_path="data/evidence/mouser/abc.json",
            sha256="a" * 64,
            collector_version="mouser-v1",
        )
    )
    session.add(product)
    session.commit()
    assert snapshot.evidence[0].storage_path.endswith(".json")
```

- [ ] **Step 2: Verify failure**

Run: `cd backend && python -m pytest tests/market/test_models.py -q`

Expected: FAIL importing `MarketProduct`.

- [ ] **Step 3: Implement market tables**

```python
# backend/app/market/models.py
class MarketProduct(Base):
    __tablename__ = "market_product"
    __table_args__ = (UniqueConstraint("source", "source_product_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_product_id: Mapped[str] = mapped_column(String)
    manufacturer_part_number: Mapped[str | None] = mapped_column(String, index=True)
    manufacturer: Mapped[str | None] = mapped_column(String)
    name: Mapped[str]
    spec: Mapped[str] = mapped_column(default="")
    category: Mapped[str] = mapped_column(default="")
    product_url: Mapped[str]
    image_url: Mapped[str | None]
    snapshots: Mapped[list["MarketPriceSnapshot"]] = relationship(back_populates="product")


class MarketPriceSnapshot(Base):
    __tablename__ = "market_price_snapshot"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("market_product.id"))
    currency: Mapped[str]
    unit_price: Mapped[float]
    minimum_quantity: Mapped[int] = mapped_column(default=1)
    minimum_order_quantity: Mapped[int | None]
    stock: Mapped[int | None]
    vat_included: Mapped[bool | None]
    shipping_included: Mapped[bool | None]
    collected_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    product: Mapped[MarketProduct] = relationship(back_populates="snapshots")
    evidence: Mapped[list["MarketEvidence"]] = relationship(back_populates="snapshot")


class MarketEvidence(Base):
    __tablename__ = "market_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("market_price_snapshot.id"))
    kind: Mapped[str]
    storage_path: Mapped[str]
    sha256: Mapped[str]
    source_url: Mapped[str | None]
    collector_version: Mapped[str]
    warning: Mapped[str | None]
    snapshot: Mapped[MarketPriceSnapshot] = relationship(back_populates="evidence")


class MarketLookupRun(Base):
    __tablename__ = "market_lookup_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    query: Mapped[str]
    requested_quantity: Mapped[int]
    cache_state: Mapped[str]
    adapters_attempted_json: Mapped[str]
    result_snapshot_ids_json: Mapped[str]
    error_json: Mapped[str]
    started_at: Mapped[datetime]
    completed_at: Mapped[datetime | None]
```

- [ ] **Step 4: Apply migration and test**

Run: `cd backend && alembic upgrade head && python -m pytest tests/market/test_models.py -q`

Expected: migration `0003` applies and evidence relationships pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/market backend/alembic backend/tests/market
git commit -m "feat: add market price and evidence schema"
```

### Task 2: Define adapter contracts and atomic evidence storage

**Files:**
- Create: `backend/app/market/contracts.py`
- Create: `backend/app/market/adapters/base.py`
- Create: `backend/app/market/evidence.py`
- Create: `backend/tests/market/test_evidence.py`

- [ ] **Step 1: Write evidence integrity tests**

```python
def test_evidence_store_is_content_addressed(tmp_path):
    store = EvidenceStore(tmp_path)
    first = store.save_bytes("MOUSER", "JSON", b'{"price":1200}', ".json")
    second = store.save_bytes("MOUSER", "JSON", b'{"price":1200}', ".json")
    assert first.path == second.path
    assert first.sha256 == second.sha256
    assert first.path.read_bytes() == b'{"price":1200}'
```

- [ ] **Step 2: Define common immutable contracts**

```python
# backend/app/market/contracts.py
@dataclass(frozen=True)
class PriceTier:
    minimum_quantity: int
    unit_price: Decimal
    currency: str


@dataclass(frozen=True)
class CollectedEvidence:
    kind: Literal["JSON", "HTML", "IMAGE", "IMPORT"]
    content: bytes
    extension: str
    source_url: str | None
    warning: str | None = None


@dataclass(frozen=True)
class CollectedProduct:
    source: Literal["MOUSER", "DEVICEMART"]
    source_product_id: str
    manufacturer_part_number: str | None
    manufacturer: str | None
    name: str
    spec: str
    category: str
    product_url: str
    image_url: str | None
    price_tiers: tuple[PriceTier, ...]
    stock: int | None
    minimum_order_quantity: int | None
    vat_included: bool | None
    shipping_included: bool | None
    evidence: tuple[CollectedEvidence, ...]
```

```python
# backend/app/market/adapters/base.py
class MarketAdapter(Protocol):
    source: str

    async def search(self, query: str) -> list[CollectedProduct]:
        ...
```

- [ ] **Step 3: Implement content-addressed evidence writes**

```python
# backend/app/market/evidence.py
class EvidenceStore:
    def __init__(self, root: Path):
        self.root = root

    def save_bytes(self, source: str, kind: str, content: bytes, extension: str):
        digest = hashlib.sha256(content).hexdigest()
        target = self.root / source.lower() / digest[:2] / f"{digest}{extension}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_bytes(content)
            temporary.replace(target)
        return StoredEvidence(path=target, sha256=digest, kind=kind)
```

- [ ] **Step 4: Run tests and commit**

Run: `cd backend && python -m pytest tests/market/test_evidence.py -q`

Expected: identical evidence is deduplicated by content hash and files are atomically written.

```bash
git add backend/app/market backend/tests/market
git commit -m "feat: define market adapter and evidence contracts"
```

### Task 3: Implement cache repository and expiry rules

**Files:**
- Create: `backend/app/market/repository.py`
- Create: `backend/tests/market/test_repository.py`
- Modify: `backend/app/core/config.py`

- [ ] **Step 1: Write cache validity tests**

```python
def test_valid_snapshot_is_returned_without_expired_rows(repository, now):
    repository.add_snapshot(expires_at=now + timedelta(days=1), unit_price=1000)
    repository.add_snapshot(expires_at=now - timedelta(seconds=1), unit_price=900)
    rows = repository.find_valid("MPN-1", quantity=10, now=now)
    assert [row.unit_price for row in rows] == [1000]


def test_expired_snapshot_is_available_only_as_stale_reference(repository, now):
    repository.add_snapshot(expires_at=now - timedelta(days=1), unit_price=900)
    assert repository.find_valid("MPN-1", 10, now) == []
    assert repository.find_stale("MPN-1", 10)[0].unit_price == 900
```

- [ ] **Step 2: Add configurable TTLs**

```python
class Settings(BaseSettings):
    market_default_ttl_hours: int = 168
    market_mouser_ttl_hours: int = 168
    market_devicemart_ttl_hours: int = 168
    market_evidence_folder: str = "data/market_evidence"
```

- [ ] **Step 3: Implement quantity-aware cache queries**

```python
def find_valid(self, query_key: str, quantity: int, now: datetime):
    statement = (
        select(MarketPriceSnapshot)
        .join(MarketProduct)
        .where(
            MarketProduct.manufacturer_part_number == query_key,
            MarketPriceSnapshot.minimum_quantity <= quantity,
            MarketPriceSnapshot.expires_at > now,
        )
        .order_by(MarketPriceSnapshot.collected_at.desc())
    )
    return list(self.session.scalars(statement))
```

The repository selects the highest `minimum_quantity` not exceeding requested quantity per product/source. It returns stale rows through a separate method and never labels them current.

- [ ] **Step 4: Run tests and commit**

Run: `cd backend && python -m pytest tests/market/test_repository.py -q`

Expected: valid, stale, source, and quantity-tier tests pass.

```bash
git add backend/app/market backend/app/core/config.py backend/tests/market
git commit -m "feat: add quantity-aware market price cache"
```

### Task 4: Implement the mocked Mouser official Search API adapter

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/core/config.py`
- Create: `backend/app/market/adapters/mouser.py`
- Create: `backend/tests/market/adapters/test_mouser.py`

- [ ] **Step 1: Add HTTP dependency and settings**

Add `"httpx>=0.28,<1"` to runtime dependencies.

```python
mouser_api_key: str = ""
mouser_api_base_url: str = "https://api.mouser.com/api/v2"
mouser_timeout_seconds: float = 15.0
```

- [ ] **Step 2: Write a no-network contract test**

```python
@pytest.mark.asyncio
async def test_mouser_maps_price_breaks_and_keeps_raw_json():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={
            "SearchResults": {
                "Parts": [{
                    "MouserPartNumber": "123-MPN",
                    "ManufacturerPartNumber": "MPN-1",
                    "Description": "Connector",
                    "Manufacturer": "Maker",
                    "ProductDetailUrl": "https://www.mouser.kr/ProductDetail/example",
                    "ImagePath": "https://example/image.jpg",
                    "AvailabilityInStock": "250",
                    "Min": "1",
                    "PriceBreaks": [
                        {"Quantity": 1, "Price": "₩1,500", "Currency": "KRW"},
                        {"Quantity": 10, "Price": "₩1,200", "Currency": "KRW"},
                    ],
                }]
            }
        })
    )
    adapter = MouserAdapter(settings_with_key(), httpx.AsyncClient(transport=transport))
    products = await adapter.search("MPN-1")
    assert products[0].price_tiers[1].unit_price == Decimal("1200")
    assert products[0].evidence[0].kind == "JSON"
```

- [ ] **Step 3: Implement the adapter**

```python
# backend/app/market/adapters/mouser.py
class MouserAdapter:
    source = "MOUSER"

    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    async def search(self, query: str) -> list[CollectedProduct]:
        if not self.settings.mouser_api_key:
            raise AdapterUnavailable("MOUSER_API_KEY is not configured")
        response = await self.client.post(
            f"{self.settings.mouser_api_base_url}/search/keyword",
            params={"apiKey": self.settings.mouser_api_key},
            json={"SearchByKeywordRequest": {"keyword": query, "records": 20, "startingRecord": 0}},
            timeout=self.settings.mouser_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        evidence = CollectedEvidence(
            kind="JSON",
            content=response.content,
            extension=".json",
            source_url=str(response.request.url),
        )
        return [map_part(part, evidence) for part in payload.get("SearchResults", {}).get("Parts", [])]
```

`map_part()` parses currency and price without floating-point conversion, preserves all price breaks, and records missing fields as warnings instead of inventing values.

- [ ] **Step 4: Run tests and commit**

Run: `cd backend && python -m pytest tests/market/adapters/test_mouser.py -q`

Expected: all tests pass with `MockTransport`; no API key or live network is required.

```bash
git add backend/pyproject.toml backend/app/core/config.py backend/app/market/adapters/mouser.py backend/tests/market/adapters
git commit -m "feat: add auditable Mouser market adapter"
```

### Task 5: Add DeviceMart import-first collection

**Files:**
- Create: `backend/app/market/adapters/devicemart_import.py`
- Create: `backend/tests/fixtures/devicemart_prices.csv`
- Create: `backend/tests/market/adapters/test_devicemart_import.py`

- [ ] **Step 1: Define the import schema**

```csv
source_product_id,manufacturer_part_number,manufacturer,name,spec,category,product_url,image_url,currency,minimum_quantity,unit_price,stock,minimum_order_quantity,vat_included,collected_at
DM-100,MPN-1,Maker,Connector,2P,Connector,https://www.devicemart.co.kr/goods/view?no=100,https://example/image.jpg,KRW,1,1500,20,1,true,2026-07-25T10:00:00+09:00
DM-100,MPN-1,Maker,Connector,2P,Connector,https://www.devicemart.co.kr/goods/view?no=100,https://example/image.jpg,KRW,10,1200,20,1,true,2026-07-25T10:00:00+09:00
```

- [ ] **Step 2: Write import tests**

```python
def test_devicemart_import_groups_price_tiers_and_keeps_source_file():
    products = DeviceMartImportAdapter().read(FIXTURES / "devicemart_prices.csv")
    assert len(products) == 1
    assert [tier.minimum_quantity for tier in products[0].price_tiers] == [1, 10]
    assert products[0].evidence[0].kind == "IMPORT"
```

- [ ] **Step 3: Implement strict import validation**

```python
REQUIRED_COLUMNS = {
    "source_product_id",
    "name",
    "product_url",
    "currency",
    "minimum_quantity",
    "unit_price",
    "collected_at",
}


def read(self, path: Path) -> list[CollectedProduct]:
    raw = path.read_bytes()
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    missing = REQUIRED_COLUMNS - set(rows[0])
    if missing:
        raise ImportValidationError(f"missing columns: {sorted(missing)}")
    evidence = CollectedEvidence("IMPORT", raw, path.suffix, None)
    return group_rows(rows, evidence)
```

Invalid numeric values, unsupported currencies, non-HTTP product URLs, and duplicate quantity tiers fail the import with row numbers.

- [ ] **Step 4: Run tests and commit**

Run: `cd backend && python -m pytest tests/market/adapters/test_devicemart_import.py -q`

Expected: valid import passes; malformed rows fail with exact row diagnostics.

```bash
git add backend/app/market/adapters/devicemart_import.py backend/tests
git commit -m "feat: add DeviceMart evidence-preserving price import"
```

### Task 6: Add a policy-gated DeviceMart HTML collector

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/core/config.py`
- Create: `backend/app/market/adapters/devicemart_html.py`
- Create: `backend/tests/market/adapters/test_devicemart_html.py`

- [ ] **Step 1: Add the explicit enable switch**

```python
devicemart_html_enabled: bool = False
devicemart_request_delay_seconds: float = 2.0
devicemart_search_url: str = "https://www.devicemart.co.kr/goods/search"
```

The default is disabled. Enabling requires recording the date and result of the access-policy/terms check in `docs/DECISIONS.md`.

- [ ] **Step 2: Write an offline HTML fixture test**

```python
@pytest.mark.asyncio
async def test_devicemart_parser_keeps_html_and_screenshot():
    page = FakePage.from_fixture("devicemart_search.html", screenshot=b"png")
    adapter = DeviceMartHtmlAdapter(enabled=True, page=page)
    products = await adapter.search("MPN-1")
    assert products[0].source == "DEVICEMART"
    assert {e.kind for e in products[0].evidence} == {"HTML", "IMAGE"}
```

- [ ] **Step 3: Implement fail-closed collection**

```python
class DeviceMartHtmlAdapter:
    source = "DEVICEMART"

    async def search(self, query: str) -> list[CollectedProduct]:
        if not self.enabled:
            raise AdapterUnavailable("DeviceMart HTML collection is disabled")
        await self.page.goto(self.search_url, wait_until="domcontentloaded")
        await self.page.locator('input[name="search_text"]').fill(query)
        await self.page.locator('button[type="submit"]').click()
        html = (await self.page.content()).encode("utf-8")
        image = await self.page.screenshot(full_page=True)
        cards = await parse_cards(self.page)
        if not cards:
            raise CollectorLayoutChanged("no recognized product cards")
        return map_cards(cards, html=html, screenshot=image)
```

The collector performs no login bypass, CAPTCHA bypass, or hidden API discovery. If selectors fail, it stores the failure evidence and marks only this adapter unavailable.

- [ ] **Step 4: Run offline tests and commit**

Run: `cd backend && python -m pytest tests/market/adapters/test_devicemart_html.py -q`

Expected: fixture parsing, evidence capture, disabled-by-default, and layout-change tests pass without live site access.

```bash
git add backend/pyproject.toml backend/app/core/config.py backend/app/market/adapters/devicemart_html.py backend/tests/market/adapters
git commit -m "feat: add policy-gated DeviceMart HTML collector"
```

### Task 7: Implement cache-first lookup orchestration

**Files:**
- Create: `backend/app/market/service.py`
- Create: `backend/tests/market/test_service.py`

- [ ] **Step 1: Write cache-hit, miss, stale, and failure tests**

```python
@pytest.mark.asyncio
async def test_cache_hit_never_calls_adapter(service, valid_snapshot):
    result = await service.lookup(query="MPN-1", quantity=10)
    assert result.cache_state == "HIT"
    service.adapters[0].search.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_miss_calls_adapter_and_persists_evidence(service):
    service.adapters[0].search.return_value = [collected_product()]
    result = await service.lookup(query="MPN-1", quantity=10)
    assert result.cache_state == "MISS_REFRESHED"
    assert result.snapshots[0].evidence


@pytest.mark.asyncio
async def test_failed_refresh_returns_stale_as_expired_reference(service, stale_snapshot):
    service.adapters[0].search.side_effect = TimeoutError()
    result = await service.lookup(query="MPN-1", quantity=10)
    assert result.cache_state == "STALE_REFRESH_FAILED"
    assert result.snapshots[0].is_current is False
```

- [ ] **Step 2: Implement the orchestration**

```python
class MarketLookupService:
    async def lookup(self, query: str, quantity: int) -> LookupResult:
        now = self.clock()
        cached = self.repository.find_valid(query, quantity, now)
        if cached:
            return LookupResult("HIT", current(cached), [])

        stale = self.repository.find_stale(query, quantity)
        errors = []
        refreshed = []
        for adapter in self.adapters:
            try:
                products = await adapter.search(query)
                refreshed.extend(self.repository.store_products(products, now))
            except Exception as exc:
                errors.append({"source": adapter.source, "type": type(exc).__name__, "message": str(exc)})
        if refreshed:
            return LookupResult("MISS_REFRESHED", current(refreshed), errors)
        return LookupResult("STALE_REFRESH_FAILED", stale_reference(stale), errors)
```

Every call writes a `MarketLookupRun`, including cache state, attempted sources, snapshot IDs, and errors.

- [ ] **Step 3: Run tests and commit**

Run: `cd backend && python -m pytest tests/market/test_service.py -q`

Expected: cache hits have zero adapter calls; failures never present stale prices as current.

```bash
git add backend/app/market/service.py backend/tests/market/test_service.py
git commit -m "feat: add cache-first market lookup orchestration"
```

### Task 8: Compare only compatible price conditions

**Files:**
- Create: `backend/app/market/comparison.py`
- Create: `backend/tests/market/test_comparison.py`

- [ ] **Step 1: Write quantity, currency, and condition tests**

```python
def test_selects_highest_applicable_quantity_break():
    assert select_tier(tiers(1, 1500, 10, 1200), quantity=12).unit_price == 1200


def test_different_currencies_are_not_averaged():
    summary = summarize_comparable([
        offer("KRW", 1200, vat=True, shipping=False),
        offer("USD", 1.0, vat=None, shipping=None),
    ])
    assert summary.groups.keys() == {("KRW", True, False), ("USD", None, None)}
```

- [ ] **Step 2: Implement condition grouping**

```python
def select_tier(tiers: list[PriceTier], quantity: int) -> PriceTier | None:
    eligible = [tier for tier in tiers if tier.minimum_quantity <= quantity]
    return max(eligible, key=lambda tier: tier.minimum_quantity) if eligible else None


def condition_key(offer: ComparableOffer):
    return offer.currency, offer.vat_included, offer.shipping_included
```

For each condition group, return min/median/max and seller count. Do not calculate a cross-currency or mixed-VAT average. Preserve the selected tier and product URL in each result.

- [ ] **Step 3: Run tests and commit**

Run: `cd backend && python -m pytest tests/market/test_comparison.py -q`

Expected: quantity breaks and condition grouping pass.

```bash
git add backend/app/market/comparison.py backend/tests/market/test_comparison.py
git commit -m "feat: compare compatible market price conditions"
```

### Task 9: Expose market lookup and evidence APIs

**Files:**
- Create: `backend/app/api/market.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/api/test_market_api.py`

- [ ] **Step 1: Write API workflow tests**

```python
def test_no_match_item_can_request_market_lookup(client, no_match_item):
    response = client.post(
        "/api/market/lookups",
        json={"raw_item_id": no_match_item.id, "query": "MPN-1", "quantity": 10},
    )
    assert response.status_code == 200
    assert response.json()["cache_state"] in {"HIT", "MISS_REFRESHED"}


def test_matched_item_requires_explicit_market_request(client, matched_item):
    response = client.post(
        "/api/market/lookups",
        json={"raw_item_id": matched_item.id, "query": "MPN-1", "quantity": 10},
    )
    assert response.status_code == 409
```

- [ ] **Step 2: Implement API contracts**

Provide:

- `POST /api/market/lookups`
- `GET /api/market/lookups/{id}`
- `GET /api/market/products/{id}/snapshots`
- `GET /api/market/evidence/{id}`
- `POST /api/market/imports/devicemart`

The lookup request accepts `force_market_comparison=false`. A `MATCHED` internal item requires this flag; a `NO_MATCH` item follows the market flow automatically. Evidence responses stream the stored file and verify its SHA-256 before returning it.

- [ ] **Step 3: Run tests and commit**

Run: `cd backend && python -m pytest tests/api/test_market_api.py -q`

Expected: no-match flow, explicit matched-item override, import, stale labels, and evidence integrity tests pass.

```bash
git add backend/app/api backend/app/main.py backend/tests/api
git commit -m "feat: expose market lookup and evidence APIs"
```

### Task 10: Add the React market-price and evidence screen

**Files:**
- Create: `frontend/src/pages/MarketPricePage.tsx`
- Create: `frontend/src/components/EvidenceViewer.tsx`
- Create: `frontend/src/pages/MarketPricePage.test.tsx`

- [ ] **Step 1: Write UI tests**

```tsx
it("labels cached and expired prices distinctly", async () => {
  render(<MarketPricePage rawItemId={9} />);
  expect(await screen.findByText("캐시 사용")).toBeVisible();
  expect(screen.getByText("기간 만료 참고자료")).toBeVisible();
});

it("shows source URL quantity tier and evidence action", async () => {
  render(<MarketPricePage rawItemId={9} />);
  expect(await screen.findByText("10개 이상")).toBeVisible();
  expect(screen.getByRole("link", {name: "상품 원본"})).toHaveAttribute("href");
  expect(screen.getByRole("button", {name: "수집 증빙 보기"})).toBeEnabled();
});
```

- [ ] **Step 2: Implement source and condition presentation**

```tsx
<section aria-labelledby={`source-${group.source}`}>
  <h2 id={`source-${group.source}`}>{group.source}</h2>
  <p>{group.cache_state === "HIT" ? "캐시 사용" : "실시간 갱신"}</p>
  {group.offers.map((offer) => (
    <article key={offer.snapshot_id}>
      <strong>{formatMoney(offer.unit_price, offer.currency)}</strong>
      <span>{`${offer.minimum_quantity}개 이상`}</span>
      <a href={offer.product_url} target="_blank" rel="noreferrer">상품 원본</a>
      <button onClick={() => openEvidence(offer.evidence_ids)}>수집 증빙 보기</button>
    </article>
  ))}
</section>
```

The screen separates internal standard price and external market price, shows source/collection time/expiry/stock/MOQ/VAT/shipping, and never labels external prices as standard prices.

- [ ] **Step 3: Run frontend tests and build**

Run: `cd frontend && npm test -- --run && npm run build`

Expected: cache state, stale state, source conditions, and evidence viewer tests pass; Vite build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src
git commit -m "feat: add market price and evidence review UI"
```

### Task 11: Add explicit local batch collection and final integration tests

**Files:**
- Modify: `backend/app/cli.py`
- Create: `backend/tests/integration/test_market_flow.py`
- Modify: `docs/README_실행방법.txt`
- Modify: `docs/HANDOFF_2026-07-24.md`

- [ ] **Step 1: Add manual local commands**

```text
python -m app.cli market-import-devicemart data/import/devicemart_prices.csv
python -m app.cli market-refresh --source mouser --query-file data/import/market_queries.csv
python -m app.cli market-refresh --source devicemart-html --query-file data/import/market_queries.csv
```

The DeviceMart HTML command exits with a clear message unless `DEVICEMART_HTML_ENABLED=true`. No background scheduler is added.

- [ ] **Step 2: Add end-to-end cache-first test**

```python
@pytest.mark.asyncio
async def test_no_match_to_cached_market_evidence(app, seeded_no_match, fake_mouser):
    first = await lookup(app, seeded_no_match, "MPN-1", 10)
    second = await lookup(app, seeded_no_match, "MPN-1", 10)
    assert first.cache_state == "MISS_REFRESHED"
    assert second.cache_state == "HIT"
    assert fake_mouser.call_count == 1
    assert second.snapshots[0].evidence_ids
```

- [ ] **Step 3: Run full verification**

Run: `cd backend && python -m pytest -q`

Expected: all backend tests pass with adapters mocked.

Run: `cd frontend && npm test -- --run && npm run build`

Expected: all frontend tests pass and production build succeeds.

- [ ] **Step 4: Perform opt-in live smoke checks**

When keys/network are available:

Run: `cd backend && python -m app.cli market-refresh --source mouser --query "MPN-1" --limit 1`

Expected: one lookup run, product/snapshot rows, and JSON evidence are stored.

Run DeviceMart HTML only after the documented policy gate is approved; otherwise validate the import path.

- [ ] **Step 5: Update operating documentation and commit**

Document API-key placement, cache TTLs, evidence directory, manual batch commands, stale-price meaning, and source-specific failure recovery.

```bash
git add backend frontend docs
git commit -m "feat: complete cache-first market price workflow"
```

## Plan self-review

- Spec coverage: separate external market DB, valid-cache-first lookup, live fallback, Mouser official API, DeviceMart import and policy-gated HTML, price tiers, stock/MOQ/VAT/shipping, URL/JSON/HTML/image evidence, stale labeling, explicit local batch execution, and UI review are covered.
- Placeholder scan: each behavior has concrete files, contracts, tests, commands, and expected outcomes.
- Type consistency: `CollectedProduct`, `PriceTier`, `MarketPriceSnapshot`, `MarketEvidence`, `MarketLookupRun`, and `LookupResult` are consistent across adapters, repository, service, API, and UI.
