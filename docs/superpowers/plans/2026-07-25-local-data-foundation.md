# Local Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the local FastAPI + SQLite foundation that preserves quote evidence, selects `_보안해제` copies without double-counting, and records deterministic cleansing decisions.

**Architecture:** Add a new `backend/` application beside the legacy Streamlit/PostgreSQL code. SQLAlchemy 2.0 models store immutable source/raw records and append-only cleansing decisions; service modules own file selection, parsing, and cleansing. A small React screen consumes read-only/review APIs, while the legacy code remains available during migration.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, SQLite, Pydantic Settings, openpyxl, xlrd, pypdf, pytest, React, TypeScript, Vite, TanStack Query.

---

## File map

- `backend/pyproject.toml`: runtime and test dependencies.
- `backend/app/main.py`: FastAPI application and router registration.
- `backend/app/core/config.py`: project-root-relative paths and environment settings.
- `backend/app/db/base.py`: SQLAlchemy declarative base.
- `backend/app/db/session.py`: SQLite engine and request session.
- `backend/app/documents/models.py`: source document and physical variant records.
- `backend/app/quotes/models.py`: immutable raw quote items.
- `backend/app/cleansing/models.py`: append-only cleansing decisions.
- `backend/app/ingestion/source_selector.py`: protected/original and `_보안해제` pairing.
- `backend/app/ingestion/readers.py`: Excel/PDF readers with provenance.
- `backend/app/ingestion/service.py`: transactional ingestion orchestration.
- `backend/app/cleansing/rules.py`: deterministic validation rules.
- `backend/app/cleansing/service.py`: current cleansing-state projection.
- `backend/app/api/documents.py`: document and ingestion endpoints.
- `backend/app/api/cleansing.py`: cleansing queue and review endpoints.
- `backend/alembic/versions/0001_source_and_cleansing.py`: initial SQLite schema.
- `backend/tests/`: unit and integration tests for each service.
- `frontend/`: Vite React shell and the source/cleansing review screen.

Legacy files under `price_analyzer_v2/` and `app.py` are not deleted in this plan.

### Task 1: Scaffold the local backend and configuration

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/core/config.py`
- Create: `backend/app/db/base.py`
- Create: `backend/app/db/session.py`
- Create: `backend/app/main.py`
- Create: `backend/tests/test_health.py`
- Modify: `price_analyzer_v2/.env.example`

- [ ] **Step 1: Write the failing health/config test**

```python
# backend/tests/test_health.py
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app


def test_paths_are_resolved_from_repository_root(tmp_path):
    settings = Settings(project_root=tmp_path, quote_folder="견적서")
    assert settings.quote_path == tmp_path / "견적서"
    assert settings.database_path == tmp_path / "data" / "price_analyzer.sqlite3"


def test_health_endpoint():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run the test and verify the missing application failure**

Run: `cd backend && python -m pytest tests/test_health.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'app'`.

- [ ] **Step 3: Add the package metadata and dependencies**

```toml
# backend/pyproject.toml
[project]
name = "price-analyzer-backend"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.116,<1",
  "uvicorn[standard]>=0.35,<1",
  "sqlalchemy>=2.0.41,<3",
  "alembic>=1.16,<2",
  "pydantic-settings>=2.10,<3",
  "python-multipart>=0.0.20,<1",
  "openpyxl>=3.1.5,<4",
  "xlrd>=2.0.2,<3",
  "pypdf>=5.8,<6",
]

[project.optional-dependencies]
dev = [
  "httpx>=0.28,<1",
  "pytest>=8.4,<9",
  "pytest-cov>=6.2,<7",
]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

- [ ] **Step 4: Implement root-relative settings, SQLite sessions, and health**

```python
# backend/app/core/config.py
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    project_root: Path = Path(__file__).resolve().parents[3]
    quote_folder: str = "견적서"
    database_file: str = "data/price_analyzer.sqlite3"

    @property
    def quote_path(self) -> Path:
        path = Path(self.quote_folder)
        return path if path.is_absolute() else self.project_root / path

    @property
    def database_path(self) -> Path:
        path = Path(self.database_file)
        return path if path.is_absolute() else self.project_root / path


