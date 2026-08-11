from __future__ import annotations

import json
import os
import subprocess
import zipfile
import zlib
from pathlib import Path

import pytest
from openpyxl import Workbook
from PIL import Image
from pypdf import PdfWriter
from sqlalchemy import create_engine, func, inspect as sa_inspect, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import RawQuoteItem, SourceDocument, SourceVariant
from app.db.sqlite import configure_sqlite
from app.ingestion.readers import (
    OcrUnavailableError,
    ParsedRow,
    UnsafeQuoteFileError,
    _OcrRuntime,
    _parse_legacy_pdf_lines,
    _parse_ocr_text,
    _parse_wia_pdf_table,
    read_quote,
    read_xlsx,
)
from app.ingestion.service import (
    SourceFileChangedError,
    UnsupportedQuoteLayoutError,
    ingest_group,
    ingest_path,
    parsing_variant_for,
    preferred_variant_for,
)
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


def _write_layout_fixture(
    path: Path,
    fixture_name: str,
) -> None:
    fixtures_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "parser_layouts.json"
    )
    fixture = json.loads(fixtures_path.read_text(encoding="utf-8"))[
        fixture_name
    ]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = fixture["sheet"]
    for row in fixture["rows"]:
        sheet.append(row)
    workbook.save(path)


@pytest.mark.parametrize(
    ("fixture_name", "expected_item"),
    [
        ("standard", "SERVO MOTOR"),
        ("assembly_device", "ROBOT"),
        ("assembly_content", "SAFETY FENCE"),
        ("assembly_fallback", "CONTROL PANEL"),
    ],
)
def test_verified_legacy_layouts_do_not_silently_return_zero_rows(
    tmp_path: Path,
    fixture_name: str,
    expected_item: str,
) -> None:
    quote = tmp_path / f"{fixture_name}.xlsx"
    _write_layout_fixture(quote, fixture_name)

    rows = read_quote(quote)

    assert len(rows) == 1
    assert rows[0].item_name == expected_item
    assert rows[0].sheet is not None
    assert rows[0].row == 3


def test_headerless_fixed_column_fallback_is_auditable(
    session: Session,
    tmp_path: Path,
) -> None:
    quote = tmp_path / "headerless.xlsx"
    _write_layout_fixture(quote, "headerless_fixed_columns")

    rows = read_quote(quote)

    assert [row.item_name for row in rows] == ["PHOTO SENSOR", "BEARING"]
    assert rows[0].quantity == "2"
    assert rows[0].unit == "EA"
    assert rows[0].unit_price == "11100"
    assert rows[0].row == 2
    assert rows[0].cells == "C2:H2"
    assert rows[0].warnings == (
        "FALLBACK_FIXED_C_E_F_H",
        "SPEC_COLUMN_NOT_FOUND",
    )
    variant = ingest_path(session, quote, root=tmp_path)
    assert json.loads(variant.raw_items[0].parse_warnings_json) == [
        "FALLBACK_FIXED_C_E_F_H",
        "SPEC_COLUMN_NOT_FOUND",
    ]


def test_device_and_content_columns_preserve_content_as_spec(
    tmp_path: Path,
) -> None:
    quote = tmp_path / "device-content.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "10-2"
    sheet.append(["위치", "장치", "내용", "단위", "수량", "단가", "금액"])
    sheet.append(["A", "(NU, G1)", "FRAME/SUPPORT", "SET", 1, 6000000, 6000000])
    workbook.save(quote)

    rows = read_xlsx(quote)

    assert len(rows) == 1
    assert rows[0].item_name == "(NU, G1)"
    assert rows[0].spec == "FRAME/SUPPORT"
    assert rows[0].unit == "SET"


def test_labor_description_is_not_accepted_as_maker(tmp_path: Path) -> None:
    quote = tmp_path / "maker.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["품명", "규격", "단위", "수량", "단가", "금액", "제조사"])
    sheet.append(["설치", "현장", "식", 1, 1000000, 1000000, "1인x7일(이동일 제외)"])
    workbook.save(quote)

    rows = read_xlsx(quote)

    assert len(rows) == 1
    assert rows[0].maker is None
    assert rows[0].warnings == ("MAKER_REJECTED_NON_BRAND",)


def test_blank_spec_is_distinguished_from_missing_spec_column(tmp_path: Path) -> None:
    quote = tmp_path / "blank-spec.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["품명", "규격", "단위", "수량", "단가", "금액"])
    sheet.append(["FRAME", None, "SET", 1, 1000000, 1000000])
    workbook.save(quote)

    rows = read_xlsx(quote)

    assert len(rows) == 1
    assert rows[0].spec is None
    assert rows[0].warnings == ("SOURCE_SPEC_BLANK",)


