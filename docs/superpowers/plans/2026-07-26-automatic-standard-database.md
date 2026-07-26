# Automatic Standard Database Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mistaken legacy-Excel reconciliation feature with an idempotent standard database built from historical quote evidence, then provide a UTF-8-safe modern web flow for browsing standards and uploading incoming bids for price assessment.

**Architecture:** Existing quote rows are assigned an explicit `HISTORICAL_REFERENCE` or `INCOMING_BID` role. A versioned build service groups the latest eligible historical `INCLUDED` rows by normalized name/spec/unit, writes append-only memberships and captured price versions, and records an input fingerprint so identical rebuilds are no-ops. The React application removes the legacy reconciliation workspace, exposes the built standard database as read-only evidence, and makes `/analysis` the primary incoming-bid upload and assessment workflow.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, SQLite, openpyxl/PDF parsers already in the repository, React 19, TypeScript 6, TanStack Query, Vitest, Testing Library, CSS custom properties.

---

## File Structure

### Remove

- `backend/app/reconciliation/`: mistaken legacy workbook reconciliation domain.
- `backend/app/api/reconciliation.py`: legacy workbook upload/review API.
- `backend/tests/reconciliation/`: legacy workbook parser and matcher tests.
- `backend/tests/api/test_reconciliation_api.py`: legacy API tests.
- `frontend/src/pages/LegacyReconciliationPage.tsx`: legacy review page.
- `frontend/src/pages/LegacyReconciliationPage.test.tsx`: legacy page test.

### Create

- `backend/app/standard_database/models.py`: build-run and document-role models.
- `backend/app/standard_database/service.py`: deterministic standard DB build orchestration.
- `backend/app/standard_database/fingerprint.py`: stable input/build fingerprint helpers.
- `backend/app/standard_database/__init__.py`: public package exports.
- `backend/app/api/submissions.py`: incoming-bid upload and submission-status API.
- `backend/tests/standard_database/test_build_service.py`: grouping, singleton, idempotency and isolation tests.
- `backend/tests/standard_database/test_build_migration.py`: Alembic round-trip and schema-drift tests.
- `backend/tests/api/test_submissions_api.py`: incoming upload and role-isolation API tests.
- `backend/tests/api/test_reconciliation_removed.py`: asserts the retired legacy API remains unavailable.
- `frontend/src/components/MetricStrip.tsx`: reusable compact metrics row.
- `frontend/src/components/EvidenceBadge.tsx`: evidence-quality and state badge.
- `frontend/src/pages/QuoteAnalysisPage.encoding.test.tsx`: Korean text regression test.

### Modify

- `backend/alembic/versions/0007_legacy_reconciliation.py`: replace uncommitted legacy schema with standard-build/document-role schema and rename the file.
- `backend/app/db/models.py`: register the standard database models.
- `backend/app/documents/models.py`: expose document-role relationship.
- `backend/app/catalog/cli.py`: add build command adapter.
- `backend/app/cli.py`: register `standard-db-build`.
- `backend/app/api/catalog.py`: return build metadata and evidence quality without legacy fields.
- `backend/app/api/pricing.py`: remove legacy comparison fields and expose current build provenance.
- `backend/app/api/analysis.py`: enforce incoming-bid analysis and return build provenance.
- `backend/app/main.py`: remove reconciliation router and include submissions router.
- `backend/tests/api/test_catalog_api.py`: update standard DB response contract.
- `backend/tests/integration/test_cli.py`: verify build CLI and revision `0007`.
- `docs/DECISIONS.md`: replace reconciliation policy with automatic-build policy.
- `docs/HANDOFF_2026-07-24.md`: document build and incoming-upload operations.
- `frontend/index.html`: correct title/theme metadata and preserve UTF-8 declaration.
- `frontend/src/App.tsx`: remove `/reconciliation`.
- `frontend/src/api/client.ts`: remove legacy API types; add submission/build contracts.
- `frontend/src/components/AppNavigation.tsx`: remove legacy navigation and make analysis the primary action.
- `frontend/src/pages/StandardPricesPage.tsx`: convert approval workspace to read-only standard DB explorer.
- `frontend/src/pages/QuoteAnalysisPage.tsx`: add incoming file upload and assessment summary.
- `frontend/src/pages/StandardPricesPage.test.tsx`: verify evidence and build provenance.
- `frontend/src/pages/QuoteAnalysisPage.test.tsx`: verify upload and price assessment.
- `frontend/src/styles.css`: replace mixed legacy styling with a consistent modern enterprise design system.
- `frontend/src/styles.test.ts`: assert typography, focus, responsive and reduced-motion rules.