settings = Settings()
```

```python
# backend/app/db/base.py
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

```python
# backend/app/db/session.py
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

settings.database_path.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(
    f"sqlite:///{settings.database_path.as_posix()}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(engine, class_=Session, expire_on_commit=False)


def get_session():
    with SessionLocal() as session:
        yield session
```

```python
# backend/app/main.py
from fastapi import FastAPI

app = FastAPI(title="Price Analyzer", version="0.1.0")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 5: Correct the example environment**

```dotenv
QUOTE_FOLDER=견적서
DATABASE_FILE=data/price_analyzer.sqlite3
H_CHAT_BASE_URL=
H_CHAT_API_KEY=
H_CHAT_PROJECT_ID=
MOUSER_API_KEY=
```

- [ ] **Step 6: Run the tests**

Run: `cd backend && python -m pytest tests/test_health.py -q`

Expected: `2 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend price_analyzer_v2/.env.example
git commit -m "feat: scaffold local FastAPI and SQLite backend"
```

### Task 2: Create immutable source and cleansing models

**Files:**
- Create: `backend/app/documents/models.py`
- Create: `backend/app/quotes/models.py`
- Create: `backend/app/cleansing/models.py`
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/versions/0001_source_and_cleansing.py`
- Create: `backend/tests/test_source_models.py`

- [ ] **Step 1: Write the failing append-only data-model test**

```python
# backend/tests/test_source_models.py
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.documents.models import SourceDocument, SourceVariant
from app.quotes.models import RawQuoteItem
from app.cleansing.models import CleanDecision, CleanStatus


def test_source_raw_and_decisions_are_separate():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        document = SourceDocument(logical_name="1차 학습/재검토/5. 견적서")
        document.variants.append(
            SourceVariant(
                path="견적서/1차 학습/재검토/5. 견적서_보안해제.xlsx",
                sha256="a" * 64,
                extension=".xlsx",
                security_state="UNLOCKED",
                selected_for_parsing_at_ingest=True,
            )
        )
        raw = RawQuoteItem(
            document=document,
            source_sheet="단위장비1",
            source_row=12,
            item_name_raw=" SERVO  MOTOR ",
            unit_price_raw="500,000",
        )
        raw.decisions.append(
            CleanDecision(
                status=CleanStatus.INCLUDED,
                reason_code="VALID",
                rule_version="clean-v1",
            )
        )
        session.add(document)
        session.commit()
        assert session.scalar(select(SourceDocument)).raw_items[0].item_name_raw == " SERVO  MOTOR "
```

- [ ] **Step 2: Run the test and verify missing model modules**

Run: `cd backend && python -m pytest tests/test_source_models.py -q`

Expected: FAIL importing `app.documents.models`.

- [ ] **Step 3: Implement source document and physical variant models**

```python
# backend/app/documents/models.py
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SourceDocument(Base):
    __tablename__ = "source_document"

    id: Mapped[int] = mapped_column(primary_key=True)
    logical_name: Mapped[str] = mapped_column(String, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    variants: Mapped[list["SourceVariant"]] = relationship(back_populates="document")
    raw_items: Mapped[list["RawQuoteItem"]] = relationship(back_populates="document")


class SourceVariant(Base):
    __tablename__ = "source_variant"
    __table_args__ = (UniqueConstraint("sha256"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("source_document.id"))
    path: Mapped[str] = mapped_column(String, unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    extension: Mapped[str] = mapped_column(String(8))
    security_state: Mapped[str] = mapped_column(String(32))
    selected_for_parsing_at_ingest: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    document: Mapped[SourceDocument] = relationship(back_populates="variants")
```

Add `from app.quotes.models import RawQuoteItem` under `TYPE_CHECKING` to avoid runtime import cycles.

