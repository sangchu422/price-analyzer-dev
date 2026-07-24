import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.cleansing.models import CleanDecision, CleanStatus
from app.db.base import Base
from app.documents.models import SourceDocument, SourceVariant
from app.quotes.models import RawQuoteItem


def test_raw_quote_text_remains_unchanged_after_cleaning_decision() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    document = SourceDocument(
        logical_name="1차 학습/재검토/5. 견적서",
    )
    document.variants.append(
        SourceVariant(
            path="견적서/1차 학습/재검토/5. 견적서_보안해제.xlsx",
            sha256="a" * 64,
            extension=".xlsx",
            security_state="UNLOCKED",
            preferred_for_parsing=True,
        )
    )
    raw_item = RawQuoteItem(
        source_sheet="단위장비1",
        source_row=12,
        source_cells='{"B12": "  네트워크 스위치  "}',
        item_name_raw="  네트워크 스위치  ",
        spec_raw="24 포트",
        unit_raw="대",
        quantity_raw="2",
        unit_price_raw="1,250,000",
        amount_raw="2,500,000",
        maker_raw="제조사 원문",
        parser_name="xlsx",
        parser_version="1.0",
    )
    raw_item.decisions.append(
        CleanDecision(
            status=CleanStatus.INCLUDED,
            reason_code="VALID",
            item_name_norm="네트워크 스위치",
            spec_norm="24 포트",
            unit_norm="대",
            maker_norm="제조사 원문",
            quantity=Decimal("2"),
            unit_price=Decimal("1250000"),
            amount=Decimal("2500000"),
            rule_version="clean-v1",
        )
    )
    document.raw_items.append(raw_item)

    with Session(engine) as session:
        session.add(document)
        session.commit()
        raw_item_id = raw_item.id

    with Session(engine) as session:
        saved = session.scalar(
            select(RawQuoteItem).where(RawQuoteItem.id == raw_item_id)
        )

        assert saved is not None
        assert saved.item_name_raw == "  네트워크 스위치  "
        assert saved.unit_price_raw == "1,250,000"
        assert saved.document.logical_name == "1차 학습/재검토/5. 견적서"
        assert saved.decisions[0].status is CleanStatus.INCLUDED
        assert saved.decisions[0].reason_code == "VALID"
        assert saved.decisions[0].rule_version == "clean-v1"


def test_initial_migration_creates_source_and_cleansing_tables(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "migration.sqlite3"
    backend_path = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["DATABASE_FILE"] = str(database_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(backend_path / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=backend_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    tables = set(
        inspect(
            create_engine(f"sqlite:///{database_path.as_posix()}")
        ).get_table_names()
    )
    assert {
        "source_document",
        "source_variant",
        "raw_quote_item",
        "clean_decision",
    } <= tables