---

### Task 1: Remove the legacy Excel reconciliation contract

**Files:**
- Delete: `backend/app/reconciliation/`
- Delete: `backend/app/api/reconciliation.py`
- Delete: `backend/tests/reconciliation/`
- Delete: `backend/tests/api/test_reconciliation_api.py`
- Delete: `frontend/src/pages/LegacyReconciliationPage.tsx`
- Delete: `frontend/src/pages/LegacyReconciliationPage.test.tsx`
- Modify: `backend/app/main.py`
- Modify: `backend/app/db/models.py`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/components/AppNavigation.tsx`

- [ ] **Step 1: Add route-removal regression tests**

In `frontend/src/App.test.tsx`, add:

```tsx
it("does not expose the retired legacy reconciliation workspace", () => {
  renderApp("/reconciliation");
  expect(screen.queryByText("기존 표준단가 대조")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "기존 DB 대조" })).not.toBeInTheDocument();
});
```

In `backend/tests/api/test_reconciliation_removed.py`, add:

```python
def test_legacy_reconciliation_api_is_removed(client) -> None:
    response = client.get("/api/reconciliation/legacy-standard-db/runs/1")
    assert response.status_code == 404
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```powershell
cd frontend
npm test -- --run src/App.test.tsx
cd ..\backend
..\.venv\Scripts\python -m pytest tests/api/test_reconciliation_removed.py -q
```

Expected: the frontend test finds the old route or link; the backend test receives a non-404 response while the router is still registered.

- [ ] **Step 3: Remove all legacy reconciliation code**

Delete the listed files and remove:

```python
from app.api.reconciliation import router as reconciliation_router
app.include_router(
    reconciliation_router,
    prefix="/api/reconciliation",
    tags=["reconciliation"],
)
```

Remove `legacy_codes`, `reconciliation_run_id`,
`reconciliation_audit_link`, and `legacy_comparison` from backend and
frontend contracts. Remove `/reconciliation` from the app switch and
navigation destinations.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run the commands from Step 2.

Expected: both test commands pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app backend/tests frontend/src
git commit -m "refactor: remove legacy workbook reconciliation"
```

---

### Task 2: Add document roles and standard-build schema

**Files:**
- Create: `backend/app/standard_database/models.py`
- Create: `backend/app/standard_database/__init__.py`
- Modify: `backend/app/db/models.py`
- Replace: `backend/alembic/versions/0007_legacy_reconciliation.py`
- Test: `backend/tests/standard_database/test_build_migration.py`

- [ ] **Step 1: Write migration tests**

Create:

```python
def test_0007_creates_document_role_and_build_run_tables(migrated_engine):
    tables = set(inspect(migrated_engine).get_table_names())
    assert {"quote_document_role", "standard_database_build_run"} <= tables
    assert not any(name.startswith("legacy_") for name in tables)


def test_0007_round_trip_and_schema_check(alembic_runner):
    alembic_runner("upgrade", "head")
    alembic_runner("check")
    alembic_runner("downgrade", "0006")
    alembic_runner("upgrade", "head")
    alembic_runner("check")
```

- [ ] **Step 2: Run the migration tests and verify they fail**

```powershell
cd backend
..\.venv\Scripts\python -m pytest tests/standard_database/test_build_migration.py -q
```

Expected: FAIL because the new tables and models do not exist.

- [ ] **Step 3: Define the models**

Implement these contracts in `models.py`:

```python
class QuoteDocumentPurpose(StrEnum):
    HISTORICAL_REFERENCE = "HISTORICAL_REFERENCE"
    INCOMING_BID = "INCOMING_BID"


class StandardBuildStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class QuoteDocumentRole(Base):
    __tablename__ = "quote_document_role"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_document.id", ondelete="RESTRICT"), index=True
    )
    purpose: Mapped[QuoteDocumentPurpose]
    supersedes_role_id: Mapped[int | None] = mapped_column(
        ForeignKey("quote_document_role.id", ondelete="RESTRICT"), unique=True
    )
    decided_by: Mapped[str] = mapped_column(String(100))
    reason_detail: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(), default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )


class StandardDatabaseBuildRun(Base):
    __tablename__ = "standard_database_build_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(String(100))
    status: Mapped[StandardBuildStatus]
    report_path: Mapped[str | None] = mapped_column(String(1024))
    counts_json: Mapped[str] = mapped_column(Text, default="{}")
    error_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(), default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )
    finished_at: Mapped[datetime | None] = mapped_column(NaiveUTCDateTime())
```

Add unique constraint `(input_fingerprint, rule_version, status)` for successful
run reuse and append-only model protection consistent with the repository.

- [ ] **Step 4: Replace migration 0007**

Rename it to `0007_standard_database_build.py`. Create the two tables, indexes,
enum checks and restrictive foreign keys using model-equivalent SQLAlchemy types.
The downgrade drops only the two new tables.

- [ ] **Step 5: Run migration tests and schema check**

```powershell
..\.venv\Scripts\python -m pytest tests/standard_database/test_build_migration.py -q
```

Expected: PASS and `alembic check` reports `No new upgrade operations detected`.

- [ ] **Step 6: Commit**

```powershell
git add backend/alembic backend/app/standard_database backend/app/db/models.py backend/tests/standard_database
git commit -m "feat: add standard database build schema"
```

---

### Task 3: Build the deterministic standard database service

**Files:**
- Create: `backend/app/standard_database/fingerprint.py`
- Create: `backend/app/standard_database/service.py`
- Test: `backend/tests/standard_database/test_build_service.py`

- [ ] **Step 1: Write grouping and singleton tests**

Create tests that seed three historical `INCLUDED` rows: two normalized-equal
bearings and one singleton motor.

```python
result = build_standard_database(session, actor="LOCAL_STANDARD_DB_BUILD")

assert result.standard_item_count == 2
assert result.observation_count == 3
assert result.single_observation_count == 1
assert price_for("BEARING").observation_count == 2
assert price_for("MOTOR").observation_count == 1
```

Also seed the same name/spec with different units and assert two standard items
plus one unit-conflict report entry.

- [ ] **Step 2: Run the focused test and verify it fails**

```powershell
cd backend
..\.venv\Scripts\python -m pytest tests/standard_database/test_build_service.py -q
```

Expected: FAIL because `build_standard_database` does not exist.

- [ ] **Step 3: Implement stable input selection and fingerprinting**

`fingerprint.py` must expose:

```python
def standard_build_fingerprint(rows: Sequence[EligibleHistoricalRow]) -> str:
    payload = [
        {
            "raw_item_id": row.raw_item_id,
            "clean_decision_id": row.clean_decision_id,
            "name": row.name,
            "spec": row.spec,
            "unit": row.unit,
            "unit_price": None if row.unit_price is None else format(row.unit_price, "f"),
        }
        for row in sorted(rows, key=lambda row: row.raw_item_id)
    ]
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
```

`service.py` selects only rows whose current document role is
`HISTORICAL_REFERENCE` and current clean decision is `INCLUDED`.

- [ ] **Step 4: Implement exact-key grouping**

Use:

```python
GroupKey = tuple[str, str, str]

def group_key(row: EligibleHistoricalRow) -> GroupKey:
    return (
        normalize_match_text(row.name),
        normalize_match_text(row.spec),
        normalize_unit(row.unit),
    )