- [ ] **Step 4: Implement immutable raw rows and append-only decisions**

```python
# backend/app/quotes/models.py
from typing import TYPE_CHECKING
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.cleansing.models import CleanDecision
    from app.documents.models import SourceDocument


class RawQuoteItem(Base):
    __tablename__ = "raw_quote_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("source_document.id"))
    source_sheet: Mapped[str | None] = mapped_column(String)
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_row: Mapped[int | None] = mapped_column(Integer)
    source_cells: Mapped[str | None] = mapped_column(String)
    item_name_raw: Mapped[str | None] = mapped_column(Text)
    spec_raw: Mapped[str | None] = mapped_column(Text)
    unit_raw: Mapped[str | None] = mapped_column(String)
    quantity_raw: Mapped[str | None] = mapped_column(String)
    unit_price_raw: Mapped[str | None] = mapped_column(String)
    amount_raw: Mapped[str | None] = mapped_column(String)
    maker_raw: Mapped[str | None] = mapped_column(String)
    parser_name: Mapped[str] = mapped_column(String)
    parser_version: Mapped[str] = mapped_column(String)
    parse_warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    document: Mapped["SourceDocument"] = relationship(back_populates="raw_items")
    decisions: Mapped[list["CleanDecision"]] = relationship(back_populates="raw_item")
```

```python
# backend/app/cleansing/models.py
from datetime import datetime
from enum import StrEnum
from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class CleanStatus(StrEnum):
    INCLUDED = "INCLUDED"
    EXCLUDED = "EXCLUDED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class CleanDecision(Base):
    __tablename__ = "clean_decision"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(ForeignKey("raw_quote_item.id"))
    status: Mapped[CleanStatus] = mapped_column(Enum(CleanStatus))
    reason_code: Mapped[str] = mapped_column(String(64))
    reason_detail: Mapped[str | None] = mapped_column(Text)
    item_name_norm: Mapped[str | None] = mapped_column(Text)
    spec_norm: Mapped[str | None] = mapped_column(Text)
    unit_norm: Mapped[str | None] = mapped_column(String)
    maker_norm: Mapped[str | None] = mapped_column(String)
    quantity: Mapped[float | None]
    unit_price: Mapped[float | None]
    amount: Mapped[float | None]
    rule_version: Mapped[str] = mapped_column(String(32))
    decided_by: Mapped[str] = mapped_column(String, default="SYSTEM")
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    raw_item: Mapped["RawQuoteItem"] = relationship(back_populates="decisions")
```

- [ ] **Step 5: Add and run the Alembic migration**

The migration creates `source_document`, `source_variant`, `raw_quote_item`, and `clean_decision` with indexes on `source_variant.sha256`, `raw_quote_item.document_id`, and `clean_decision.raw_item_id`.

Run: `cd backend && alembic upgrade head`

Expected: `Running upgrade -> 0001`.

- [ ] **Step 6: Run the model test**

Run: `cd backend && python -m pytest tests/test_source_models.py -q`

Expected: `1 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend/app backend/alembic backend/alembic.ini backend/tests
git commit -m "feat: add immutable quote source and cleansing schema"
```

### Task 3: Pair protected originals with `_보안해제` variants

**Files:**
- Create: `backend/app/ingestion/source_selector.py`
- Create: `backend/tests/ingestion/test_source_selector.py`

- [ ] **Step 1: Write tests for logical-name pairing and precedence**

```python
# backend/tests/ingestion/test_source_selector.py
from pathlib import Path
from app.ingestion.source_selector import build_source_groups


def test_unlocked_xlsx_wins_without_hiding_protected_original(tmp_path: Path):
    original = tmp_path / "5. 견적서.xls"
    unlocked = tmp_path / "5. 견적서_보안해제.xlsx"
    original.write_bytes(b"protected")
    unlocked.write_bytes(b"PK\x03\x04")

    groups = build_source_groups([original, unlocked])
    group = groups["5. 견적서"]

    assert group.preferred == unlocked
    assert group.variants == [original, unlocked]


def test_suffix_is_removed_only_at_filename_end(tmp_path: Path):
    path = tmp_path / "보안해제 장비_보안해제.xlsx"
    path.write_bytes(b"PK\x03\x04")
    groups = build_source_groups([path])
    assert "보안해제 장비" in groups
```

