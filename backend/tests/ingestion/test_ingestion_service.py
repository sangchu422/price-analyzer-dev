from __future__ import annotations

import os
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import RawQuoteItem, SourceDocument, SourceVariant
from app.db.sqlite import configure_sqlite
from app.ingestion.readers import ParsedRow, read_quote
from app.ingestion.service import ingest_group, ingest_path
from app.ingestion.source_selector import build_source_groups


@pytest.fixture
def session() -> Session:
    engine = configure_sqlite(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


def _write_quote(
    path: Path,
    *,
    item_name: str = "SERVO MOTOR",
    unit_price: object = 500000,
) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "단위설비1"
    sheet.append(["견적서"])
    for _ in range(5):
        sheet.append([])
    sheet.append(["품명", "규격", "단위", "수량", "단가", "금액", "메이커"])
    sheet.append(
        [item_name, "AC 220V", "EA", "2", unit_price, "1000000", "ACME"]
    )
    workbook.save(path)


def test_ingestion_preserves_exact_variant_and_cell_provenance(
    session: Session,
    tmp_path: Path,
) -> None:
    quote = tmp_path / "설비 견적_보안해제.xlsx"
    _write_quote(quote)

    variant = ingest_path(session, quote, root=tmp_path)
    item = session.scalar(select(RawQuoteItem))

    assert item is not None
    assert variant.path == "설비 견적_보안해제.xlsx"
    assert item.source_variant is variant
    assert item.source_sheet == "단위설비1"
    assert item.source_page is None
    assert item.source_row == 8
    assert item.source_cells == "A8:G8"
    assert item.item_name_raw == "SERVO MOTOR"
    assert item.spec_raw == "AC 220V"
    assert item.unit_raw == "EA"
    assert item.quantity_raw == "2"
    assert item.unit_price_raw == "500000"
    assert item.amount_raw == "1000000"
    assert item.maker_raw == "ACME"


def test_same_file_content_is_idempotent(
    session: Session,
    tmp_path: Path,
) -> None:
    quote = tmp_path / "설비 견적.xlsx"
    _write_quote(quote)

    first = ingest_path(session, quote, root=tmp_path)
    second = ingest_path(session, quote, root=tmp_path)

    assert second.id == first.id
    assert session.scalar(select(func.count(SourceVariant.id))) == 1
    assert session.scalar(select(func.count(RawQuoteItem.id))) == 1


def test_changed_content_at_an_existing_path_is_rejected(
    session: Session,
    tmp_path: Path,
) -> None:
    quote = tmp_path / "설비 견적.xlsx"
    _write_quote(quote, item_name="FIRST")
    ingest_path(session, quote, root=tmp_path)
    _write_quote(quote, item_name="CHANGED")

    with pytest.raises(ValueError, match="content changed at immutable source path"):
        ingest_path(session, quote, root=tmp_path)

    assert session.scalar(select(func.count(SourceVariant.id))) == 1
    assert session.scalar(select(func.count(RawQuoteItem.id))) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows path identity")
def test_existing_path_identity_is_case_insensitive_on_windows(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quote_folder = tmp_path / "Quotes"
    quote_folder.mkdir()
    quote = quote_folder / "Quote.xlsx"
    _write_quote(quote, item_name="FIRST")
    monkeypatch.chdir(tmp_path)
    ingest_path(session, Path("Quotes/Quote.xlsx"))
    _write_quote(quote, item_name="CHANGED")

    with pytest.raises(ValueError, match="content changed at immutable source path"):
        ingest_path(session, Path("QUOTES/QUOTE.xlsx"))

    assert session.scalar(select(func.count(SourceVariant.id))) == 1


def test_duplicate_content_cannot_be_silently_attributed_to_another_document(
    session: Session,
    tmp_path: Path,
) -> None:
    first = tmp_path / "첫 견적.xlsx"
    second = tmp_path / "다른 견적.xlsx"
    _write_quote(first)
    second.write_bytes(first.read_bytes())
    ingest_path(session, first, root=tmp_path)

    with pytest.raises(
        ValueError,
        match="duplicate content belongs to another logical source",
    ):
        ingest_path(session, second, root=tmp_path)

    document = session.scalar(select(SourceDocument))
    assert document is not None
    assert document.logical_name == "첫 견적"
    assert session.scalar(select(func.count(SourceVariant.id))) == 1


def test_group_ingestion_registers_both_variants_but_parses_only_unlocked(
    session: Session,
    tmp_path: Path,
) -> None:
    original = tmp_path / "설비 견적.xlsx"
    unlocked = tmp_path / "설비 견적_보안해제.xlsx"
    _write_quote(original, item_name="LOCKED COPY")
    _write_quote(unlocked, item_name="UNLOCKED COPY")
    group = build_source_groups([original, unlocked], root=tmp_path)[0]

    preferred = ingest_group(session, group, root=tmp_path)

    variants = session.scalars(
        select(SourceVariant).order_by(SourceVariant.path)
    ).all()
    rows = session.scalars(select(RawQuoteItem)).all()
    assert preferred.path == "설비 견적_보안해제.xlsx"
    assert len(variants) == 2
    assert sum(variant.preferred_for_parsing for variant in variants) == 1
    assert len(rows) == 1
    assert rows[0].item_name_raw == "UNLOCKED COPY"
    assert rows[0].source_variant.path == "설비 견적_보안해제.xlsx"


def test_identical_group_content_is_stored_and_parsed_only_once(
    session: Session,
    tmp_path: Path,
) -> None:
    original = tmp_path / "동일 견적.xlsx"
    unlocked = tmp_path / "동일 견적_보안해제.xlsx"
    _write_quote(unlocked, item_name="SAME CONTENT")
    original.write_bytes(unlocked.read_bytes())
    group = build_source_groups([original, unlocked], root=tmp_path)[0]

    first = ingest_group(session, group, root=tmp_path)
    second = ingest_group(session, group, root=tmp_path)

    assert first.id == second.id
    assert first.path == "동일 견적_보안해제.xlsx"
    assert session.scalar(select(func.count(SourceVariant.id))) == 1
    assert session.scalar(select(func.count(RawQuoteItem.id))) == 1


def test_locked_then_unlocked_keeps_evidence_and_selects_unlocked(
    session: Session,
    tmp_path: Path,
) -> None:
    original = tmp_path / "설비 견적.xlsx"
    unlocked = tmp_path / "설비 견적_보안해제.xlsx"
    _write_quote(original, item_name="ORIGINAL EVIDENCE")
    _write_quote(unlocked, item_name="UNLOCKED EVIDENCE")

    original_variant = ingest_path(session, original, root=tmp_path)
    unlocked_variant = ingest_path(session, unlocked, root=tmp_path)

    document = session.scalar(select(SourceDocument))
    assert document is not None
    assert original_variant.document_id == unlocked_variant.document_id
    assert not original_variant.preferred_for_parsing
    assert unlocked_variant.preferred_for_parsing
    assert {row.item_name_raw for row in document.raw_items} == {
        "ORIGINAL EVIDENCE",
        "UNLOCKED EVIDENCE",
    }


def test_absolute_ingestion_requires_a_stable_containing_root(
    session: Session,
    tmp_path: Path,
) -> None:
    quote = tmp_path / "설비 견적.xlsx"
    _write_quote(quote)

    with pytest.raises(ValueError, match="explicit stable root"):
        ingest_path(session, quote)
    with pytest.raises(ValueError, match="outside the declared root"):
        ingest_path(session, quote, root=tmp_path / "other")

    assert session.scalar(select(func.count(SourceVariant.id))) == 0


def test_parse_errors_leave_no_partial_database_rows(
    session: Session,
    tmp_path: Path,
) -> None:
    corrupt = tmp_path / "손상 견적.xlsx"
    corrupt.write_bytes(b"not an xlsx archive")

    with pytest.raises(Exception):
        ingest_path(session, corrupt, root=tmp_path)

    assert session.scalar(select(func.count(SourceDocument.id))) == 0
    assert session.scalar(select(func.count(SourceVariant.id))) == 0
    assert session.scalar(select(func.count(RawQuoteItem.id))) == 0


def test_unsupported_extension_is_rejected_without_database_writes(
    session: Session,
    tmp_path: Path,
) -> None:
    unsupported = tmp_path / "견적서.csv"
    unsupported.write_text("품명,단가\nMOTOR,1000", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported quote extension"):
        ingest_path(session, unsupported, root=tmp_path)

    assert session.scalar(select(func.count(SourceDocument.id))) == 0


def test_xls_reader_uses_same_parsed_row_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeSheet:
        name = "내역"
        nrows = 2
        ncols = 3

        def cell_value(self, row: int, column: int) -> object:
            rows = [
                ["품명", "규격", "단가"],
                ["BEARING", "6204", 2400],
            ]
            return rows[row][column]

    class FakeBook:
        def sheets(self) -> list[FakeSheet]:
            return [FakeSheet()]

    monkeypatch.setattr(
        "app.ingestion.readers.xlrd.open_workbook",
        lambda _: FakeBook(),
    )
    quote = tmp_path / "legacy.xls"
    quote.write_bytes(b"fixture")

    rows = read_quote(quote)

    assert rows == [
        ParsedRow(
            sheet="내역",
            page=None,
            row=2,
            cells="A2:C2",
            item_name="BEARING",
            spec="6204",
            unit=None,
            quantity=None,
            unit_price="2400",
            amount=None,
            maker=None,
        )
    ]


def test_pdf_reader_records_page_provenance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakePage:
        def extract_text(self) -> str:
            return "품명  규격  단가\nBEARING  6204  2400"

    class FakePdf:
        pages = [FakePage()]

    monkeypatch.setattr("app.ingestion.readers.PdfReader", lambda _: FakePdf())
    quote = tmp_path / "quote.pdf"
    quote.write_bytes(b"fixture")

    rows = read_quote(quote)

    assert rows[0].sheet is None
    assert rows[0].page == 1
    assert rows[0].row is None
    assert rows[0].cells is None
    assert rows[0].item_name == "BEARING"
    assert rows[0].unit_price == "2400"