def test_cjk_quote_headers_are_supported_and_derived_price_needs_review(
    tmp_path: Path,
) -> None:
    quote = tmp_path / "cjk-header.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["品名", "規格", "數量", "單位", "單價", "金額"])
    sheet.append(["MOTOR", "5KW", 2, "EA", None, 1000000])
    workbook.save(quote)

    rows = read_xlsx(quote)

    assert len(rows) == 1
    assert rows[0].item_name == "MOTOR"
    assert rows[0].spec == "5KW"
    assert rows[0].unit_price == "500000"
    assert rows[0].amount == "1000000"
    assert "CJK_HEADER_TABLE" in rows[0].warnings
    assert "DERIVED_UNIT_PRICE" in rows[0].warnings
    assert "PARSER_SOURCE_REVIEW_REQUIRED" in rows[0].warnings


def test_bilingual_pdf_style_headers_are_recognized_in_one_band(
    tmp_path: Path,
) -> None:
    quote = tmp_path / "bilingual-header.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "품 명\nDescription.",
            "규 격\nType",
            "수 량\nQty",
            "단 위\nUnit",
            "단 가\nUnit Price",
            "금 액\nTotal Price",
        ]
    )
    sheet.append(["BEARING", "6204", 2, "EA", 2400, 4800])
    workbook.save(quote)

    rows = read_xlsx(quote)

    assert len(rows) == 1
    assert rows[0].item_name == "BEARING"
    assert rows[0].spec == "6204"
    assert rows[0].unit_price == "2400"


def test_legacy_pdf_line_parser_is_review_only_and_rejects_totals() -> None:
    rows = _parse_legacy_pdf_lines(
        "1 MOTOR AC220V 2 EA 500,000 1,000,000\n"
        "TOTAL 1 LOT 1,000,000",
        page=2,
    )

    assert len(rows) == 1
    assert rows[0].item_name == "MOTOR AC220V"
    assert rows[0].unit_price == "500000"
    assert rows[0].amount == "1000000"
    assert "PARSER_SOURCE_REVIEW_REQUIRED" in rows[0].warnings