- [ ] **Step 2: Verify failure**

Run: `cd backend && python -m pytest tests/ingestion/test_source_selector.py -q`

Expected: FAIL importing `build_source_groups`.

- [ ] **Step 3: Implement deterministic grouping**

```python
# backend/app/ingestion/source_selector.py
from dataclasses import dataclass
from pathlib import Path
import re

UNLOCKED_SUFFIX = re.compile(r"_보안해제$", re.IGNORECASE)


@dataclass(frozen=True)
class SourceGroup:
    logical_name: str
    variants: list[Path]
    preferred: Path


def logical_stem(path: Path) -> str:
    return UNLOCKED_SUFFIX.sub("", path.stem).strip()


def _priority(path: Path) -> tuple[int, int, str]:
    unlocked = int(path.stem.lower().endswith("_보안해제"))
    xlsx = int(path.suffix.lower() == ".xlsx")
    return unlocked, xlsx, path.name


def build_source_groups(paths: list[Path]) -> dict[str, SourceGroup]:
    grouped: dict[str, list[Path]] = {}
    for path in sorted(paths):
        grouped.setdefault(logical_stem(path), []).append(path)
    return {
        name: SourceGroup(name, variants, max(variants, key=_priority))
        for name, variants in grouped.items()
    }
```

- [ ] **Step 4: Add the 12 real filename pairs as a regression fixture**

Create `backend/tests/fixtures/unlocked_pairs.json` with the protected and `_보안해제.xlsx` relative paths for all 12 files, and assert every pair yields one logical group with the unlocked copy preferred.

- [ ] **Step 5: Run tests**

Run: `cd backend && python -m pytest tests/ingestion/test_source_selector.py -q`

Expected: all source selector tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/ingestion backend/tests/ingestion backend/tests/fixtures
git commit -m "feat: prefer unlocked quote variants without duplicate ingestion"
```

### Task 4: Parse provenance-bearing raw records

**Files:**
- Create: `backend/app/ingestion/readers.py`
- Create: `backend/app/ingestion/service.py`
- Create: `backend/tests/ingestion/test_ingestion_service.py`

- [ ] **Step 1: Write a parser-contract integration test**

```python
def test_ingestion_preserves_sheet_row_and_original_values(
    session, sample_quote_xlsx
):
    result = ingest_path(session, sample_quote_xlsx)
    item = result.document.raw_items[0]
    assert item.source_sheet == "단위장비1"
    assert item.source_row == 7
    assert item.source_cells == "A7:G7"
    assert item.item_name_raw == "SERVO MOTOR"
    assert item.unit_price_raw == "500000"
```

- [ ] **Step 2: Verify the test fails**

Run: `cd backend && python -m pytest tests/ingestion/test_ingestion_service.py -q`

Expected: FAIL because `ingest_path` is missing.

- [ ] **Step 3: Define a reader-neutral parsed row**

```python
# backend/app/ingestion/readers.py
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ParsedRow:
    sheet: str | None
    page: int | None
    row: int | None
    cells: str | None
    item_name: str | None
    spec: str | None
    unit: str | None
    quantity: str | None
    unit_price: str | None
    amount: str | None
    maker: str | None
    warnings: tuple[str, ...] = ()


def read_quote(path: Path) -> list[ParsedRow]:
    if path.suffix.lower() == ".xlsx":
        return read_xlsx(path)
    if path.suffix.lower() == ".xls":
        return read_xls(path)
    if path.suffix.lower() == ".pdf":
        return read_pdf(path)
    raise ValueError(f"unsupported quote extension: {path.suffix}")