```

Reject an empty normalized name into `missing_name_count`. Keep different units
in distinct keys. Reuse the current `StandardItem` with the same current canonical
key; otherwise create one. Append membership decisions with
`method="STANDARD_DB_EXACT_V1"` and never supersede a human `MANUAL_*` match.

- [ ] **Step 5: Calculate and capture prices**

For every group with at least one valid unit price, call the existing pricing
calculator after memberships are flushed. Create a `CAPTURED` price version only
when its fingerprint differs from the current version. Use actor
`LOCAL_STANDARD_DB_BUILD`, not a human approval field in the UI.

- [ ] **Step 6: Run tests and verify they pass**

```powershell
..\.venv\Scripts\python -m pytest tests/standard_database/test_build_service.py -q
```

Expected: singleton, multi-observation, unit separation and source-evidence tests pass.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/standard_database backend/tests/standard_database
git commit -m "feat: build standards from historical quote evidence"
```

---

### Task 4: Guarantee idempotency, manual-decision safety and incoming-bid isolation

**Files:**
- Modify: `backend/app/standard_database/service.py`
- Modify: `backend/tests/standard_database/test_build_service.py`

- [ ] **Step 1: Add failing safety tests**

Add:

```python
first = build_standard_database(session, actor="LOCAL_STANDARD_DB_BUILD")
second = build_standard_database(session, actor="LOCAL_STANDARD_DB_BUILD")
assert second.reused_run_id == first.run_id
assert second.created_standard_items == 0
assert second.created_memberships == 0
assert second.created_price_versions == 0
```

Seed an identical `INCOMING_BID` row and assert observation counts do not change.
Seed a conflicting human membership and assert `ManualMembershipConflict` rolls
back the entire build.

- [ ] **Step 2: Run tests and verify they fail**

```powershell
..\.venv\Scripts\python -m pytest tests/standard_database/test_build_service.py -q
```

Expected: duplicate versions are created or incoming rows leak into prices.

- [ ] **Step 3: Implement run reuse and transaction boundary**

Before writes, find a successful run with the same fingerprint and rule version.
Return its counts without new writes. Raise conflicts before mutating standard
items. Keep the service free of `session.commit()` and let the CLI/API boundary
commit once; any exception causes a full rollback.

- [ ] **Step 4: Run tests and verify they pass**

Run the command from Step 2.

Expected: all safety tests pass and row/version counts remain unchanged on rerun.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/standard_database backend/tests/standard_database
git commit -m "test: enforce standard build isolation and idempotency"
```

---

### Task 5: Add the local build command and execute the initial standard DB build

**Files:**
- Modify: `backend/app/catalog/cli.py`
- Modify: `backend/app/cli.py`
- Modify: `backend/tests/integration/test_cli.py`

- [ ] **Step 1: Write the failing CLI test**

```python
exit_code = main([
    "standard-db-build",
    "--database-file", str(database),
    "--report", str(report),
    "--json",
])
payload = json.loads(capsys.readouterr().out)
assert exit_code == 0
assert payload["status"] == "SUCCEEDED"
assert payload["single_observation_count"] == 1
assert json.loads(report.read_text(encoding="utf-8")) == payload
```

- [ ] **Step 2: Run it and verify it fails**

```powershell
cd backend
..\.venv\Scripts\python -m pytest tests/integration/test_cli.py -q
```

Expected: argparse rejects `standard-db-build`.

- [ ] **Step 3: Register the command**

Add arguments:

```python
build = subparsers.add_parser("standard-db-build")
build.add_argument("--database-file", default=str(settings.database_path))
build.add_argument("--report")
build.add_argument("--json", action="store_true")
build.add_argument("--actor", default="LOCAL_STANDARD_DB_BUILD")
```

Use existing safe output path validation and report writing. Migrate the selected
database to head before opening the session.

- [ ] **Step 4: Run CLI tests and verify they pass**

Run Step 2.

Expected: all CLI tests pass.

- [ ] **Step 5: Build on a DB copy and verify counts**

Copy `backend/.local/standard-item-migration-v2.sqlite3`, run the command twice,
and verify:

```text
raw_quote_item before == raw_quote_item after
second.created_standard_items == 0
second.created_memberships == 0
second.created_price_versions == 0
```

After copy validation, back up the actual local DB and run the command once on the
actual local DB. Do not include incoming submissions.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/cli.py backend/app/catalog/cli.py backend/tests/integration/test_cli.py
git commit -m "feat: add local standard database build command"
```