def test_quote_zip_is_bounded_and_preserves_member_provenance(
    tmp_path: Path,
) -> None:
    member = tmp_path / "member.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "items"
    sheet.append(["item", "spec", "unit", "qty", "unitprice", "amount"])
    sheet.append(["SENSOR", "PNP", "EA", 2, 11100, 22200])
    workbook.save(member)
    quote = tmp_path / "quotes.zip"
    with zipfile.ZipFile(quote, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(member, "vendor/quote.xlsx")

    rows = read_quote(quote)

    assert len(rows) == 1
    assert rows[0].sheet == "vendor/quote.xlsx::items"
    assert rows[0].item_name == "SENSOR"
    assert rows[0].warnings[:2] == (
        "ARCHIVE_MEMBER",
        "ARCHIVE_MEMBER:vendor/quote.xlsx",
    )


def test_quote_zip_rejects_path_traversal_before_member_extraction(
    tmp_path: Path,
) -> None:
    quote = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(quote, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("../outside.xlsx", b"not a workbook")

    with pytest.raises(UnsafeQuoteFileError, match="path is unsafe"):
        read_quote(quote)


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


def test_duplicate_content_at_different_paths_preserves_both_evidence_records(
    session: Session,
    tmp_path: Path,
) -> None:
    first = tmp_path / "첫 견적.xlsx"
    second = tmp_path / "다른 견적.xlsx"
    _write_quote(first)
    second.write_bytes(first.read_bytes())
    ingest_path(session, first, root=tmp_path)
    ingest_path(session, second, root=tmp_path)

    assert session.scalar(select(func.count(SourceDocument.id))) == 2
    assert session.scalar(select(func.count(SourceVariant.id))) == 2
    assert session.scalar(select(func.count(RawQuoteItem.id))) == 2


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
    assert sum(
        variant.selected_for_parsing_at_ingest
        for variant in variants
    ) == 1
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
    assert session.scalar(select(func.count(SourceVariant.id))) == 2
    assert session.scalar(select(func.count(RawQuoteItem.id))) == 1
    assert {variant.path for variant in first.document.variants} == {
        "동일 견적.xlsx",
        "동일 견적_보안해제.xlsx",
    }


def test_byte_identical_locked_then_unlocked_retains_unlocked_precedence(
    session: Session,
    tmp_path: Path,
) -> None:
    original = tmp_path / "동일 순차 견적.xlsx"
    unlocked = tmp_path / "동일 순차 견적_보안해제.xlsx"
    _write_quote(original, item_name="SHARED ROW")
    unlocked.write_bytes(original.read_bytes())

    original_variant = ingest_path(session, original, root=tmp_path)
    unlocked_variant = ingest_path(session, unlocked, root=tmp_path)

    assert original_variant.id != unlocked_variant.id
    assert original_variant.path == "동일 순차 견적.xlsx"
    assert unlocked_variant.path == "동일 순차 견적_보안해제.xlsx"
    assert original_variant.security_state == "UNKNOWN"
    assert unlocked_variant.security_state == "UNLOCKED"
    assert not original_variant.selected_for_parsing_at_ingest
    assert unlocked_variant.selected_for_parsing_at_ingest
    assert session.scalar(select(func.count(RawQuoteItem.id))) == 1
    assert preferred_variant_for(unlocked_variant.document) is unlocked_variant
    assert parsing_variant_for(session, unlocked_variant) is original_variant


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
    assert not original_variant.selected_for_parsing_at_ingest
    assert unlocked_variant.selected_for_parsing_at_ingest
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


def test_supported_file_with_unknown_layout_rolls_back_visibly(
    session: Session,
    tmp_path: Path,
) -> None:
    quote = tmp_path / "unknown-layout.xlsx"
    workbook = Workbook()
    workbook.active.append(["알 수 없는", "양식"])
    workbook.save(quote)

    with pytest.raises(
        UnsupportedQuoteLayoutError,
        match="no quote rows matched",
    ):
        ingest_path(session, quote, root=tmp_path)

    assert session.scalar(select(func.count(SourceDocument.id))) == 0
    assert session.scalar(select(func.count(SourceVariant.id))) == 0


def test_file_swap_between_hash_and_parse_rolls_back_all_ingestion(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quote = tmp_path / "changing.xlsx"
    _write_quote(quote, item_name="BEFORE")
    from app.ingestion import service as ingestion_service

    actual_reader = ingestion_service.read_quote

    def read_then_replace(path: Path) -> list[ParsedRow]:
        rows = actual_reader(path)
        _write_quote(path, item_name="AFTER")
        return rows

    monkeypatch.setattr(ingestion_service, "read_quote", read_then_replace)

    with pytest.raises(SourceFileChangedError, match="changed while parsing"):
        ingest_path(session, quote, root=tmp_path)

    assert session.scalar(select(func.count(SourceDocument.id))) == 0
    assert session.scalar(select(func.count(SourceVariant.id))) == 0
    assert session.scalar(select(func.count(RawQuoteItem.id))) == 0


def test_ingestion_success_does_not_commit_unrelated_outer_work(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "transaction-success.sqlite3"
    engine = configure_sqlite(
        create_engine(f"sqlite:///{database_path.as_posix()}")
    )
    Base.metadata.create_all(engine)
    quote = tmp_path / "quote.xlsx"
    _write_quote(quote)
    with Session(engine, expire_on_commit=False) as session:
        unrelated = SourceDocument(logical_name="unrelated-pending")
        session.add(unrelated)

        ingest_path(session, quote, root=tmp_path)

        assert session.in_transaction()
        assert sa_inspect(unrelated).persistent
        with Session(engine) as observer:
            assert observer.scalar(
                select(func.count(SourceDocument.id))
            ) == 0
        session.commit()
    with Session(engine) as observer:
        assert observer.scalar(select(func.count(SourceDocument.id))) == 2


def test_ingestion_failure_rolls_back_only_its_savepoint(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "transaction-failure.sqlite3"
    engine = configure_sqlite(
        create_engine(f"sqlite:///{database_path.as_posix()}")
    )
    Base.metadata.create_all(engine)
    quote = tmp_path / "unknown.xlsx"
    workbook = Workbook()
    workbook.active.append(["unknown"])
    workbook.save(quote)
    with Session(engine, expire_on_commit=False) as session:
        unrelated = SourceDocument(logical_name="keep-me-pending")
        session.add(unrelated)

        with pytest.raises(UnsupportedQuoteLayoutError):
            ingest_path(session, quote, root=tmp_path)

        assert session.in_transaction()
        assert sa_inspect(unrelated).persistent
        assert session.scalar(select(func.count(SourceDocument.id))) == 1
        assert session.scalar(select(func.count(SourceVariant.id))) == 0
        with Session(engine) as observer:
            assert observer.scalar(
                select(func.count(SourceDocument.id))
            ) == 0
        session.commit()
    with Session(engine) as observer:
        assert observer.scalar(select(func.count(SourceDocument.id))) == 1


def test_clean_caller_rollback_removes_ingestion_after_savepoint(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "clean-rollback.sqlite3"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    configure_sqlite(engine)
    configure_sqlite(engine)
    Base.metadata.create_all(engine)
    quote = tmp_path / "rollback-quote.xlsx"
    _write_quote(quote)
    with engine.connect() as connection:
        assert connection.connection.driver_connection.isolation_level is None
        assert connection.exec_driver_sql(
            "PRAGMA foreign_keys"
        ).scalar_one() == 1

    with Session(engine) as session:
        ingest_path(session, quote, root=tmp_path)
        session.rollback()

    with Session(engine) as observer:
        assert observer.scalar(select(func.count(SourceDocument.id))) == 0
        assert observer.scalar(select(func.count(SourceVariant.id))) == 0
        assert observer.scalar(select(func.count(RawQuoteItem.id))) == 0


def test_clean_caller_commit_persists_ingestion_after_savepoint(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "clean-commit.sqlite3"
    engine = configure_sqlite(
        create_engine(f"sqlite:///{database_path.as_posix()}")
    )
    Base.metadata.create_all(engine)
    quote = tmp_path / "commit-quote.xlsx"
    _write_quote(quote)

    with Session(engine) as session:
        ingest_path(session, quote, root=tmp_path)
        session.commit()

    with Session(engine) as observer:
        assert observer.scalar(select(func.count(SourceDocument.id))) == 1
        assert observer.scalar(select(func.count(SourceVariant.id))) == 1
        assert observer.scalar(select(func.count(RawQuoteItem.id))) == 1


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


def test_ocr_text_reuses_header_parser_and_marks_provenance() -> None:
    rows = _parse_ocr_text(
        "item  spec  unit  qty  unitprice  amount\n"
        "SERVO MOTOR  AC220V  EA  2  500000  1000000",
        page=3,
    )

    assert rows == [
        ParsedRow(
            sheet=None,
            page=3,
            row=None,
            cells=None,
            item_name="SERVO MOTOR",
            spec="AC220V",
            unit="EA",
            quantity="2",
            unit_price="500000",
            amount="1000000",
            maker=None,
            warnings=("OCR_SOURCE", "OCR_REVIEW_REQUIRED"),
        )
    ]


def test_blank_pdf_uses_bounded_ocr_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.ingestion import readers

    quote = tmp_path / "image-quote.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    with quote.open("wb") as stream:
        writer.write(stream)

    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    (tessdata / "kor.traineddata").write_bytes(b"fixture")
    (tessdata / "eng.traineddata").write_bytes(b"fixture")
    runtime = _OcrRuntime(
        tesseract=Path("tesseract.exe"),
        renderer=Path("pdftoppm.exe"),
        languages=("kor", "eng"),
        tessdata_sources=(tessdata,),
    )
    monkeypatch.setattr(readers, "_resolve_ocr_runtime", lambda **_: runtime)

    def fake_command(
        command: list[str],
        *,
        error_message: str,
    ) -> subprocess.CompletedProcess[str]:
        del error_message
        if command[0] == "pdftoppm.exe":
            Image.new("RGB", (100, 100), "white").save(
                Path(command[-1]).with_suffix(".png")
            )
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(
            command,
            0,
            "item  spec  unit  qty  unitprice  amount\n"
            "BEARING  6204  EA  1  2400  2400",
            "",
        )

    monkeypatch.setattr(readers, "_run_command", fake_command)

    rows = read_quote(quote)

    assert len(rows) == 1
    assert rows[0].item_name == "BEARING"
    assert rows[0].page == 1
    assert rows[0].warnings == ("OCR_SOURCE", "OCR_REVIEW_REQUIRED")


def test_pdf_ocr_page_cap_is_checked_before_runtime_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.ingestion import readers

    quote = tmp_path / "many-image-pages.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.add_blank_page(width=300, height=300)
    with quote.open("wb") as stream:
        writer.write(stream)
    monkeypatch.setattr(readers, "MAX_OCR_PAGES", 1)

    def must_not_resolve(**_: object) -> object:
        raise AssertionError("OCR runtime must not be resolved")

    monkeypatch.setattr(readers, "_resolve_ocr_runtime", must_not_resolve)

    with pytest.raises(UnsafeQuoteFileError, match="OCR page"):
        read_quote(quote)


def test_image_ocr_unavailable_is_explicit_and_non_destructive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.ingestion import readers

    quote = tmp_path / "quote.jpg"
    Image.new("RGB", (100, 100), "white").save(quote)

    def unavailable(**_: object) -> object:
        raise OcrUnavailableError("OCR_UNAVAILABLE")

    monkeypatch.setattr(readers, "_resolve_ocr_runtime", unavailable)

    with pytest.raises(OcrUnavailableError, match="OCR_UNAVAILABLE"):
        read_quote(quote)
    assert quote.is_file()
    assert quote.stat().st_size > 0


def test_jpeg_quote_uses_same_ocr_header_parser(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.ingestion import readers

    quote = tmp_path / "quote.jpeg"
    Image.new("RGB", (100, 100), "white").save(quote)
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    (tessdata / "kor.traineddata").write_bytes(b"fixture")
    (tessdata / "eng.traineddata").write_bytes(b"fixture")
    runtime = _OcrRuntime(
        tesseract=Path("tesseract.exe"),
        renderer=None,
        languages=("kor", "eng"),
        tessdata_sources=(tessdata,),
    )
    monkeypatch.setattr(readers, "_resolve_ocr_runtime", lambda **_: runtime)
    monkeypatch.setattr(
        readers,
        "_run_command",
        lambda command, **_: subprocess.CompletedProcess(
            command,
            0,
            "item  spec  unit  qty  unitprice  amount\n"
            "SENSOR  PNP  EA  2  11100  22200",
            "",
        ),
    )

    rows = read_quote(quote)

    assert len(rows) == 1
    assert rows[0].item_name == "SENSOR"
    assert rows[0].unit_price == "11100"
    assert rows[0].warnings == ("OCR_SOURCE", "OCR_REVIEW_REQUIRED")


def test_jpeg_resolution_is_bounded_before_ocr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.ingestion import readers

    quote = tmp_path / "large.jpg"
    Image.new("RGB", (101, 101), "white").save(quote)
    monkeypatch.setattr(readers, "MAX_OCR_PAGE_PIXELS", 10_000)

    def must_not_resolve(**_: object) -> object:
        raise AssertionError("OCR runtime must not be resolved")

    monkeypatch.setattr(readers, "_resolve_ocr_runtime", must_not_resolve)

    with pytest.raises(UnsafeQuoteFileError, match="resolution"):
        read_quote(quote)


def test_ocr_runtime_honors_configured_command_and_tessdata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.ingestion import readers

    executable = tmp_path / "tesseract.exe"
    executable.write_bytes(b"fixture")
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    (tessdata / "kor.traineddata").write_bytes(b"fixture")
    (tessdata / "eng.traineddata").write_bytes(b"fixture")
    monkeypatch.setenv("TESSERACT_CMD", str(executable))
    monkeypatch.setenv("TESSDATA_PREFIX", str(tessdata))
    monkeypatch.setenv("OCR_LANGUAGES", "kor+eng")

    runtime = readers._resolve_ocr_runtime(require_renderer=False)

    assert runtime.tesseract == executable
    assert runtime.languages == ("kor", "eng")
    assert runtime.tessdata_sources[0] == tessdata


def test_wia_pdf_table_reader_preserves_fields_and_page_provenance() -> None:
    table = [
        [
            "구분",
            "구분",
            "품 명",
            "규격",
            "단위",
            "수량",
            "단가(원)",
            "금액(원)",
            "原MAKER",
        ],
        ["", "", "HMI", "15인치", "EA", "2", "1,550,000", "3,100,000", ""],
        ["", "", "합계", "", "", "", "", "3,100,000", ""],
    ]

    rows = _parse_wia_pdf_table(
        table,
        page=4,
        unit_name="전기부문(공통)",
    )

    assert rows == [
        ParsedRow(
            sheet=None,
            page=4,
            row=None,
            cells=None,
            item_name="HMI",
            spec="15인치",
            unit="EA",
            quantity="2",
            unit_price="1,550,000",
            amount="3,100,000",
            maker=None,
            warnings=("PDF_WIA_TABLE", "UNIT_SECTION:전기부문(공통)"),
        )
    ]


def test_pdf_reader_rejects_page_and_extracted_text_limits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.ingestion import readers

    class FakePage:
        def extract_text(self) -> str:
            return "abcdef"

    class FakePdf:
        pages = [FakePage(), FakePage()]

    monkeypatch.setattr(readers, "PdfReader", lambda _: FakePdf())
    quote = tmp_path / "bounded.pdf"
    quote.write_bytes(b"fixture")
    monkeypatch.setattr(readers, "MAX_PDF_PAGES", 1)
    with pytest.raises(UnsafeQuoteFileError):
        read_quote(quote)

    monkeypatch.setattr(readers, "MAX_PDF_PAGES", 2)
    monkeypatch.setattr(readers, "MAX_PDF_EXTRACTED_TEXT_CHARS", 5)
    with pytest.raises(UnsafeQuoteFileError):
        read_quote(quote)


def test_xlsx_reader_rejects_unknown_dimensions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.ingestion import readers

    class FakeSheet:
        max_row = None
        max_column = None

    class FakeWorkbook:
        worksheets = [FakeSheet()]

        def close(self) -> None:
            pass

    monkeypatch.setattr(readers, "_validate_xlsx_archive", lambda _: None)
    monkeypatch.setattr(
        readers,
        "load_workbook",
        lambda *args, **kwargs: FakeWorkbook(),
    )

    with pytest.raises(UnsafeQuoteFileError):
        read_xlsx(tmp_path / "unknown-dimensions.xlsx")


def test_pdf_flate_decode_is_bounded_and_other_filters_are_rejected() -> None:
    from pypdf.generic import EncodedStreamObject, NameObject

    from app.ingestion import readers

    compressed = zlib.compress(b"A" * 100)
    assert readers._bounded_flate_size(compressed, 100) == 100
    with pytest.raises(UnsafeQuoteFileError):
        readers._bounded_flate_size(compressed, 99)

    stream = EncodedStreamObject()
    stream._data = b"encoded"
    stream[NameObject("/Filter")] = NameObject("/LZWDecode")

    class FakePage:
        def raw_get(self, name):
            return stream

    with pytest.raises(UnsafeQuoteFileError):
        readers._bounded_pdf_page_content(
            FakePage(),
            decoded_remaining=100,
        )


def test_pdf_rejects_runlength_tounicode_before_decoder_is_invoked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from pypdf.generic import (
        DictionaryObject,
        EncodedStreamObject,
        NameObject,
    )

    from app.ingestion import readers

    to_unicode = EncodedStreamObject()
    to_unicode._data = b"\x80A\x80B\x80C"
    to_unicode[NameObject("/Filter")] = NameObject("/RunLengthDecode")
    font = DictionaryObject(
        {NameObject("/ToUnicode"): to_unicode}
    )
    resources = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font}
            )
        }
    )
    decoder_called = False

    class FakePage:
        def raw_get(self, name):
            if name == "/Resources":
                return resources
            raise KeyError(name)

        def extract_text(self) -> str:
            nonlocal decoder_called
            decoder_called = True
            to_unicode.get_data()
            return ""

    class FakePdf:
        pages = [FakePage()]

    monkeypatch.setattr(readers, "PdfReader", lambda _: FakePdf())
    quote = tmp_path / "resource-bomb.pdf"
    quote.write_bytes(b"fixture")

    with pytest.raises(UnsafeQuoteFileError):
        read_quote(quote)

    assert not decoder_called


def test_pdf_allows_bounded_dct_image_that_text_extraction_skips(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from pypdf.generic import (
        DictionaryObject,
        EncodedStreamObject,
        NameObject,
        NumberObject,
    )

    from app.ingestion import readers

    image = EncodedStreamObject()
    image._data = b"bounded-jpeg"
    image[NameObject("/Subtype")] = NameObject("/Image")
    image[NameObject("/Filter")] = NameObject("/DCTDecode")
    image[NameObject("/Width")] = NumberObject(100)
    image[NameObject("/Height")] = NumberObject(80)
    resources = DictionaryObject(
        {
            NameObject("/XObject"): DictionaryObject(
                {NameObject("/Im1"): image}
            )
        }
    )

    class FakePage:
        def raw_get(self, name):
            if name == "/Resources":
                return resources
            raise KeyError(name)

        def extract_text(self) -> str:
            return ""

    class FakePdf:
        pages = [FakePage()]

    monkeypatch.setattr(readers, "PdfReader", lambda _: FakePdf())
    monkeypatch.setattr(readers, "_read_pdf_with_ocr", lambda *_: [])
    quote = tmp_path / "bounded-image.pdf"
    quote.write_bytes(b"fixture")

    assert read_quote(quote) == []


def test_pdf_raw_preflight_rejects_escaped_runlength_before_reader(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from pypdf import filters

    from app.ingestion import readers

    reader_called = False
    decoder_called = False

    def fail_reader(*args, **kwargs):
        nonlocal reader_called
        reader_called = True
        raise AssertionError("PdfReader must not be constructed")

    def fail_decoder(*args, **kwargs):
        nonlocal decoder_called
        decoder_called = True
        raise AssertionError("RunLength decoder must not run")

    monkeypatch.setattr(readers, "PdfReader", fail_reader)
    monkeypatch.setattr(filters.RunLengthDecode, "decode", fail_decoder)
    attack = tmp_path / "object-stream-attack.pdf"
    attack.write_bytes(
        b"%PDF-1.7\n"
        b"1 0 obj\n"
        b"<< /Type /ObjStm /N 1 /First 4 "
        b"/Filter /Run#4cengthDecode /Length 7 >>\n"
        b"stream\n\x80A\x80B\nendstream\nendobj\n%%EOF\n"
    )

    with pytest.raises(UnsafeQuoteFileError):
        read_quote(attack)

    assert not reader_called
    assert not decoder_called


def test_pdf_raw_preflight_ignores_filter_text_in_comments_and_strings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.ingestion import readers

    class FakePdf:
        pages = []

    monkeypatch.setattr(readers, "PdfReader", lambda _: FakePdf())
    quote = tmp_path / "lexical-comments.pdf"
    quote.write_bytes(
        b"%PDF-1.7\n"
        b"% /Filter /DCTDecode\n"
        b"1 0 obj << /Note (/Filter /DCTDecode) "
        b"/Filter /FlateDecode >> endobj\n%%EOF\n"
    )

    assert read_quote(quote) == []


def test_pdf_raw_preflight_rejects_filter_chains_and_token_overflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.ingestion import readers

    chain = tmp_path / "filter-chain.pdf"
    chain.write_bytes(
        b"%PDF-1.7\n"
        b"1 0 obj << /Filter [/ASCII85Decode /FlateDecode] >> endobj\n"
    )
    with pytest.raises(UnsafeQuoteFileError):
        read_quote(chain)

    monkeypatch.setattr(readers, "MAX_PDF_LEXICAL_TOKENS", 5)
    overflow = tmp_path / "token-overflow.pdf"
    overflow.write_bytes(b"%PDF-1.7\n1 2 3 4 5 6 7\n")
    with pytest.raises(UnsafeQuoteFileError):
        read_quote(overflow)


def test_pdf_raw_preflight_rejects_cr_only_hidden_filter_before_reader(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.ingestion import readers

    reader_called = False

    def fail_reader(*args, **kwargs):
        nonlocal reader_called
        reader_called = True
        raise AssertionError("PdfReader must not be constructed")

    monkeypatch.setattr(readers, "PdfReader", fail_reader)
    attack = tmp_path / "cr-comment-attack.pdf"
    attack.write_bytes(
        b"%PDF-1.7\r"
        b"% harmless comment\r"
        b"1 0 obj << /Filter /LZWDecode >> endobj\r%%EOF\r"
    )

    with pytest.raises(UnsafeQuoteFileError):
        read_quote(attack)
    assert not reader_called


def test_pdf_raw_preflight_does_not_skip_orphan_stream_decoy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.ingestion import readers

    reader_called = False

    def fail_reader(*args, **kwargs):
        nonlocal reader_called
        reader_called = True
        raise AssertionError("PdfReader must not be constructed")

    monkeypatch.setattr(readers, "PdfReader", fail_reader)
    attack = tmp_path / "orphan-stream-decoy.pdf"
    attack.write_bytes(
        b"%PDF-1.7\nstream\n"
        b"2 0 obj << /Type /ObjStm "
        b"/Filter /Run#4cengthDecode >> endobj "
        b"binary-endstream-decoy endstream\n"
    )

    with pytest.raises(UnsafeQuoteFileError):
        read_quote(attack)
    assert not reader_called


def test_pdf_raw_preflight_rejects_indirect_filter_and_false_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.ingestion import readers

    reader_called = False

    def fail_reader(*args, **kwargs):
        nonlocal reader_called
        reader_called = True
        raise AssertionError("PdfReader must not be constructed")

    monkeypatch.setattr(readers, "PdfReader", fail_reader)
    attack = tmp_path / "indirect-filter.pdf"
    attack.write_bytes(
        b"%PDF-1.7\n"
        b"1 0 obj << /Length 30 >> stream\n"
        b"abc /Filter 9 0 R "
        b"endstream false-boundary endstream\nendobj\n"
    )

    with pytest.raises(UnsafeQuoteFileError):
        read_quote(attack)
    assert not reader_called


def test_pdf_raw_name_limit_rejects_before_contextual_lexing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.ingestion import readers

    monkeypatch.setattr(readers, "MAX_PDF_LEXICAL_NAMES", 3)
    raw_names = readers._raw_pdf_name_tokens(b"/a /b")
    assert iter(raw_names) is raw_names

    def fail_lexer(*args, **kwargs):
        raise AssertionError("contextual lexer must not be reached")

    monkeypatch.setattr(readers, "_pdf_lexical_tokens", fail_lexer)
    attack = tmp_path / "many-raw-names.pdf"
    attack.write_bytes(b"%PDF-1.7\n" + (b"/a " * 4))

    with pytest.raises(UnsafeQuoteFileError, match="too many raw names"):
        read_quote(attack)


def test_pdf_raw_preflight_caps_combined_dictionary_and_array_depth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.ingestion import readers

    class FakePdf:
        pages = []

    monkeypatch.setattr(readers, "MAX_PDF_LEXICAL_DEPTH", 2)
    monkeypatch.setattr(readers, "PdfReader", lambda _: FakePdf())
    attack = tmp_path / "combined-depth.pdf"
    attack.write_bytes(
        b"%PDF-1.7\n"
        b"1 0 obj << /Kids [[ ]] >> endobj\n%%EOF\n"
    )

    with pytest.raises(UnsafeQuoteFileError, match="nesting is too deep"):
        read_quote(attack)


def test_rows_without_item_name_are_skipped_even_when_other_fields_present(
    tmp_path: Path,
) -> None:
    """
    금액만 있고 품명이 빈칸인 행(예: 미기입 템플릿 행, 소계 행)은
    파싱 결과에 포함되지 않아야 한다.
    """
    wb = Workbook()
    ws = wb.active
    ws.append(["구분", "", "품  명", "규격", "단위", "수량", "단가(원)", "금액(원)", "원MAKER"])
    ws.append(["자재비", "철자재", "BASE FRAME", "S45C", "KG", "100", "2200", "220000", ""])
    ws.append(["", "", "", "", "", "", "", "0", ""])  # 품명 없음, 금액=0
    ws.append(["", "", "", "", "", "", "", "500000", ""])  # 품명 없음, 금액 있음
    ws.append(["", "", "SAFETY COVER", "SS400", "EA", "2", "15000", "30000", ""])

    quote = tmp_path / "template_with_empty_rows.xlsx"
    wb.save(quote)

    rows = read_quote(quote)
    item_names = [r.item_name for r in rows]

    assert all(name for name in item_names), (
        f"품명 없는 행이 파싱됨: {item_names}"
    )
    assert "BASE FRAME" in item_names
    assert "SAFETY COVER" in item_names
    assert len(rows) == 2, f"품명 있는 행만 2개여야 함, got {len(rows)}: {item_names}"


def test_wia_original_maker_column_is_preserved(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "단위장비4"
    sheet.append(["품명", "규격", "단위", "수량", "단가", "금액", "原MAKER"])
    sheet.append(
        ["2분력계", "5000NM/10000N", "SET", 2, 47000000, 94000000, "DEVELOPMENT"]
    )
    quote = tmp_path / "wia-maker.xlsx"
    workbook.save(quote)

    rows = read_quote(quote)

    assert len(rows) == 1
    assert rows[0].maker == "DEVELOPMENT"
    assert rows[0].cells == "A2:G2"


def test_summary_and_merged_pdf_rows_are_not_auto_included() -> None:
    from app.ingestion import readers

    rows = readers._parse_tabular_rows(
        [
            ["Description", "Qty", "Unit Price", "Total Price"],
            ["Labor Cost Sub Total (4)", "1", "1000000", "1000000"],
            ["TOTAL AMOUNT", "1", "580000000", "580000000"],
            [
                "1. MOTOR\n2. BEARING",
                "1",
                "500000",
                "500000",
            ],
        ],
        sheet=None,
        page=1,
        row_numbers=False,
        cell_ranges=False,
        require_price=True,
        extra_warnings=("PDF_COORDINATE_TABLE",),
    )

    assert len(rows) == 1
    assert rows[0].item_name == "1. MOTOR\n2. BEARING"
    assert "MULTI_ITEM_BLOCK" in rows[0].warnings
    assert "PARSER_SOURCE_REVIEW_REQUIRED" in rows[0].warnings


def test_coordinate_pdf_without_spec_column_requires_review() -> None:
    from app.ingestion import readers

    rows = readers._parse_tabular_rows(
        [
            ["Description", "Qty", "Unit Price", "Total Price"],
            ["Mitsubishi", "1", "4000000", "4000000"],
        ],
        sheet=None,
        page=2,
        row_numbers=False,
        cell_ranges=False,
        require_price=True,
        extra_warnings=("PDF_COORDINATE_TABLE",),
    )

    assert len(rows) == 1
    assert "SPEC_COLUMN_NOT_FOUND" in rows[0].warnings
    assert "PARSER_SOURCE_REVIEW_REQUIRED" in rows[0].warnings


def test_품목내역_header_is_recognized_and_subheader_exchange_rate_column_ignored(
    tmp_path: Path,
) -> None:
    """
    파일에 '품목내역' 헤더가 있고 아래 서브헤더 행에 '환율/역률'이 있을 때,
    item_name 이 환율 수치가 아닌 실제 품목명 컬럼으로 매핑되어야 한다.
    """
    wb = Workbook()
    ws = wb.active

    # 헤더 행: B=번호, C=품목내역, I=합  계
    ws.append([])  # row 1: empty
    ws.append(["", "번호", "품목내역", "", "단가금액(천원)", "", "", "", "합  계"])
    # 서브헤더 행: G=환율/역률, H=합  계
    ws.append(["", "", "", "", "원가분", "환율이분", "환율/역률\n(환율환산)", "합  계"])
    # 데이터 행
    ws.append(["", "1", "SERVO MOTOR", "", 68745.81, 198500.0, 101.614, 83788.57])
    ws.append(["", "2", "BEARING", "", 12000.0, 34000.0, 17.755, 14300.0])

    quote = tmp_path / "품목내역_layout.xlsx"
    wb.save(quote)

    rows = read_quote(quote)
    named_rows = [r for r in rows if r.item_name is not None]

    assert len(named_rows) >= 2, f"expected ≥2 named rows, got {len(named_rows)}: {named_rows}"
    item_names = [r.item_name for r in named_rows]
    assert "SERVO MOTOR" in item_names, f"expected 'SERVO MOTOR' in {item_names}"
    assert "BEARING" in item_names, f"expected 'BEARING' in {item_names}"
    for r in named_rows:
        try:
            float(r.item_name)
            raise AssertionError(
                f"item_name '{r.item_name}' is a float — exchange rate column misidentified"
            )
        except ValueError:
            pass  # non-numeric name is correct