```

The Excel readers reuse the verified row-detection rules from `price_analyzer_v2/pipeline/parsers/` but return `ParsedRow` with exact provenance. The PDF reader records `page` and leaves `sheet`, `row`, and `cells` null.

- [ ] **Step 4: Implement transactional ingestion**

```python
# backend/app/ingestion/service.py
import hashlib
import json
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.documents.models import SourceDocument, SourceVariant
from app.ingestion.readers import read_quote
from app.ingestion.source_selector import logical_stem
from app.quotes.models import RawQuoteItem


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ingest_path(session: Session, path: Path) -> SourceVariant:
    digest = sha256(path)
    existing = session.scalar(
        select(SourceVariant).where(SourceVariant.sha256 == digest)
    )
    if existing:
        return existing

    logical_name = logical_stem(path)
    document = session.scalar(
        select(SourceDocument).where(SourceDocument.logical_name == logical_name)
    ) or SourceDocument(logical_name=logical_name)
    variant = SourceVariant(
        document=document,
        path=str(path),
        sha256=digest,
        extension=path.suffix.lower(),
        security_state="UNLOCKED" if path.stem.endswith("_보안해제") else "UNKNOWN",
        selected_for_parsing_at_ingest=path.stem.endswith("_보안해제"),
    )
    session.add(variant)
    session.flush()

    if variant.selected_for_parsing_at_ingest or not document.raw_items:
        for parsed in read_quote(path):
            document.raw_items.append(
                RawQuoteItem(
                    source_sheet=parsed.sheet,
                    source_page=parsed.page,
                    source_row=parsed.row,
                    source_cells=parsed.cells,
                    item_name_raw=parsed.item_name,
                    spec_raw=parsed.spec,
                    unit_raw=parsed.unit,
                    quantity_raw=parsed.quantity,
                    unit_price_raw=parsed.unit_price,
                    amount_raw=parsed.amount,
                    maker_raw=parsed.maker,
                    parser_name="quote-reader",
                    parser_version="reader-v1",
                    parse_warnings_json=json.dumps(parsed.warnings, ensure_ascii=False),
                )
            )
    session.commit()
    return variant
```

- [ ] **Step 5: Run ingestion tests**

Run: `cd backend && python -m pytest tests/ingestion -q`

Expected: tests prove hash idempotency, provenance retention, and unlocked precedence.

- [ ] **Step 6: Commit**

```bash
git add backend/app/ingestion backend/tests/ingestion
git commit -m "feat: ingest quote rows with immutable provenance"
```

### Task 5: Apply deterministic cleansing and review states

**Files:**
- Create: `backend/app/cleansing/rules.py`
- Create: `backend/app/cleansing/service.py`
- Create: `backend/tests/cleansing/test_rules.py`
- Create: `backend/tests/cleansing/test_outliers.py`

- [ ] **Step 1: Write failing rule tests**

```python
@pytest.mark.parametrize(
    ("item_name", "unit_price", "expected_reason"),
    [
        ("", "1000", "MISSING_ITEM_NAME"),
        ("SERVO MOTOR", "0", "INVALID_UNIT_PRICE"),
        ("이 윤", "100000", "SUMMARY_OR_FEE_LINE"),
    ],
)
def test_automatic_exclusions(item_name, unit_price, expected_reason):
    result = evaluate(make_raw(item_name=item_name, unit_price=unit_price))
    assert result.status == CleanStatus.EXCLUDED
    assert result.reason_code == expected_reason


def test_amount_mismatch_requires_review():
    result = evaluate(
        make_raw(item_name="BEARING", quantity="2", unit_price="1000", amount="9000")
    )
    assert result.status == CleanStatus.REVIEW_REQUIRED
    assert result.reason_code == "AMOUNT_MISMATCH"