---

### Task 6: Add incoming-bid upload without contaminating standards

**Files:**
- Create: `backend/app/api/submissions.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/analysis/service.py`
- Modify: `backend/app/api/analysis.py`
- Test: `backend/tests/api/test_submissions_api.py`
- Test: `backend/tests/api/test_analysis_api.py`

- [ ] **Step 1: Write upload and isolation tests**

```python
response = client.post(
    "/api/submissions",
    files={"file": ("new-bid.xlsx", workbook_bytes, XLSX_MIME)},
    data={"submitted_by": "buyer"},
)
assert response.status_code == 201
submission = response.json()
assert submission["purpose"] == "INCOMING_BID"

analysis = client.get(f"/api/analysis/documents/{submission['document_id']}")
assert analysis.status_code == 200
assert analysis.json()["document"]["purpose"] == "INCOMING_BID"
```

Run a standard DB rebuild after upload and assert its observation count is
unchanged.

- [ ] **Step 2: Run tests and verify they fail**

```powershell
cd backend
..\.venv\Scripts\python -m pytest tests/api/test_submissions_api.py tests/api/test_analysis_api.py -q
```

Expected: `/api/submissions` is 404.

- [ ] **Step 3: Implement submission ingestion**

`POST /api/submissions` must:

1. Validate supported extension and non-empty content.
2. Store the original under `backend/.local/submissions/<sha256>/<filename>`.
3. Reuse current ingestion parser selection.
4. Create or reuse `SourceDocument`/`SourceVariant`.
5. Append `QuoteDocumentRole(INCOMING_BID)`.
6. Return document ID, SHA, parser status and raw row counts.

It must not create catalog memberships or standard prices.

- [ ] **Step 4: Enforce analysis role**

`GET /api/analysis/documents/{id}` accepts only current `INCOMING_BID` documents
for the incoming assessment endpoint. Return `409 DOCUMENT_ROLE_MISMATCH` for
historical references.

- [ ] **Step 5: Run tests and verify they pass**

Run Step 2.

Expected: upload, analysis and isolation tests pass.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/api/submissions.py backend/app/api/analysis.py backend/app/analysis backend/app/main.py backend/tests/api
git commit -m "feat: upload incoming bids for isolated analysis"
```

---

### Task 7: Repair Korean encoding and establish the modern UI foundation

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/AppNavigation.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/styles.test.ts`
- Create: `frontend/src/pages/QuoteAnalysisPage.encoding.test.tsx`

- [ ] **Step 1: Add encoding regression tests**

Read every frontend source file as UTF-8 and reject replacement/mojibake markers:

```ts
const forbidden = ["\uFFFD", "占쏙옙", "紐", "寃ъ", "?쒖"];
for (const file of sourceFiles) {
  const text = readFileSync(file, "utf8");
  for (const marker of forbidden) expect(text).not.toContain(marker);
}
```

Render navigation and core pages and assert visible Korean labels:

```tsx
expect(screen.getByRole("link", { name: "정제 검토" })).toBeVisible();
expect(screen.getByRole("link", { name: "표준 DB" })).toBeVisible();
expect(screen.getByRole("link", { name: "신규 견적 분석" })).toBeVisible();
```

- [ ] **Step 2: Run tests and verify they fail**

```powershell
cd frontend
npm test -- --run src/styles.test.ts src/pages/QuoteAnalysisPage.encoding.test.tsx
```

Expected: one or more corrupted source strings are reported.

- [ ] **Step 3: Normalize source encoding**

Save all `.ts`, `.tsx`, `.css`, `.html`, `.md` touched by this feature as UTF-8.
Replace corrupted literals with verified Korean. Keep:

```html
<meta charset="UTF-8" />
<html lang="ko">
<title>Price Analyzer · 신규 견적 분석</title>
```