```

- [ ] **Step 2: Run and verify failure**

Run: `cd backend && python -m pytest tests/cleansing/test_rules.py -q`

Expected: FAIL importing `evaluate`.

- [ ] **Step 3: Implement normalization and rule priority**

```python
# backend/app/cleansing/rules.py
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

from app.cleansing.models import CleanStatus

RULE_VERSION = "clean-v1"
FEE_PATTERN = re.compile(
    r"^\s*(합\s*계|소\s*계|이\s*윤|일반관리비|관리비|노무비|경비)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Evaluation:
    status: CleanStatus
    reason_code: str
    reason_detail: str | None
    item_name_norm: str | None
    spec_norm: str | None
    unit_norm: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None


def number(value: str | None) -> Decimal | None:
    if value is None or not str(value).strip():
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        return None


def text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def evaluate(raw) -> Evaluation:
    name = text(raw.item_name_raw)
    spec = text(raw.spec_raw)
    unit = text(raw.unit_raw).upper()
    quantity = number(raw.quantity_raw)
    unit_price = number(raw.unit_price_raw)
    amount = number(raw.amount_raw)

    if not name:
        return Evaluation(CleanStatus.EXCLUDED, "MISSING_ITEM_NAME", None, None, spec, unit, quantity, unit_price, amount)
    if unit_price is None or unit_price <= 0:
        return Evaluation(CleanStatus.EXCLUDED, "INVALID_UNIT_PRICE", None, name, spec, unit, quantity, unit_price, amount)
    if FEE_PATTERN.match(name):
        return Evaluation(CleanStatus.EXCLUDED, "SUMMARY_OR_FEE_LINE", None, name, spec, unit, quantity, unit_price, amount)
    if quantity and amount and abs(quantity * unit_price - amount) > max(Decimal("1"), amount * Decimal("0.01")):
        return Evaluation(CleanStatus.REVIEW_REQUIRED, "AMOUNT_MISMATCH", f"{quantity}×{unit_price}!={amount}", name, spec, unit, quantity, unit_price, amount)
    return Evaluation(CleanStatus.INCLUDED, "VALID", None, name, spec, unit, quantity, unit_price, amount)
```

- [ ] **Step 4: Add group-local MAD outlier detection**

```python
def mad_outlier_ids(rows: list[tuple[int, Decimal]]) -> set[int]:
    if len(rows) < 3:
        return set()
    values = sorted(value for _, value in rows)
    median = values[len(values) // 2]
    deviations = sorted(abs(value - median) for value in values)
    mad = deviations[len(deviations) // 2]
    if mad == 0:
        return {row_id for row_id, value in rows if value != median}
    return {
        row_id
        for row_id, value in rows
        if Decimal("0.6745") * abs(value - median) / mad > Decimal("3.5")
    }
```

Tests must prove fewer than three observations are never auto-flagged and flagged rows become `REVIEW_REQUIRED`, not deleted.

- [ ] **Step 5: Persist a new decision instead of updating old decisions**

```python
# backend/app/cleansing/service.py
def apply_rules(session: Session, raw_item: RawQuoteItem) -> CleanDecision:
    result = evaluate(raw_item)
    decision = CleanDecision(
        raw_item=raw_item,
        status=result.status,
        reason_code=result.reason_code,
        reason_detail=result.reason_detail,
        item_name_norm=result.item_name_norm,
        spec_norm=result.spec_norm,
        unit_norm=result.unit_norm,
        quantity=float(result.quantity) if result.quantity is not None else None,
        unit_price=float(result.unit_price) if result.unit_price is not None else None,
        amount=float(result.amount) if result.amount is not None else None,
        rule_version=RULE_VERSION,
    )
    session.add(decision)
    session.commit()
    return decision
```

- [ ] **Step 6: Run cleansing tests**

Run: `cd backend && python -m pytest tests/cleansing -q`

Expected: automatic exclusions, review states, and outlier recovery tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/cleansing backend/tests/cleansing
git commit -m "feat: add auditable quote cleansing decisions"
```

### Task 6: Expose ingestion and cleansing review APIs

**Files:**
- Create: `backend/app/api/documents.py`
- Create: `backend/app/api/cleansing.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/api/test_cleansing_api.py`

- [ ] **Step 1: Write API tests**

```python
def test_review_queue_returns_provenance(client, seeded_review_item):
    response = client.get("/api/cleansing/review-queue")
    assert response.status_code == 200
    row = response.json()["items"][0]
    assert row["reason_code"] == "AMOUNT_MISMATCH"
    assert row["source"]["sheet"] == "단위장비1"
    assert row["source"]["row"] == 12


def test_manual_review_appends_decision(client, seeded_review_item):
    response = client.post(
        f"/api/cleansing/{seeded_review_item.id}/decisions",
        json={"status": "INCLUDED", "reason_code": "MANUAL_REVIEW", "reason_detail": "원본 확인", "decided_by": "sangwoo"},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "INCLUDED"
```

- [ ] **Step 2: Implement endpoint contracts**

```python
# backend/app/api/cleansing.py
class DecisionRequest(BaseModel):
    status: CleanStatus
    reason_code: str
    reason_detail: str
    decided_by: str


@router.post("/{raw_item_id}/decisions", status_code=201)
def decide(raw_item_id: int, body: DecisionRequest, session: Session = Depends(get_session)):
    item = session.get(RawQuoteItem, raw_item_id)
    if item is None:
        raise HTTPException(404, "raw quote item not found")
    decision = CleanDecision(
        raw_item=item,
        status=body.status,
        reason_code=body.reason_code,
        reason_detail=body.reason_detail,
        rule_version="manual-v1",
        decided_by=body.decided_by,
    )
    session.add(decision)
    session.commit()
    return {"id": decision.id, "status": decision.status}
```

`GET /api/documents` returns logical documents with all variants and a single preferred parsing variant. `POST /api/documents/scan` scans the configured quote folder, groups variants, and ingests only preferred content.

- [ ] **Step 3: Register routers**

```python
from app.api import cleansing, documents

app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
app.include_router(cleansing.router, prefix="/api/cleansing", tags=["cleansing"])
```

- [ ] **Step 4: Run API tests**

Run: `cd backend && python -m pytest tests/api -q`

Expected: review queue and append-only review endpoints pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api backend/app/main.py backend/tests/api
git commit -m "feat: expose source and cleansing review APIs"
```

### Task 7: Add the local React review screen

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/pages/CleansingReviewPage.tsx`
- Create: `frontend/src/pages/CleansingReviewPage.test.tsx`

- [ ] **Step 1: Scaffold Vite React TypeScript**

Run: `npm create vite@latest frontend -- --template react-ts`

Expected: `frontend/src/main.tsx` and Vite scripts exist.

- [ ] **Step 2: Add dependencies and a failing review test**

Run: `cd frontend && npm install @tanstack/react-query react-router-dom && npm install -D vitest @testing-library/react @testing-library/jest-dom jsdom`

```tsx
it("shows source provenance and review reason", async () => {
  server.use(http.get("/api/cleansing/review-queue", () =>
    HttpResponse.json({items: [{
      id: 7,
      item_name: "BEARING",
      reason_code: "AMOUNT_MISMATCH",
      source: {file: "quote.xlsx", sheet: "단위장비1", row: 12},
    }]})
  ));
  render(<CleansingReviewPage />);
  expect(await screen.findByText("BEARING")).toBeVisible();
  expect(screen.getByText("quote.xlsx · 단위장비1 · 12행")).toBeVisible();
});
```

- [ ] **Step 3: Implement typed API access and review actions**

```tsx
// frontend/src/api/client.ts
export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<T>;
}
```

```tsx
// frontend/src/pages/CleansingReviewPage.tsx
export function CleansingReviewPage() {
  const query = useQuery({
    queryKey: ["cleansing-review"],
    queryFn: () => getJson<ReviewQueue>("/api/cleansing/review-queue"),
  });
  if (query.isLoading) return <p>검토 목록을 불러오는 중입니다.</p>;
  if (query.isError) return <p role="alert">검토 목록을 불러오지 못했습니다.</p>;
  return (
    <main>
      <h1>데이터 정제 검토</h1>
      {query.data!.items.map((item) => (
        <article key={item.id}>
          <h2>{item.item_name || "품명 없음"}</h2>
          <p>{item.reason_code}</p>
          <p>{`${item.source.file} · ${item.source.sheet ?? "PDF"} · ${item.source.row ?? item.source.page}행`}</p>
        </article>
      ))}
    </main>
  );
}
```

- [ ] **Step 4: Run frontend tests and production build**

Run: `cd frontend && npm test -- --run && npm run build`

Expected: tests pass and Vite produces `frontend/dist`.

- [ ] **Step 5: Commit**

```bash
git add frontend
git commit -m "feat: add local cleansing review screen"
```

### Task 8: Migrate and verify the local corpus

**Files:**
- Create: `backend/app/cli.py`
- Create: `backend/tests/integration/test_local_corpus.py`
- Modify: `docs/HANDOFF_2026-07-24.md`

- [ ] **Step 1: Add a corpus preflight command**

```python
# backend/app/cli.py
def preflight() -> int:
    paths = scan_supported_files(settings.quote_path)
    groups = build_source_groups(paths)
    unlocked = [group for group in groups.values() if group.preferred.stem.endswith("_보안해제")]
    print(f"logical_documents={len(groups)}")
    print(f"unlocked_preferred={len(unlocked)}")
    return 0
```

Expose it as `python -m app.cli preflight` and `python -m app.cli ingest`.

- [ ] **Step 2: Write the integration assertions**

```python
def test_real_corpus_has_no_protected_preferred_variant(real_quote_root):
    groups = build_source_groups(scan_supported_files(real_quote_root))
    assert all(
        not group.preferred.suffix.lower() == ".xls"
        or not group.preferred.stem.endswith("_보안해제")
        for group in groups.values()
    )
    assert sum(g.preferred.stem.endswith("_보안해제") for g in groups.values()) >= 15
```

- [ ] **Step 3: Run preflight and ingestion**

Run: `cd backend && python -m app.cli preflight`

Expected: the command reports all logical documents and at least 15 unlocked-preferred groups without reading protected originals as content.

Run: `cd backend && python -m app.cli ingest`

Expected: every preferred Excel/PDF source is either ingested or recorded with a specific parse status; a second run inserts zero duplicate raw items.

- [ ] **Step 4: Run the complete backend and frontend verification**

Run: `cd backend && python -m pytest -q`

Expected: all backend tests pass.

Run: `cd frontend && npm test -- --run && npm run build`

Expected: all frontend tests pass and the production build succeeds.

- [ ] **Step 5: Update the handoff with observed counts**

Record logical document count, preferred variant count, raw row count, `INCLUDED`/`EXCLUDED`/`REVIEW_REQUIRED` counts, and parse failures. Do not replace source files or remove protected originals.

- [ ] **Step 6: Commit**

```bash
git add backend frontend docs/HANDOFF_2026-07-24.md
git commit -m "feat: migrate local quote corpus into auditable data layers"
```

## Plan self-review

- Spec coverage: immutable originals, `_보안해제` precedence, provenance, cleaning statuses, reason codes, outlier review, SQLite, FastAPI, React, and local-only operation are covered.
- Deferred to the next plan: canonical grouping, hChat embeddings, standard-price versioning.
- Deferred to the third plan: Mouser, DeviceMart, market evidence, cache-first lookup.
- Placeholder scan: implementation steps contain concrete paths, commands, contracts, and expected results.
- Type consistency: `RawQuoteItem`, `CleanDecision`, `CleanStatus`, `ParsedRow`, and `SourceVariant` names are consistent across model, service, API, and test tasks.