- [ ] **Step 4: Replace design tokens and application shell**

Use a restrained modern enterprise system:

```css
:root {
  color-scheme: light;
  --canvas: #f6f7f9;
  --surface: #ffffff;
  --surface-muted: #f0f2f5;
  --ink: #111827;
  --muted: #667085;
  --line: #e4e7ec;
  --accent: #5b4ce1;
  --accent-strong: #4738c7;
  --success: #067647;
  --warning: #b54708;
  --danger: #b42318;
  --radius-sm: 8px;
  --radius-md: 14px;
  --shadow-panel: 0 12px 32px rgb(16 24 40 / 0.08);
  font-family: Inter, Pretendard, "Noto Sans KR", "Segoe UI", sans-serif;
}
```

Use a compact sticky navigation, a clear page title/action row, aligned metric
strips, dense evidence tables and subtle state badges. Do not add decorative
gradients, oversized hero text, glass effects or excessive card grids. Preserve
keyboard focus, reduced motion and responsive layouts.

- [ ] **Step 5: Run encoding and style tests**

Run Step 2 plus:

```powershell
npm run lint
```

Expected: tests and lint pass with no corrupted Korean markers.

- [ ] **Step 6: Commit**

```powershell
git add frontend/index.html frontend/src/App.tsx frontend/src/components frontend/src/styles.css frontend/src/styles.test.ts frontend/src/pages/QuoteAnalysisPage.encoding.test.tsx
git commit -m "style: refresh application shell and repair Korean text"
```

---

### Task 8: Convert the standard-price page into a read-only standard DB explorer

**Files:**
- Create: `frontend/src/components/MetricStrip.tsx`
- Create: `frontend/src/components/EvidenceBadge.tsx`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/StandardPricesPage.tsx`
- Modify: `frontend/src/pages/StandardPricesPage.test.tsx`
- Modify: `backend/app/api/catalog.py`
- Modify: `backend/app/api/pricing.py`

- [ ] **Step 1: Write the page contract test**

Mock a standard item with one captured observation and assert:

```tsx
expect(await screen.findByRole("heading", { name: "표준 DB" })).toBeVisible();
expect(screen.getByText("근거 1건")).toBeVisible();
expect(screen.getByText("마지막 구축")).toBeVisible();
expect(screen.queryByRole("button", { name: /승인/ })).not.toBeInTheDocument();
expect(screen.getByRole("link", { name: "원본 견적 근거" })).toBeVisible();
```

- [ ] **Step 2: Run it and verify it fails**

```powershell
cd frontend
npm test -- --run src/pages/StandardPricesPage.test.tsx
```

Expected: the page still exposes approval controls or lacks build metadata.

- [ ] **Step 3: Update API contracts**

Catalog/pricing responses include:

```ts
type EvidenceQuality = "SINGLE_OBSERVATION" | "MULTI_OBSERVATION";

interface StandardBuildProvenance {
  build_run_id: number;
  built_at: string;
  rule_version: string;
}
```

Remove every legacy Excel field. Return current standard price statistics,
supplier/maker/date summaries and evidence source links.

- [ ] **Step 4: Rebuild the page**

The page layout is:

1. Title and latest build status.
2. Search/filter toolbar.
3. Compact standard-item list.
4. Selected item metrics.
5. Evidence table.
6. Immutable price-version history.

No approval actor input or approval mutation remains.

- [ ] **Step 5: Run backend and frontend focused tests**

```powershell
cd backend
..\.venv\Scripts\python -m pytest tests/api/test_catalog_api.py tests/api/test_pricing_api.py -q
cd ..\frontend
npm test -- --run src/pages/StandardPricesPage.test.tsx
```

Expected: all focused tests pass.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/api/catalog.py backend/app/api/pricing.py frontend/src
git commit -m "feat: present automatic standards as read-only evidence"
```

---

### Task 9: Make incoming-bid upload the primary analysis workflow

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/QuoteAnalysisPage.tsx`
- Modify: `frontend/src/pages/QuoteAnalysisPage.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write the upload-to-analysis test**

```tsx
await user.upload(screen.getByLabelText("신규 견적서"), bidFile);
await user.type(screen.getByLabelText("접수자"), "buyer");
await user.click(screen.getByRole("button", { name: "견적 분석 시작" }));

expect(await screen.findByText("고가 2건")).toBeVisible();
expect(screen.getByText("적정 5건")).toBeVisible();
expect(screen.getByText("시장가 확인 필요 1건")).toBeVisible();
expect(screen.getByRole("cell", { name: "SERVO MOTOR" })).toBeVisible();
```

Assert that the mocked request first calls `POST /api/submissions`, then
`GET /api/analysis/documents/{document_id}`.

- [ ] **Step 2: Run the test and verify it fails**

```powershell
cd frontend
npm test -- --run src/pages/QuoteAnalysisPage.test.tsx
```

Expected: no incoming file control exists.

- [ ] **Step 3: Implement upload and assessment states**

Add a compact upload bar with file, submitter and action. After upload, show:

- total parsed lines;
- matched/market-lookup/review-required counts;
- low/within-range/review/high counts;
- assessed monetary coverage;
- overall assessment;
- a filterable line table with quote, reference range, variance and evidence.

Keep market-price lookup status explicitly future-facing without fake values.

- [ ] **Step 4: Run the page test and build**

```powershell
npm test -- --run src/pages/QuoteAnalysisPage.test.tsx
npm run build
```

Expected: test passes and Vite build exits 0.

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/api/client.ts frontend/src/pages/QuoteAnalysisPage.tsx frontend/src/pages/QuoteAnalysisPage.test.tsx frontend/src/styles.css
git commit -m "feat: add modern incoming bid analysis workflow"
```

---

### Task 10: Update decisions, local data and complete end-to-end verification

**Files:**
- Modify: `docs/DECISIONS.md`
- Modify: `docs/HANDOFF_2026-07-24.md`
- Modify: local ignored DB and reports under `backend/.local/`

- [ ] **Step 1: Replace obsolete documentation**

Remove the legacy reconciliation section. Document:

- existing Excel is unused;
- historical/incoming role separation;
- `standard-db-build` command;
- singleton policy;
- rebuild and correction procedure;
- incoming upload workflow;
- hChat/market integrations remain deferred.

- [ ] **Step 2: Clean the mistaken local reconciliation state**

Verify `legacy_reconciliation_decision == 0` and
`standard_item_external_code == 0`. Downgrade old 0007 or restore the recorded
pre-0007 backup, apply the replacement 0007, and remove only the generated
legacy evidence copy and validation DB under the explicitly verified
`backend/.local/legacy-validation` and `backend/.local/evidence/legacy` paths.

- [ ] **Step 3: Run all backend verification**

```powershell
cd backend
..\.venv\Scripts\python -m pytest -q
..\.venv\Scripts\python -m alembic -c alembic.ini check
```

Expected: all tests pass and Alembic reports no new operations.

- [ ] **Step 4: Run all frontend verification**

```powershell
cd frontend
npm test -- --run
npm run lint
npm run build
```

Expected: all tests, lint and production build pass.

- [ ] **Step 5: Verify the actual local workflow**

Using a database copy first and then the actual local DB:

1. Run `standard-db-build` twice.
2. Verify the second run creates zero standards/memberships/prices.
3. Verify raw quote count is unchanged.
4. Upload a sample as `INCOMING_BID`.
5. Verify the standard price observation count remains unchanged.
6. Open `/standard-prices` and `/analysis` in a real browser.
7. Verify Korean text, responsive layout, keyboard focus and zero console errors.

- [ ] **Step 6: Commit documentation**

```powershell
git add docs/DECISIONS.md docs/HANDOFF_2026-07-24.md
git commit -m "docs: hand off automatic standard database workflow"
```

- [ ] **Step 7: Review final diff**

```powershell
git status --short
git diff main...HEAD --stat
git diff --check
```

Expected: no reconciliation page/API/model remains, no legacy Excel runtime
reference remains, and no whitespace errors are reported.
