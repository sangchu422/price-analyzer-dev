"""Reader-neutral extraction of quote rows with source provenance."""

from __future__ import annotations

import re
import os
import shutil
import subprocess
import tempfile
import threading
import zipfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any

import xlrd
import pdfplumber
from PIL import Image
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from pypdf import PdfReader
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    IndirectObject,
    StreamObject,
)


SUPPORTED_QUOTE_EXTENSIONS = frozenset(
    {".xlsx", ".xls", ".pdf", ".jpg", ".jpeg", ".zip"}
)
MAX_XLSX_ARCHIVE_ENTRIES = 5_000
MAX_XLSX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_XLSX_COMPRESSION_RATIO = 200
MAX_XLSX_WORKSHEETS = 200
MAX_XLSX_WORKSHEET_ROWS = 200_000
MAX_XLSX_WORKSHEET_COLUMNS = 500
MAX_XLSX_WORKSHEET_CELLS = 1_000_000
MAX_XLSX_TOTAL_CELLS = 1_000_000
MAX_XLS_SHEETS = 200
MAX_XLS_SHEET_ROWS = 200_000
MAX_XLS_SHEET_COLUMNS = 500
MAX_XLS_SHEET_CELLS = 2_000_000
MAX_XLS_TOTAL_CELLS = 5_000_000
MAX_PDF_PAGES = 200
MAX_PDF_COMPRESSED_CONTENT_BYTES = 25 * 1024 * 1024
MAX_PDF_DECODED_CONTENT_BYTES = 32 * 1024 * 1024
MAX_PDF_EXTRACTED_TEXT_CHARS = 5_000_000
MAX_PDF_EXTRACTED_ROWS = 200_000
MAX_PDF_TABLES = 1_000
MAX_PDF_TABLE_ROWS = 200_000
MAX_PDF_TABLE_CELLS = 1_000_000
MAX_PDF_TOTAL_FLATE_DECODED_BYTES = 32 * 1024 * 1024
MAX_PDF_REACHABLE_OBJECTS = 50_000
MAX_PDF_RESOURCE_DEPTH = 50
MAX_PDF_IMAGE_COUNT = 1_000
MAX_PDF_IMAGE_RAW_BYTES = 16 * 1024 * 1024
MAX_PDF_IMAGE_PIXELS = 25_000_000
MAX_PDF_TOTAL_IMAGE_PIXELS = 100_000_000
MAX_PDF_RAW_BYTES = 25 * 1024 * 1024
MAX_PDF_LEXICAL_TOKENS = 2_000_000
MAX_PDF_LEXICAL_NAMES = 500_000
MAX_PDF_LEXICAL_OBJECTS = 200_000
MAX_PDF_LEXICAL_REFERENCES = 500_000
MAX_PDF_LEXICAL_DEPTH = 100
MAX_PDF_DIRECT_ARRAY_CHILDREN = 100_000
MAX_OCR_PAGES = 20
MAX_OCR_DPI = 200
MAX_OCR_PAGE_PIXELS = 12_000_000
MAX_OCR_TOTAL_PIXELS = 80_000_000
MAX_OCR_IMAGE_BYTES = 25 * 1024 * 1024
MAX_OCR_TEXT_CHARS = 2_000_000
OCR_COMMAND_TIMEOUT_SECONDS = 45
MAX_QUOTE_ARCHIVE_ENTRIES = 50
MAX_QUOTE_ARCHIVE_MEMBER_BYTES = 32 * 1024 * 1024
MAX_QUOTE_ARCHIVE_TOTAL_BYTES = 64 * 1024 * 1024
MAX_QUOTE_ARCHIVE_COMPRESSION_RATIO = 200
_PDF_DECODE_PATCH_LOCK = threading.Lock()
REQUIRED_XLSX_ARCHIVE_ENTRIES = frozenset(
    {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml"}
)


class UnsafeQuoteFileError(ValueError):
    """A quote exceeds bounded local parsing resources."""


class OcrUnavailableError(ValueError):
    """OCR is needed but the required local executable is unavailable."""


class OcrReviewRequiredError(ValueError):
    """OCR ran safely, but no supported quote table could be confirmed."""


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


_FIELD_ALIASES = {
    "item_name": (
        "품명",
        "품목",
        "품목내역",
        "품목명",
        "자재명",
        "장치",
        "내용",
        "item",
        "itemname",
        "description",
        "designation",
        "bezeichnung",
        "品名",
        "品目",
        "摘要",
    ),
    "spec": (
        "규격",
        "사양",
        "spec",
        "specification",
        "model",
        "type",
        "typ",
        "規格",
        "型式",
        "形式",
        "型号",
    ),
    "unit": ("단위", "unit", "einheit", "單位", "単位"),
    "quantity": (
        "수량",
        "qty",
        "quantity",
        "menge",
        "數量",
        "数量",
    ),
    "unit_price": (
        "단가",
        "unitprice",
        "price",
        "priceperunit",
        "einzelpreis",
        "單價",
        "単価",
    ),
    "amount": (
        "금액",
        "합계",
        "amount",
        "total",
        "totalprice",
        "gesamtpreis",
        "金額",
        "金額",
        "金额",
        "合計金額",
        "合計金额",
    ),
    "maker": (
        "메이커",
        "제조사",
        "브랜드",
        "원maker",
        "原maker",
        "maker사",
        "maker",
        "manufacturer",
    ),
}
_HEADER_SEPARATORS = re.compile(r"[\s_\-./()\[\]:]+")
_PDF_COLUMNS = re.compile(r"\t+|\s{2,}")
_WIA_UNIT_NAME = re.compile(
    r"(?:단위(?:공사명|장비명)[^:：\n]*|전기부문)\s*[:：]\s*"
    r"(.+?)\s*(?:\(첨부\)|$)",
    re.MULTILINE,
)


def read_quote(path: Path) -> list[ParsedRow]:
    """Read a supported quote without normalizing its field values."""
    extension = path.suffix.lower()
    if extension == ".xlsx":
        return read_xlsx(path)
    if extension == ".xls":
        return read_xls(path)
    if extension == ".pdf":
        return read_pdf(path)
    if extension in {".jpg", ".jpeg"}:
        return read_image(path)
    if extension == ".zip":
        return read_zip(path)
    raise ValueError(f"unsupported quote extension: {path.suffix}")


def read_xlsx(path: Path) -> list[ParsedRow]:
    _validate_xlsx_archive(path)
    workbook = load_workbook(path, data_only=True, read_only=True)
    try:
        if len(workbook.worksheets) > MAX_XLSX_WORKSHEETS:
            raise UnsafeQuoteFileError("xlsx has too many worksheets")
        parsed: list[ParsedRow] = []
        total_cells = 0
        for sheet in workbook.worksheets:
            if sheet.max_row is None or sheet.max_column is None:
                raise UnsafeQuoteFileError(
                    "xlsx worksheet dimensions are unavailable"
                )
            cells = sheet.max_row * sheet.max_column
            if (
                sheet.max_row > MAX_XLSX_WORKSHEET_ROWS
                or sheet.max_column > MAX_XLSX_WORKSHEET_COLUMNS
                or cells > MAX_XLSX_WORKSHEET_CELLS
            ):
                raise UnsafeQuoteFileError(
                    "xlsx worksheet dimensions exceed safe limits"
                )
            total_cells += cells
            if total_cells > MAX_XLSX_TOTAL_CELLS:
                raise UnsafeQuoteFileError(
                    "xlsx total cell count exceeds safe limits"
                )
            matrix = [
                [cell.value for cell in row]
                for row in sheet.iter_rows()
            ]
            parsed.extend(
                _parse_tabular_rows(
                    matrix,
                    sheet=sheet.title,
                    page=None,
                    row_numbers=True,
                    cell_ranges=True,
                )
            )
        return parsed
    finally:
        workbook.close()


def read_xls(path: Path) -> list[ParsedRow]:
    workbook = xlrd.open_workbook(str(path))
    sheets = workbook.sheets()
    if len(sheets) > MAX_XLS_SHEETS:
        raise UnsafeQuoteFileError("xls has too many worksheets")
    parsed: list[ParsedRow] = []
    total_cells = 0
    for sheet in sheets:
        cells = sheet.nrows * sheet.ncols
        if (
            sheet.nrows > MAX_XLS_SHEET_ROWS
            or sheet.ncols > MAX_XLS_SHEET_COLUMNS
            or cells > MAX_XLS_SHEET_CELLS
        ):
            raise UnsafeQuoteFileError(
                "xls worksheet dimensions exceed safe limits"
            )
        total_cells += cells
        if total_cells > MAX_XLS_TOTAL_CELLS:
            raise UnsafeQuoteFileError(
                "xls total cell count exceeds safe limits"
            )
        matrix = [
            [sheet.cell_value(row, column) for column in range(sheet.ncols)]
            for row in range(sheet.nrows)
        ]
        parsed.extend(
            _parse_tabular_rows(
                matrix,
                sheet=sheet.name,
                page=None,
                row_numbers=True,
                cell_ranges=True,
            )
        )
    return parsed


def read_zip(path: Path) -> list[ParsedRow]:
    """Read bounded XLS/XLSX members while retaining archive provenance."""
    parsed: list[ParsedRow] = []
    total_uncompressed = 0
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_QUOTE_ARCHIVE_ENTRIES:
            raise UnsafeQuoteFileError("quote archive has too many entries")
        with tempfile.TemporaryDirectory(prefix="price-archive-") as directory:
            temporary_root = Path(directory)
            for member_index, entry in enumerate(entries):
                member_name = _safe_archive_member_name(entry.filename)
                if entry.is_dir():
                    continue
                extension = PurePosixPath(member_name).suffix.lower()
                if extension not in {".xls", ".xlsx"}:
                    continue
                if entry.flag_bits & 1:
                    raise UnsafeQuoteFileError(
                        "encrypted quote archive members are unsupported"
                    )
                if entry.file_size > MAX_QUOTE_ARCHIVE_MEMBER_BYTES:
                    raise UnsafeQuoteFileError(
                        "quote archive member exceeds safe byte limits"
                    )
                total_uncompressed += entry.file_size
                if total_uncompressed > MAX_QUOTE_ARCHIVE_TOTAL_BYTES:
                    raise UnsafeQuoteFileError(
                        "quote archive expands beyond safe byte limits"
                    )
                if entry.file_size and not entry.compress_size:
                    raise UnsafeQuoteFileError(
                        "quote archive member has invalid compression metadata"
                    )
                if (
                    entry.compress_size
                    and entry.file_size / entry.compress_size
                    > MAX_QUOTE_ARCHIVE_COMPRESSION_RATIO
                ):
                    raise UnsafeQuoteFileError(
                        "quote archive member compression ratio is unsafe"
                    )
                payload = archive.read(entry)
                if len(payload) != entry.file_size:
                    raise UnsafeQuoteFileError(
                        "quote archive member size changed while reading"
                    )
                member_path = temporary_root / f"member-{member_index}{extension}"
                member_path.write_bytes(payload)
                for row in read_quote(member_path):
                    unit_price = row.unit_price
                    archive_warnings = [
                        "ARCHIVE_MEMBER",
                        f"ARCHIVE_MEMBER:{member_name}",
                        *row.warnings,
                    ]
                    if unit_price is None:
                        unit_price = _derived_unit_price(
                            row.amount,
                            row.quantity,
                        )
                        if unit_price is not None:
                            archive_warnings.extend(
                                (
                                    "DERIVED_UNIT_PRICE",
                                    "PARSER_SOURCE_REVIEW_REQUIRED",
                                )
                            )
                    parsed.append(
                        ParsedRow(
                            sheet=(
                                f"{member_name}::{row.sheet}"
                                if row.sheet
                                else member_name
                            ),
                            page=row.page,
                            row=row.row,
                            cells=row.cells,
                            item_name=row.item_name,
                            spec=row.spec,
                            unit=row.unit,
                            quantity=row.quantity,
                            unit_price=unit_price,
                            amount=row.amount,
                            maker=row.maker,
                            warnings=tuple(dict.fromkeys(archive_warnings)),
                        )
                    )
    return parsed


def _safe_archive_member_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    member = PurePosixPath(normalized)
    if (
        not normalized
        or member.is_absolute()
        or ".." in member.parts
        or (member.parts and ":" in member.parts[0])
    ):
        raise UnsafeQuoteFileError("quote archive member path is unsafe")
    return member.as_posix()


def _validate_xlsx_archive(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_XLSX_ARCHIVE_ENTRIES:
            raise UnsafeQuoteFileError("xlsx archive has too many entries")
        entry_names = {entry.filename for entry in entries}
        if not REQUIRED_XLSX_ARCHIVE_ENTRIES <= entry_names:
            raise UnsafeQuoteFileError(
                "xlsx archive is missing required workbook entries"
            )
        total_uncompressed = 0
        total_compressed = 0
        for entry in entries:
            total_uncompressed += entry.file_size
            total_compressed += entry.compress_size
            if (
                entry.file_size > 0
                and entry.compress_size == 0
            ):
                raise UnsafeQuoteFileError(
                    "xlsx archive contains an invalid compressed entry"
                )
            if (
                entry.compress_size > 0
                and entry.file_size / entry.compress_size
                > MAX_XLSX_COMPRESSION_RATIO
            ):
                raise UnsafeQuoteFileError(
                    "xlsx archive entry compression ratio is unsafe"
                )
        if total_uncompressed > MAX_XLSX_UNCOMPRESSED_BYTES:
            raise UnsafeQuoteFileError(
                "xlsx archive expands beyond the safe byte limit"
            )
        if (
            total_compressed > 0
            and total_uncompressed / total_compressed
            > MAX_XLSX_COMPRESSION_RATIO
        ):
            raise UnsafeQuoteFileError(
                "xlsx archive compression ratio is unsafe"
            )


def read_pdf(path: Path) -> list[ParsedRow]:
    _preflight_pdf_lexical(path)
    with _bounded_pypdf_flate_decoding():
        return _read_pdf(path)


def _read_pdf(path: Path) -> list[ParsedRow]:
    reader = PdfReader(str(path))
    if len(reader.pages) > MAX_PDF_PAGES:
        raise UnsafeQuoteFileError("pdf has too many pages")
    parsed: list[ParsedRow] = []
    text_total = 0
    row_total = 0
    resource_budget = _PdfResourceBudget()
    wia_units: dict[int, str] = {}
    for page_number, page in enumerate(reader.pages, start=1):
        _inspect_pdf_page_graph(page, resource_budget)
        text = page.extract_text() or ""
        unit_match = _WIA_UNIT_NAME.search(text)
        if unit_match is not None:
            wia_units[page_number] = unit_match.group(1).strip()
        text_total += len(text)
        if text_total > MAX_PDF_EXTRACTED_TEXT_CHARS:
            raise UnsafeQuoteFileError(
                "pdf extracted text exceeds safe limits"
            )
        lines = text.splitlines()
        row_total += len(lines)
        if row_total > MAX_PDF_EXTRACTED_ROWS:
            raise UnsafeQuoteFileError(
                "pdf extracted row count exceeds safe limits"
            )
        matrix = [
            [part for part in _PDF_COLUMNS.split(line.strip())]
            for line in lines
            if line.strip()
        ]
        parsed.extend(
            _parse_tabular_rows(
                matrix,
                sheet=None,
                page=page_number,
                row_numbers=False,
                cell_ranges=False,
            )
        )
    if wia_units:
        table_rows = _read_wia_pdf_tables(path, wia_units)
        if table_rows:
            return table_rows
    if parsed:
        return parsed
    layout_rows = _read_pdf_layout_rows(path)
    if layout_rows:
        return layout_rows
    if not reader.pages:
        return []
    return _read_pdf_with_ocr(path, reader)


def _read_pdf_layout_rows(path: Path) -> list[ParsedRow]:
    """Recover text PDFs whose visual table is lost by plain extraction."""
    parsed: list[ParsedRow] = []
    table_count = 0
    row_count = 0
    cell_count = 0
    try:
        with pdfplumber.open(path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                page_rows: list[ParsedRow] = []
                for table in page.extract_tables():
                    if not table:
                        continue
                    table_count += 1
                    row_count += len(table)
                    cell_count += sum(len(row or ()) for row in table)
                    if (
                        table_count > MAX_PDF_TABLES
                        or row_count > MAX_PDF_TABLE_ROWS
                        or cell_count > MAX_PDF_TABLE_CELLS
                    ):
                        raise UnsafeQuoteFileError(
                            "pdf extracted table data exceeds safe limits"
                        )
                    matrix = [list(row or ()) for row in table]
                    page_rows.extend(
                        _parse_tabular_rows(
                            matrix,
                            sheet=None,
                            page=page_number,
                            row_numbers=False,
                            cell_ranges=False,
                            require_price=True,
                            derive_unit_price=True,
                            extra_warnings=("PDF_COORDINATE_TABLE",),
                        )
                    )
                if page_rows:
                    parsed.extend(page_rows)
                    continue

                text = page.extract_text(
                    x_tolerance=2,
                    y_tolerance=3,
                    layout=True,
                ) or ""
                matrix = [
                    [part for part in _PDF_COLUMNS.split(line.strip())]
                    for line in text.splitlines()
                    if line.strip()
                ]
                reconstructed = _parse_tabular_rows(
                    matrix,
                    sheet=None,
                    page=page_number,
                    row_numbers=False,
                    cell_ranges=False,
                    require_price=True,
                    derive_unit_price=True,
                    extra_warnings=("PDF_LAYOUT_TEXT",),
                )
                if reconstructed:
                    parsed.extend(reconstructed)
                else:
                    parsed.extend(
                        _parse_legacy_pdf_lines(text, page=page_number)
                    )
    except UnsafeQuoteFileError:
        raise
    except Exception:
        return []
    return _deduplicate_parsed_rows(parsed)


_LEGACY_PDF_UNIT = re.compile(
    r"(?<![A-Za-z])"
    r"(?P<unit>EA|SET|LOT|PCS?|PC|UNIT|JOB|LS|KG|HR|DAY|M/D|M-D|"
    r"식|개|대|건|명|일|시간)"
    r"(?![A-Za-z])",
    re.IGNORECASE,
)
_LEGACY_PDF_NUMBER = re.compile(
    r"(?:[₩￦$\¥€] ?)?-?"
    r"(?:\d{1,3}(?:\s*,\s*\d{3})+|\d{4,})(?:\.\d+)?"
)


def _parse_legacy_pdf_lines(text: str, *, page: int) -> list[ParsedRow]:
    parsed: list[ParsedRow] = []
    for line in text.splitlines():
        normalized_line = " ".join(line.split())
        unit_match = _LEGACY_PDF_UNIT.search(normalized_line)
        if unit_match is None:
            continue
        before_unit = normalized_line[: unit_match.start()].rstrip()
        quantity_match = re.search(r"(\d+(?:\.\d+)?)\s*$", before_unit)
        if quantity_match is None:
            continue
        quantity = quantity_match.group(1)
        description = before_unit[: quantity_match.start()].strip()
        description = re.sub(
            r"^\s*\d+(?:[.)_-]\d+)*(?:[.)_-])?\s+",
            "",
            description,
        )
        if _is_non_item_label(description):
            continue
        price_tokens = [
            _normalized_money(match.group(0))
            for match in _LEGACY_PDF_NUMBER.finditer(
                normalized_line[unit_match.end() :]
            )
        ]
        price_tokens = [value for value in price_tokens if value is not None]
        if not price_tokens:
            continue
        amount = price_tokens[1] if len(price_tokens) >= 2 else price_tokens[0]
        unit_price = price_tokens[0] if len(price_tokens) >= 2 else None
        warnings = [
            "PDF_LEGACY_LINE",
            "SPEC_COLUMN_NOT_FOUND",
            "PARSER_SOURCE_REVIEW_REQUIRED",
        ]
        if unit_price is None:
            unit_price = _derived_unit_price(amount, quantity)
            if unit_price is None:
                continue
            warnings.append("DERIVED_UNIT_PRICE")
        parsed.append(
            ParsedRow(
                sheet=None,
                page=page,
                row=None,
                cells=None,
                item_name=description,
                spec=None,
                unit=unit_match.group("unit"),
                quantity=quantity,
                unit_price=unit_price,
                amount=amount,
                maker=None,
                warnings=tuple(warnings),
            )
        )
    return parsed


def _deduplicate_parsed_rows(rows: list[ParsedRow]) -> list[ParsedRow]:
    result: list[ParsedRow] = []
    seen: set[tuple[object, ...]] = set()
    for row in rows:
        key = (
            row.page,
            row.item_name,
            row.spec,
            row.unit,
            row.quantity,
            row.unit_price,
            row.amount,
        )
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def read_image(path: Path) -> list[ParsedRow]:
    """Read a bounded image quote using the optional local OCR runtime."""
    size = path.stat().st_size
    if size > MAX_OCR_IMAGE_BYTES:
        raise UnsafeQuoteFileError("image exceeds safe OCR byte limits")
    try:
        with Image.open(path) as image:
            width, height = image.size
    except (OSError, ValueError) as exc:
        raise OcrReviewRequiredError(
            "image could not be decoded for OCR review"
        ) from exc
    pixels = width * height
    if pixels <= 0 or pixels > MAX_OCR_PAGE_PIXELS:
        raise UnsafeQuoteFileError("image resolution exceeds safe OCR limits")
    runtime = _resolve_ocr_runtime(require_renderer=False)
    with _ocr_tessdata(runtime) as tessdata:
        text = _run_tesseract(path, runtime, tessdata=tessdata)
    rows = _parse_ocr_text(text, page=1)
    if not rows:
        raise OcrReviewRequiredError(
            "OCR completed but no supported quote rows were confirmed"
        )
    return rows


@dataclass(frozen=True)
class _OcrRuntime:
    tesseract: Path
    renderer: Path | None
    languages: tuple[str, ...]
    tessdata_sources: tuple[Path, ...]


def _read_pdf_with_ocr(
    path: Path,
    reader: PdfReader,
) -> list[ParsedRow]:
    page_count = len(reader.pages)
    if page_count > MAX_OCR_PAGES:
        raise UnsafeQuoteFileError("pdf exceeds safe OCR page limits")

    page_pixels: list[int] = []
    for page in reader.pages:
        try:
            width_points = float(page.mediabox.width)
            height_points = float(page.mediabox.height)
        except (TypeError, ValueError) as exc:
            raise UnsafeQuoteFileError(
                "pdf page dimensions are invalid for OCR"
            ) from exc
        width_pixels = round(width_points * MAX_OCR_DPI / 72)
        height_pixels = round(height_points * MAX_OCR_DPI / 72)
        pixels = width_pixels * height_pixels
        if pixels <= 0 or pixels > MAX_OCR_PAGE_PIXELS:
            raise UnsafeQuoteFileError(
                "pdf page resolution exceeds safe OCR limits"
            )
        page_pixels.append(pixels)
    if sum(page_pixels) > MAX_OCR_TOTAL_PIXELS:
        raise UnsafeQuoteFileError(
            "pdf total rendered resolution exceeds safe OCR limits"
        )

    runtime = _resolve_ocr_runtime(require_renderer=True)
    assert runtime.renderer is not None
    parsed: list[ParsedRow] = []
    text_total = 0
    with tempfile.TemporaryDirectory(prefix="price-ocr-") as directory:
        temporary_root = Path(directory)
        with _ocr_tessdata(runtime, temporary_root) as tessdata:
            for page_number in range(1, page_count + 1):
                image_prefix = temporary_root / f"page-{page_number}"
                _run_command(
                    [
                        str(runtime.renderer),
                        "-f",
                        str(page_number),
                        "-l",
                        str(page_number),
                        "-singlefile",
                        "-r",
                        str(MAX_OCR_DPI),
                        "-png",
                        str(path),
                        str(image_prefix),
                    ],
                    error_message="PDF page rendering needs manual review",
                )
                image_path = image_prefix.with_suffix(".png")
                if not image_path.is_file():
                    raise OcrReviewRequiredError(
                        "PDF renderer did not produce an OCR image"
                    )
                if image_path.stat().st_size > MAX_OCR_IMAGE_BYTES:
                    raise UnsafeQuoteFileError(
                        "rendered PDF page exceeds safe OCR byte limits"
                    )
                with Image.open(image_path) as image:
                    if image.width * image.height > MAX_OCR_PAGE_PIXELS:
                        raise UnsafeQuoteFileError(
                            "rendered PDF page exceeds safe OCR resolution"
                        )
                text = _run_tesseract(
                    image_path,
                    runtime,
                    tessdata=tessdata,
                )
                text_total += len(text)
                if text_total > MAX_OCR_TEXT_CHARS:
                    raise UnsafeQuoteFileError(
                        "OCR text exceeds safe extraction limits"
                    )
                parsed.extend(_parse_ocr_text(text, page=page_number))
    if not parsed:
        raise OcrReviewRequiredError(
            "OCR completed but no supported quote rows were confirmed"
        )
    return parsed


def ocr_pdf_text_pages(
    path: Path,
    *,
    page_limit: int = 2,
) -> tuple[str, ...]:
    """Return bounded OCR text for quote cover pages.

    This is shared with metadata auditing. It deliberately returns text only;
    callers must still apply field-specific evidence rules before accepting a
    value.
    """

    if page_limit <= 0 or page_limit > MAX_OCR_PAGES:
        raise ValueError("OCR page limit is outside the safe range")
    _preflight_pdf_lexical(path)
    reader = PdfReader(str(path))
    if len(reader.pages) > MAX_PDF_PAGES:
        raise UnsafeQuoteFileError("pdf has too many pages")
    if reader.is_encrypted and reader.decrypt("") == 0:
        raise OcrReviewRequiredError("encrypted PDF needs manual review")
    pages = min(len(reader.pages), page_limit)
    runtime = _resolve_ocr_runtime(require_renderer=True)
    assert runtime.renderer is not None
    result: list[str] = []
    text_total = 0
    with tempfile.TemporaryDirectory(prefix="price-date-ocr-") as directory:
        temporary_root = Path(directory)
        with _ocr_tessdata(runtime, temporary_root) as tessdata:
            for page_number in range(1, pages + 1):
                page = reader.pages[page_number - 1]
                try:
                    width_pixels = round(
                        float(page.mediabox.width) * MAX_OCR_DPI / 72
                    )
                    height_pixels = round(
                        float(page.mediabox.height) * MAX_OCR_DPI / 72
                    )
                except (TypeError, ValueError) as exc:
                    raise UnsafeQuoteFileError(
                        "pdf page dimensions are invalid for OCR"
                    ) from exc
                if (
                    width_pixels <= 0
                    or height_pixels <= 0
                    or width_pixels * height_pixels > MAX_OCR_PAGE_PIXELS
                ):
                    raise UnsafeQuoteFileError(
                        "pdf page resolution exceeds safe OCR limits"
                    )
                image_prefix = temporary_root / f"page-{page_number}"
                _run_command(
                    [
                        str(runtime.renderer),
                        "-f", str(page_number),
                        "-l", str(page_number),
                        "-singlefile",
                        "-r", str(MAX_OCR_DPI),
                        "-png",
                        str(path),
                        str(image_prefix),
                    ],
                    error_message="PDF page rendering needs manual review",
                )
                image_path = image_prefix.with_suffix(".png")
                if not image_path.is_file():
                    raise OcrReviewRequiredError(
                        "PDF renderer did not produce an OCR image"
                    )
                if image_path.stat().st_size > MAX_OCR_IMAGE_BYTES:
                    raise UnsafeQuoteFileError(
                        "rendered PDF page exceeds safe OCR byte limits"
                    )
                text = _run_tesseract(
                    image_path,
                    runtime,
                    tessdata=tessdata,
                )
                text_total += len(text)
                if text_total > MAX_OCR_TEXT_CHARS:
                    raise UnsafeQuoteFileError(
                        "OCR text exceeds safe extraction limits"
                    )
                result.append(text)
    return tuple(result)


def _parse_ocr_text(text: str, *, page: int) -> list[ParsedRow]:
    matrix = [
        [part for part in _PDF_COLUMNS.split(line.strip())]
        for line in text.splitlines()
        if line.strip()
    ]
    parsed = _parse_tabular_rows(
        matrix,
        sheet=None,
        page=page,
        row_numbers=False,
        cell_ranges=False,
    )
    return [
        ParsedRow(
            sheet=row.sheet,
            page=row.page,
            row=row.row,
            cells=row.cells,
            item_name=row.item_name,
            spec=row.spec,
            unit=row.unit,
            quantity=row.quantity,
            unit_price=row.unit_price,
            amount=row.amount,
            maker=row.maker,
            warnings=(
                *row.warnings,
                "OCR_SOURCE",
                "OCR_REVIEW_REQUIRED",
            ),
        )
        for row in parsed
    ]


def _resolve_ocr_runtime(*, require_renderer: bool) -> _OcrRuntime:
    backend_root = Path(__file__).resolve().parents[2]
    tesseract = _find_executable(
        "tesseract",
        environment_name="TESSERACT_CMD",
        candidates=(
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Tesseract-OCR"
            / "tesseract.exe",
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
            / "Tesseract-OCR"
            / "tesseract.exe",
        ),
    )
    if tesseract is None:
        raise OcrUnavailableError(
            "OCR_UNAVAILABLE: tesseract executable was not found"
        )

    renderer = None
    if require_renderer:
        program_files = Path(
            os.environ.get("ProgramFiles", "C:/Program Files")
        )
        program_files_x86 = Path(
            os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")
        )
        renderer = _find_executable(
            "pdftoppm",
            environment_name="PDFTOPPM_CMD",
            candidates=(
                program_files / "poppler" / "Library" / "bin" / "pdftoppm.exe",
                program_files / "poppler" / "bin" / "pdftoppm.exe",
                program_files_x86 / "poppler" / "Library" / "bin" / "pdftoppm.exe",
                program_files_x86 / "poppler" / "bin" / "pdftoppm.exe",
            ),
        )
        if renderer is None:
            raise OcrUnavailableError(
                "OCR_UNAVAILABLE: pdftoppm executable was not found"
            )

    configured_languages = os.environ.get("OCR_LANGUAGES", "kor+eng")
    languages = tuple(
        language.strip()
        for language in configured_languages.split("+")
        if language.strip()
    )
    if not languages:
        raise OcrUnavailableError(
            "OCR_UNAVAILABLE: no OCR language was configured"
        )
    configured_tessdata = os.environ.get("TESSDATA_PREFIX")
    tessdata_sources = _unique_existing_directories(
        (
            Path(configured_tessdata) if configured_tessdata else None,
            backend_root / ".local" / "ocr" / "tessdata",
            tesseract.parent / "tessdata",
            Path("/usr/share/tesseract-ocr/5/tessdata"),
            Path("/usr/share/tesseract-ocr/4.00/tessdata"),
            Path("/usr/share/tessdata"),
        )
    )
    missing = [
        language
        for language in languages
        if not any(
            (source / f"{language}.traineddata").is_file()
            for source in tessdata_sources
        )
    ]
    if missing:
        raise OcrUnavailableError(
            "OCR_UNAVAILABLE: configured OCR language data was not found"
        )
    return _OcrRuntime(
        tesseract=tesseract,
        renderer=renderer,
        languages=languages,
        tessdata_sources=tessdata_sources,
    )


def _find_executable(
    name: str,
    *,
    environment_name: str,
    candidates: tuple[Path, ...],
) -> Path | None:
    configured = os.environ.get(environment_name)
    if configured:
        configured_path = Path(configured).expanduser()
        if (
            configured_path.is_file()
            and configured_path.suffix.lower() not in {".bat", ".cmd"}
        ):
            return configured_path
        resolved = shutil.which(configured)
        if resolved and Path(resolved).suffix.lower() not in {".bat", ".cmd"}:
            return Path(resolved)
        return None

    path_directories = [
        Path(entry)
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry
    ]
    executable_names = (
        (f"{name}.exe", name)
        if os.name == "nt"
        else (name,)
    )
    for directory in path_directories:
        for executable_name in executable_names:
            candidate = directory / executable_name
            if candidate.is_file():
                return candidate
    resolved = shutil.which(name)
    if resolved and Path(resolved).suffix.lower() not in {".bat", ".cmd"}:
        return Path(resolved)
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _unique_existing_directories(
    candidates: tuple[Path | None, ...],
) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate is None or not candidate.is_dir():
            continue
        resolved = candidate.resolve(strict=False)
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return tuple(result)


@contextmanager
def _ocr_tessdata(
    runtime: _OcrRuntime,
    temporary_root: Path | None = None,
) -> Iterator[Path]:
    required = {
        language: f"{language}.traineddata"
        for language in runtime.languages
    }
    for source in runtime.tessdata_sources:
        if all((source / filename).is_file() for filename in required.values()):
            yield source
            return

    if temporary_root is not None:
        combined = temporary_root / "tessdata"
        combined.mkdir()
        for filename in required.values():
            source_file = next(
                source / filename
                for source in runtime.tessdata_sources
                if (source / filename).is_file()
            )
            shutil.copyfile(source_file, combined / filename)
        yield combined
        return

    with tempfile.TemporaryDirectory(prefix="price-tessdata-") as directory:
        combined = Path(directory)
        for filename in required.values():
            source_file = next(
                source / filename
                for source in runtime.tessdata_sources
                if (source / filename).is_file()
            )
            shutil.copyfile(source_file, combined / filename)
        yield combined


def _run_tesseract(
    image_path: Path,
    runtime: _OcrRuntime,
    *,
    tessdata: Path,
) -> str:
    completed = _run_command(
        [
            str(runtime.tesseract),
            str(image_path),
            "stdout",
            "--tessdata-dir",
            str(tessdata),
            "-l",
            "+".join(runtime.languages),
            "--psm",
            "6",
            "-c",
            "preserve_interword_spaces=1",
        ],
        error_message="OCR execution needs manual review",
    )
    text = completed.stdout
    if len(text) > MAX_OCR_TEXT_CHARS:
        raise UnsafeQuoteFileError("OCR text exceeds safe extraction limits")
    return text


def _run_command(
    command: list[str],
    *,
    error_message: str,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=OCR_COMMAND_TIMEOUT_SECONDS,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise OcrUnavailableError(
            "OCR_UNAVAILABLE: local OCR executable disappeared"
        ) from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise OcrReviewRequiredError(error_message) from exc


def _read_wia_pdf_tables(
    path: Path,
    wia_units: dict[int, str],
) -> list[ParsedRow]:
    """Extract rows from the observed Hyundai WIA detail-table layout.

    The generic pypdf text reader remains the fallback. This layout-specific
    pass runs only after the bounded pypdf preflight recognized a unit-work or
    electrical-section marker, so arbitrary PDFs are not sent through the
    slower table extractor.
    """
    parsed: list[ParsedRow] = []
    table_count = 0
    row_count = 0
    cell_count = 0
    try:
        with pdfplumber.open(path) as pdf:
            for page_number, unit_name in sorted(wia_units.items()):
                if page_number > len(pdf.pages):
                    continue
                page = pdf.pages[page_number - 1]
                for table in page.extract_tables():
                    if not table:
                        continue
                    table_count += 1
                    row_count += len(table)
                    cell_count += sum(len(row or ()) for row in table)
                    if (
                        table_count > MAX_PDF_TABLES
                        or row_count > MAX_PDF_TABLE_ROWS
                        or cell_count > MAX_PDF_TABLE_CELLS
                    ):
                        raise UnsafeQuoteFileError(
                            "pdf extracted table data exceeds safe limits"
                        )
                    parsed.extend(
                        _parse_wia_pdf_table(
                            table,
                            page=page_number,
                            unit_name=unit_name,
                        )
                    )
    except UnsafeQuoteFileError:
        raise
    except Exception:
        return []
    return parsed


def _parse_wia_pdf_table(
    table: list[list[Any] | None],
    *,
    page: int,
    unit_name: str,
) -> list[ParsedRow]:
    parsed: list[ParsedRow] = []
    header_found = False
    for row in table:
        if row is None or len(row) < 7:
            continue
        row_text = " ".join(_raw_text(value) or "" for value in row)
        if "품 명" in row_text or "단가(원)" in row_text:
            header_found = True
            continue
        if not header_found:
            continue

        offset = 2 if len(row) >= 9 else 1
        item_name = (_raw_text(row[offset]) or "").lstrip("■□●○").strip()
        spec = _raw_text(row[offset + 1])
        unit = _raw_text(row[offset + 2])
        quantity = _raw_text(row[offset + 3])
        unit_price = _raw_text(row[offset + 4])
        amount = _raw_text(row[offset + 5])
        maker = (
            _raw_text(row[offset + 6])
            if offset + 6 < len(row)
            else None
        )
        warnings = ["PDF_WIA_TABLE", f"UNIT_SECTION:{unit_name}"]
        if spec is None:
            warnings.append("SOURCE_SPEC_BLANK")
        if maker is not None and not _plausible_maker(maker):
            maker = None
            warnings.append("MAKER_REJECTED_NON_BRAND")
        if (
            len(item_name) < 2
            or _is_number(item_name)
            or not _positive_number(unit_price)
            or not _number_at_least(amount, Decimal("1000"))
        ):
            continue
        parsed.append(
            ParsedRow(
                sheet=None,
                page=page,
                row=None,
                cells=None,
                item_name=item_name,
                spec=spec,
                unit=unit,
                quantity=quantity,
                unit_price=unit_price,
                amount=amount,
                maker=maker,
                warnings=tuple(warnings),
            )
        )
    return parsed


@dataclass
class _PdfResourceBudget:
    visited: set[tuple[object, ...]] = field(default_factory=set)
    object_count: int = 0
    raw_bytes: int = 0
    decoded_bytes: int = 0
    decoded_limit: int = MAX_PDF_DECODED_CONTENT_BYTES
    image_count: int = 0
    image_raw_bytes: int = 0
    image_pixels: int = 0


def _inspect_pdf_page_graph(
    page: Any,
    budget: _PdfResourceBudget,
) -> None:
    if not hasattr(page, "raw_get"):
        return
    for root_name in ("/Contents", "/Resources"):
        try:
            root = page.raw_get(root_name)
        except KeyError:
            continue
        _inspect_pdf_object(root, budget, depth=0)


def _inspect_pdf_object(
    value: Any,
    budget: _PdfResourceBudget,
    *,
    depth: int,
) -> None:
    if depth > MAX_PDF_RESOURCE_DEPTH:
        raise UnsafeQuoteFileError("pdf resource graph is too deep")
    if isinstance(value, IndirectObject):
        marker = (
            "indirect",
            id(value.pdf),
            value.idnum,
            value.generation,
        )
        if marker in budget.visited:
            return
        _visit_pdf_marker(marker, budget)
        _inspect_pdf_object(value.get_object(), budget, depth=depth + 1)
        return
    if isinstance(value, (DictionaryObject, ArrayObject)):
        marker = ("direct", id(value))
        if marker in budget.visited:
            return
        _visit_pdf_marker(marker, budget)
    if isinstance(value, StreamObject):
        _inspect_pdf_stream(value, budget)
        for child in value.values():
            _inspect_pdf_object(child, budget, depth=depth + 1)
    elif isinstance(value, DictionaryObject):
        for child in value.values():
            _inspect_pdf_object(child, budget, depth=depth + 1)
    elif isinstance(value, ArrayObject):
        for child in value:
            _inspect_pdf_object(child, budget, depth=depth + 1)
    else:
        budget.object_count += 1
        if budget.object_count > MAX_PDF_REACHABLE_OBJECTS:
            raise UnsafeQuoteFileError(
                "pdf resource graph has too many primitive children"
            )


def _visit_pdf_marker(
    marker: tuple[object, ...],
    budget: _PdfResourceBudget,
) -> None:
    budget.visited.add(marker)
    budget.object_count += 1
    if budget.object_count > MAX_PDF_REACHABLE_OBJECTS:
        raise UnsafeQuoteFileError(
            "pdf resource graph has too many objects"
        )


def _inspect_pdf_stream(
    stream: StreamObject,
    budget: _PdfResourceBudget,
) -> None:
    raw = stream._data
    budget.raw_bytes += len(raw)
    if budget.raw_bytes > MAX_PDF_COMPRESSED_CONTENT_BYTES:
        raise UnsafeQuoteFileError(
            "pdf reachable stream bytes exceed safe limits"
        )
    filters = _pdf_filter_names(stream)
    if str(stream.get("/Subtype", "")) == "/Image":
        _inspect_pdf_image(stream, filters, raw, budget)
        # PageObject._extract_text explicitly skips image XObjects.
        if filters in (("/DCTDecode",), ("/JPXDecode",)):
            return
    remaining = budget.decoded_limit - budget.decoded_bytes
    if not filters:
        decoded_size = len(raw)
    elif filters in (("/FlateDecode",), ("/Fl",)):
        decoded_size = _bounded_flate_size(raw, remaining)
    else:
        raise UnsafeQuoteFileError(
            "pdf resource uses an unbounded compressed filter"
        )
    budget.decoded_bytes += decoded_size
    if budget.decoded_bytes > budget.decoded_limit:
        raise UnsafeQuoteFileError(
            "pdf decoded resource bytes exceed safe limits"
        )


def _inspect_pdf_image(
    stream: StreamObject,
    filters: tuple[str, ...],
    raw: bytes,
    budget: _PdfResourceBudget,
) -> None:
    width = _pdf_positive_int(stream.get("/Width"))
    height = _pdf_positive_int(stream.get("/Height"))
    if width is None or height is None:
        raise UnsafeQuoteFileError(
            "pdf image dimensions are missing or invalid"
        )
    pixels = width * height
    budget.image_count += 1
    budget.image_raw_bytes += len(raw)
    budget.image_pixels += pixels
    if (
        budget.image_count > MAX_PDF_IMAGE_COUNT
        or budget.image_raw_bytes > MAX_PDF_IMAGE_RAW_BYTES
        or pixels > MAX_PDF_IMAGE_PIXELS
        or budget.image_pixels > MAX_PDF_TOTAL_IMAGE_PIXELS
    ):
        raise UnsafeQuoteFileError("pdf image resources exceed safe limits")
    if filters in (("/DCTDecode",), ("/JPXDecode",)):
        return


def _pdf_positive_int(value: Any) -> int | None:
    if hasattr(value, "get_object"):
        value = value.get_object()
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


@dataclass(frozen=True)
class _PdfLexicalToken:
    kind: str
    value: str


def _preflight_pdf_lexical(path: Path) -> None:
    data = path.read_bytes()
    if len(data) > MAX_PDF_RAW_BYTES:
        raise UnsafeQuoteFileError("pdf file exceeds safe raw byte limits")
    _conservative_pdf_filter_prescan(data)
    tokens = _pdf_lexical_tokens(data)
    name_count = sum(token.kind == "name" for token in tokens)
    object_count = sum(
        token.kind == "word" and token.value == "obj"
        for token in tokens
    )
    reference_count = sum(
        token.kind == "word" and token.value == "R"
        for token in tokens
    )
    if name_count > MAX_PDF_LEXICAL_NAMES:
        raise UnsafeQuoteFileError("pdf has too many lexical names")
    if object_count > MAX_PDF_LEXICAL_OBJECTS:
        raise UnsafeQuoteFileError("pdf has too many object declarations")
    if reference_count > MAX_PDF_LEXICAL_REFERENCES:
        raise UnsafeQuoteFileError("pdf has too many indirect references")

    expansion_filters = {
        "RunLengthDecode",
        "RL",
        "LZWDecode",
        "LZW",
    }
    for token in tokens:
        if token.kind == "name" and token.value in expansion_filters:
            raise UnsafeQuoteFileError(
                "pdf declares an unsafe expansion filter"
            )
    for index, token in enumerate(tokens):
        if token.kind == "name" and token.value == "Filter":
            _validate_lexical_filter(tokens, index + 1)


def _validate_lexical_filter(
    tokens: list[_PdfLexicalToken],
    index: int,
) -> None:
    allowed = {"FlateDecode", "Fl", "DCTDecode", "JPXDecode"}
    if index >= len(tokens):
        raise UnsafeQuoteFileError("pdf filter declaration is incomplete")
    value = tokens[index]
    if value.kind == "name":
        if value.value not in allowed:
            raise UnsafeQuoteFileError(
                "pdf declares an unsupported filter"
            )
        return
    if value.kind != "punct" or value.value != "[":
        raise UnsafeQuoteFileError(
            "pdf filter declaration is indirect or malformed"
        )
    names: list[str] = []
    cursor = index + 1
    while cursor < len(tokens):
        current = tokens[cursor]
        if current.kind == "punct" and current.value == "]":
            break
        if current.kind != "name":
            raise UnsafeQuoteFileError(
                "pdf filter array is malformed"
            )
        names.append(current.value)
        cursor += 1
    if cursor >= len(tokens) or len(names) != 1 or names[0] not in allowed:
        raise UnsafeQuoteFileError(
            "pdf filter arrays and chains are unsafe"
        )


def _pdf_lexical_tokens(data: bytes) -> list[_PdfLexicalToken]:
    tokens: list[_PdfLexicalToken] = []
    array_children: list[int] = []
    dictionary_starts: list[int] = []
    candidate_stream_length: int | None = None
    index = 0
    while index < len(data):
        byte = data[index]
        if byte in b"\x00\t\n\x0c\r ":
            index += 1
            continue
        if byte == ord("%"):
            index = _skip_pdf_comment(data, index + 1)
            continue
        if byte == ord("("):
            candidate_stream_length = None
            index = _skip_pdf_literal_string(data, index + 1)
            continue
        if byte == ord("<") and not data.startswith(b"<<", index):
            candidate_stream_length = None
            closing = data.find(b">", index + 1)
            index = len(data) if closing < 0 else closing + 1
            continue
        if data.startswith(b"<<", index) or data.startswith(b">>", index):
            token = data[index : index + 2].decode("ascii")
            index += 2
            if token == "<<":
                candidate_stream_length = None
                if (
                    len(dictionary_starts) + len(array_children) + 1
                    > MAX_PDF_LEXICAL_DEPTH
                ):
                    raise UnsafeQuoteFileError(
                        "pdf lexical nesting is too deep"
                    )
                dictionary_starts.append(len(tokens))
            elif dictionary_starts:
                dictionary_start = dictionary_starts.pop()
                candidate_stream_length = _direct_pdf_stream_length(
                    tokens[dictionary_start + 1 :]
                )
            else:
                candidate_stream_length = None
            _append_pdf_token(tokens, "punct", token, array_children)
            continue
        if byte in b"[]{}":
            candidate_stream_length = None
            token = chr(byte)
            index += 1
            if token == "[":
                if (
                    len(dictionary_starts) + len(array_children) + 1
                    > MAX_PDF_LEXICAL_DEPTH
                ):
                    raise UnsafeQuoteFileError(
                        "pdf lexical nesting is too deep"
                    )
                if array_children:
                    array_children[-1] += 1
                array_children.append(0)
            elif token == "]":
                if array_children:
                    array_children.pop()
            _append_pdf_token(tokens, "punct", token, array_children)
            continue
        if byte == ord("/"):
            candidate_stream_length = None
            end = _pdf_token_end(data, index + 1)
            value = _decode_pdf_name(data[index + 1 : end])
            index = end
            _append_pdf_token(tokens, "name", value, array_children)
            continue
        end = _pdf_token_end(data, index)
        if end == index:
            index += 1
            continue
        value = data[index:end].decode("latin-1")
        index = end
        _append_pdf_token(tokens, "word", value, array_children)
        if value == "stream" and candidate_stream_length is not None:
            stream_start = _pdf_stream_data_start(data, index)
            if (
                stream_start is not None
                and candidate_stream_length
                <= len(data) - stream_start
            ):
                index = stream_start + candidate_stream_length
            candidate_stream_length = None
        else:
            candidate_stream_length = None
    return tokens


def _direct_pdf_stream_length(
    dictionary_tokens: list[_PdfLexicalToken],
) -> int | None:
    nesting = 0
    lengths: list[int] = []
    for index, token in enumerate(dictionary_tokens):
        if token.kind == "punct":
            if token.value in {"<<", "["}:
                nesting += 1
            elif token.value in {">>", "]"} and nesting:
                nesting -= 1
            continue
        if (
            nesting
            or token.kind != "name"
            or token.value != "Length"
            or index + 1 >= len(dictionary_tokens)
        ):
            continue
        value = dictionary_tokens[index + 1]
        if value.kind != "word" or re.fullmatch(r"\d+", value.value) is None:
            return None
        if (
            index + 3 < len(dictionary_tokens)
            and dictionary_tokens[index + 2].kind == "word"
            and re.fullmatch(
                r"\d+",
                dictionary_tokens[index + 2].value,
            )
            is not None
            and dictionary_tokens[index + 3]
            == _PdfLexicalToken("word", "R")
        ):
            return None
        lengths.append(int(value.value))
    if len(lengths) != 1 or lengths[0] > MAX_PDF_RAW_BYTES:
        return None
    return lengths[0]


def _pdf_stream_data_start(data: bytes, index: int) -> int | None:
    if data.startswith(b"\r\n", index):
        return index + 2
    if index < len(data) and data[index] in b"\r\n":
        return index + 1
    return None


def _conservative_pdf_filter_prescan(data: bytes) -> None:
    allowed = {"FlateDecode", "Fl", "DCTDecode", "JPXDecode"}
    dangerous = {"RunLengthDecode", "RL", "LZWDecode", "LZW"}
    name_count = 0
    # This deliberately scans every raw slash-name, including names in
    # comments, strings, and stream bytes. A safe file can therefore be
    # rejected, but an unsafe expansion filter cannot hide behind lexer
    # context before a third-party PDF reader sees the bytes.
    for value, _, end in _raw_pdf_name_tokens(data):
        name_count += 1
        if name_count > MAX_PDF_LEXICAL_NAMES:
            raise UnsafeQuoteFileError("pdf has too many raw names")
        if value in dangerous:
            raise UnsafeQuoteFileError(
                "pdf raw bytes contain an unsafe expansion filter"
            )
        if value != "Filter":
            continue
        cursor = end
        while cursor < len(data) and data[cursor] in b"\x00\t\n\x0c\r ":
            cursor += 1
        if cursor >= len(data):
            raise UnsafeQuoteFileError(
                "pdf raw filter declaration is incomplete"
            )
        if data[cursor] == ord("["):
            raise UnsafeQuoteFileError(
                "pdf raw filter arrays and chains are unsafe"
            )
        if data[cursor] != ord("/"):
            raise UnsafeQuoteFileError(
                "pdf raw filter declaration is indirect or malformed"
            )
        value_end = _pdf_token_end(data, cursor + 1)
        filter_name = _decode_pdf_name(data[cursor + 1 : value_end])
        if filter_name not in allowed:
            raise UnsafeQuoteFileError(
                "pdf raw bytes declare an unsupported filter"
            )


def _raw_pdf_name_tokens(
    data: bytes,
) -> Iterator[tuple[str, int, int]]:
    index = 0
    while index < len(data):
        slash = data.find(b"/", index)
        if slash < 0:
            break
        end = _pdf_token_end(data, slash + 1)
        if end > slash + 1:
            yield (
                _decode_pdf_name(data[slash + 1 : end]),
                slash,
                end,
            )
        index = max(end, slash + 1)


def _skip_pdf_comment(data: bytes, index: int) -> int:
    cr = data.find(b"\r", index)
    lf = data.find(b"\n", index)
    endings = [position for position in (cr, lf) if position >= 0]
    if not endings:
        return len(data)
    ending = min(endings)
    if data.startswith(b"\r\n", ending):
        return ending + 2
    return ending + 1


def _append_pdf_token(
    tokens: list[_PdfLexicalToken],
    kind: str,
    value: str,
    array_children: list[int],
) -> None:
    tokens.append(_PdfLexicalToken(kind, value))
    if len(tokens) > MAX_PDF_LEXICAL_TOKENS:
        raise UnsafeQuoteFileError("pdf has too many lexical tokens")
    if array_children and not (kind == "punct" and value in {"[", "]"}):
        array_children[-1] += 1
        if array_children[-1] > MAX_PDF_DIRECT_ARRAY_CHILDREN:
            raise UnsafeQuoteFileError(
                "pdf direct array has too many children"
            )


def _skip_pdf_literal_string(data: bytes, index: int) -> int:
    depth = 1
    while index < len(data) and depth:
        byte = data[index]
        if byte == ord("\\"):
            index += 2
            continue
        if byte == ord("("):
            depth += 1
            if depth > MAX_PDF_LEXICAL_DEPTH:
                raise UnsafeQuoteFileError(
                    "pdf literal string nesting is too deep"
                )
        elif byte == ord(")"):
            depth -= 1
        index += 1
    return index


def _pdf_token_end(data: bytes, index: int) -> int:
    delimiters = b"\x00\t\n\x0c\r ()<>[]{}/%"
    while index < len(data) and data[index] not in delimiters:
        index += 1
    return index


def _decode_pdf_name(value: bytes) -> str:
    decoded = bytearray()
    index = 0
    while index < len(value):
        if (
            value[index] == ord("#")
            and index + 2 < len(value)
            and all(
                character in b"0123456789abcdefABCDEF"
                for character in value[index + 1 : index + 3]
            )
        ):
            decoded.append(int(value[index + 1 : index + 3], 16))
            index += 3
        else:
            decoded.append(value[index])
            index += 1
    return decoded.decode("latin-1")


def _bounded_pdf_page_content(
    page: Any,
    *,
    decoded_remaining: int,
) -> tuple[int, int]:
    budget = _PdfResourceBudget(decoded_limit=decoded_remaining)
    if not hasattr(page, "raw_get"):
        return 0, 0
    try:
        contents = page.raw_get("/Contents")
    except KeyError:
        return 0, 0
    _inspect_pdf_object(contents, budget, depth=0)
    return budget.raw_bytes, budget.decoded_bytes


def _pdf_filter_names(stream: StreamObject) -> tuple[str, ...]:
    value = stream.get("/Filter")
    if value is None:
        return ()
    value = value.get_object() if hasattr(value, "get_object") else value
    values = value if isinstance(value, ArrayObject) else [value]
    return tuple(str(item.get_object()) for item in values)


def _bounded_flate_size(data: bytes, maximum: int) -> int:
    return len(_bounded_flate_decode(data, maximum))


def _bounded_flate_decode(data: bytes, maximum: int) -> bytes:
    for window_bits in (zlib.MAX_WBITS, zlib.MAX_WBITS | 32):
        decoder = zlib.decompressobj(window_bits)
        try:
            decoded = decoder.decompress(data, maximum + 1)
            if len(decoded) > maximum or decoder.unconsumed_tail:
                raise UnsafeQuoteFileError(
                    "pdf decoded content exceeds safe limits"
                )
            decoded += decoder.flush(maximum + 1 - len(decoded))
            if len(decoded) > maximum:
                raise UnsafeQuoteFileError(
                    "pdf decoded content exceeds safe limits"
                )
            return decoded
        except zlib.error:
            continue
    raise UnsafeQuoteFileError("pdf flate content cannot be decoded safely")


@contextmanager
def _bounded_pypdf_flate_decoding():
    """Serialize pypdf's process-global Flate hook and restore it reliably."""

    import pypdf.filters as pdf_filters

    with _PDF_DECODE_PATCH_LOCK:
        original = pdf_filters.decompress
        remaining = MAX_PDF_TOTAL_FLATE_DECODED_BYTES

        def bounded_decompress(data: bytes) -> bytes:
            nonlocal remaining
            decoded = _bounded_flate_decode(data, remaining)
            remaining -= len(decoded)
            return decoded

        pdf_filters.decompress = bounded_decompress
        try:
            yield
        finally:
            pdf_filters.decompress = original


def _parse_tabular_rows(
    rows: list[list[Any]],
    *,
    sheet: str | None,
    page: int | None,
    row_numbers: bool,
    cell_ranges: bool,
    require_price: bool = False,
    derive_unit_price: bool = False,
    extra_warnings: tuple[str, ...] = (),
) -> list[ParsedRow]:
    header_index, columns = _find_header(rows)
    if header_index is None:
        return (
            _parse_fixed_column_fallback(rows, sheet=sheet, page=page)
            if cell_ranges
            else []
        )
    if _contains_cjk_quote_header(rows[header_index]):
        require_price = True
        derive_unit_price = True
        extra_warnings = (*extra_warnings, "CJK_HEADER_TABLE")

    parsed: list[ParsedRow] = []
    mapped_columns = sorted(columns.values())
    for row_index, values in enumerate(
        rows[header_index + 1 :],
        start=header_index + 1,
    ):
        for expanded_values, expansion_warnings in _expanded_tabular_values(
            values,
            columns,
        ):
            fields = {
                field: _raw_text(
                    expanded_values[column]
                    if column < len(expanded_values)
                    else None
                )
                for field, column in columns.items()
            }
            if not any(fields.values()):
                continue
            item_name = fields.get("item_name")
            if _is_non_item_label(item_name):
                continue
            for field in ("quantity", "unit_price", "amount"):
                normalized = _normalized_money(fields.get(field))
                if normalized is not None:
                    fields[field] = normalized
            warnings = [*extra_warnings, *expansion_warnings]
            if _looks_like_multi_item_block(item_name):
                warnings.extend(
                    (
                        "MULTI_ITEM_BLOCK",
                        "PARSER_SOURCE_REVIEW_REQUIRED",
                    )
                )
            if (
                derive_unit_price
                and not fields.get("unit_price")
                and fields.get("amount")
                and fields.get("quantity")
            ):
                derived = _derived_unit_price(
                    fields["amount"],
                    fields["quantity"],
                )
                if derived is not None:
                    fields["unit_price"] = derived
                    warnings.extend(
                        (
                            "DERIVED_UNIT_PRICE",
                            "PARSER_SOURCE_REVIEW_REQUIRED",
                        )
                    )
            if require_price and not (
                _positive_number(fields.get("unit_price"))
                or _positive_number(fields.get("amount"))
            ):
                continue

            maker = fields.get("maker")
            if "spec" not in columns:
                warnings.append("SPEC_COLUMN_NOT_FOUND")
                if any(
                    source_warning in warnings
                    for source_warning in (
                        "PDF_COORDINATE_TABLE",
                        "PDF_LAYOUT_TEXT",
                    )
                ):
                    warnings.append("PARSER_SOURCE_REVIEW_REQUIRED")
            elif fields.get("spec") is None:
                warnings.append("SOURCE_SPEC_BLANK")
            if maker is not None and not _plausible_maker(maker):
                maker = None
                warnings.append("MAKER_REJECTED_NON_BRAND")

            source_row = row_index + 1 if row_numbers else None
            source_cells = None
            if cell_ranges:
                first_column = get_column_letter(mapped_columns[0] + 1)
                last_column = get_column_letter(mapped_columns[-1] + 1)
                source_cells = (
                    f"{first_column}{source_row}:{last_column}{source_row}"
                )
            parsed.append(
                ParsedRow(
                    sheet=sheet,
                    page=page,
                    row=source_row,
                    cells=source_cells,
                    item_name=item_name,
                    spec=fields.get("spec"),
                    unit=fields.get("unit"),
                    quantity=fields.get("quantity"),
                    unit_price=fields.get("unit_price"),
                    amount=fields.get("amount"),
                    maker=maker,
                    warnings=tuple(dict.fromkeys(warnings)),
                )
            )
    return parsed


def _expanded_tabular_values(
    values: list[Any],
    columns: dict[str, int],
) -> list[tuple[list[Any], tuple[str, ...]]]:
    parts = {
        field: _multiline_parts(
            values[column] if column < len(values) else None
        )
        for field, column in columns.items()
    }
    candidate_lengths = [
        len(parts[field])
        for field in ("quantity", "unit", "unit_price", "amount")
        if field in parts and len(parts[field]) > 1
    ]
    if not candidate_lengths:
        return [(values, ())]
    count = max(candidate_lengths)
    item_parts = parts.get("item_name", [])
    if len(item_parts) not in {count, count + 1}:
        return [(values, ("PARSER_SOURCE_REVIEW_REQUIRED",))]

    expanded: list[tuple[list[Any], tuple[str, ...]]] = []
    for item_index in range(count):
        row = list(values)
        valid = True
        for field, column in columns.items():
            field_parts = parts[field]
            if len(field_parts) == count:
                value = field_parts[item_index]
            elif field == "item_name" and len(field_parts) == count + 1:
                value = field_parts[item_index + 1]
            elif len(field_parts) == 1 and field in {"unit", "spec", "maker"}:
                value = field_parts[0]
            elif not field_parts:
                value = None
            else:
                valid = False
                break
            if column >= len(row):
                row.extend([None] * (column + 1 - len(row)))
            row[column] = value
        if not valid:
            return [(values, ("PARSER_SOURCE_REVIEW_REQUIRED",))]
        expanded.append((row, ("PDF_MULTILINE_TABLE_EXPANDED",)))
    return expanded


def _multiline_parts(value: Any) -> list[str]:
    raw = _raw_text(value)
    if raw is None:
        return []
    return [part.strip() for part in raw.splitlines() if part.strip()]


def _contains_cjk_quote_header(row: list[Any]) -> bool:
    aliases = {
        "\u54c1\u540d",
        "\u898f\u683c",
        "\u6578\u91cf",
        "\u6570\u91cf",
        "\u55ae\u4f4d",
        "\u5358\u4f4d",
        "\u55ae\u50f9",
        "\u5358\u4fa1",
        "\u91d1\u984d",
        "\uf90a\u984d",
        "\u91d1\u989d",
    }
    normalized = {_normalized_header(value) for value in row}
    return len(normalized.intersection(aliases)) >= 2


def _find_header(
    rows: list[list[Any]],
) -> tuple[int | None, dict[str, int]]:
    for row_index, row in enumerate(rows):
        width = max(
            (len(candidate) for candidate in rows[row_index : row_index + 4]),
            default=len(row),
        )
        combined = [None] * width
        for header_end in range(row_index, min(len(rows), row_index + 4)):
            for column_index, value in enumerate(rows[header_end]):
                raw = _raw_text(value)
                if raw:
                    current = _raw_text(combined[column_index])
                    combined[column_index] = (
                        f"{current}\n{raw}" if current else raw
                    )
            columns = _header_columns(combined)
            has_item = "item_name" in columns
            has_price = "unit_price" in columns or "amount" in columns
            if has_item and has_price:
                return header_end, columns
    return None, {}


def _header_columns(row: list[Any]) -> dict[str, int]:
    columns: dict[str, int] = {}
    normalized_headers = [_normalized_header(value) for value in row]
    device = "\uc7a5\uce58"
    content = "\ub0b4\uc6a9"
    if device in normalized_headers and content in normalized_headers:
        columns["item_name"] = normalized_headers.index(device)
        columns["spec"] = normalized_headers.index(content)
    for column_index, value in enumerate(row):
        if column_index in columns.values():
            continue
        field = _field_for_header(value)
        if field is not None and field not in columns:
            columns[field] = column_index
    if "item_name" not in columns and (
        "unit_price" in columns or "amount" in columns
    ):
        item_column = _fallback_item_column(row, columns)
        if item_column is not None:
            columns["item_name"] = item_column
    return columns


def _field_for_header(value: Any) -> str | None:
    raw = _raw_text(value)
    if not raw:
        return None
    candidates = (raw, *re.split(r"[\r\n]+", raw))
    for candidate in candidates:
        normalized = _normalized_header(candidate)
        if not normalized:
            continue
        for field, aliases in _FIELD_ALIASES.items():
            if normalized in aliases:
                return field
        undecorated = normalized.removesuffix("\uc6d0").removesuffix("krw")
        for field in ("unit_price", "amount"):
            if undecorated in _FIELD_ALIASES[field]:
                return field
    return None


def _normalized_header(value: Any) -> str:
    return _HEADER_SEPARATORS.sub(
        "",
        _raw_text(value) or "",
    ).casefold()


def _plausible_maker(value: str) -> bool:
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 120:
        return False
    if re.search(r"\d+\s*인\s*[x×*]\s*\d+\s*일", normalized, re.IGNORECASE):
        return False
    rejected_terms = (
        "이동일 제외",
        "인건비",
        "노무비",
        "작업일",
        "출장비",
    )
    return not any(term in normalized for term in rejected_terms)


def _fallback_item_column(
    row: list[Any],
    columns: dict[str, int],
) -> int | None:
    price_column = columns.get("unit_price", columns.get("amount"))
    if price_column is None:
        return None
    mapped_columns = set(columns.values())
    ignored = {"단위", "수량", "구분", "번호", "순번", "위치"}
    for column in range(price_column - 1, -1, -1):
        value = _HEADER_SEPARATORS.sub(
            "",
            _raw_text(row[column]) or "",
        ).casefold()
        if (
            value
            and column not in mapped_columns
            and not any(word in value for word in ignored)
        ):
            return column
    return None


def _is_non_item_label(value: str | None) -> bool:
    item = " ".join((value or "").split()).strip()
    if len(item) < 2 or _is_number(item):
        return True
    if re.fullmatch(r"\d+(?:[._-]\d+)+[.)]?", item):
        return True
    normalized = re.sub(r"[\s_.\-/():：]+", "", item).casefold()
    summary_labels = {
        "total",
        "grandtotal",
        "subtotal",
        "vat",
        "\ud569\uacc4",
        "\ucd1d\uacc4",
        "\uc18c\uacc4",
        "\uacf5\uae09\uac00\uc561",
        "\ubd80\uac00\uc138",
        "\uacac\uc801\uae08\uc561",
        "\uacf5\uc0ac\uae08\uc561",
        "\ubb38\uc11c\ubc88\ud638",
        "quotationno",
        "quoteno",
        "refno",
        "opno",
    }
    if normalized in summary_labels:
        return True
    summary_markers = (
        "subtotal",
        "grandtotal",
        "totalamount",
        "합계",
        "총계",
        "소계",
        "合計",
        "總計",
        "小計",
    )
    return any(marker.casefold() in normalized for marker in summary_markers)


def _looks_like_multi_item_block(value: str | None) -> bool:
    if not value or "\n" not in value:
        return False
    numbered_lines = re.findall(
        r"(?:^|\n)\s*\d{1,3}\s*[.)]",
        value,
    )
    return len(numbered_lines) >= 2


def _normalized_money(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = re.sub(r"(?:KRW|JPY|USD|EUR)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[₩￦$\¥€,\s]", "", text)
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None
    if negative and not text.startswith("-"):
        text = f"-{text}"
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return format(number, "f")


def _derived_unit_price(
    amount: str | None,
    quantity: str | None,
) -> str | None:
    amount_text = _normalized_money(amount)
    quantity_text = _normalized_money(quantity)
    if amount_text is None or quantity_text is None:
        return None
    amount_number = Decimal(amount_text)
    quantity_number = Decimal(quantity_text)
    if amount_number <= 0 or quantity_number <= 0:
        return None
    result = amount_number / quantity_number
    return format(result.quantize(Decimal("0.000001")).normalize(), "f")


def _raw_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _parse_fixed_column_fallback(
    rows: list[list[Any]],
    *,
    sheet: str | None,
    page: int | None,
) -> list[ParsedRow]:
    """Conservatively recognize the observed C/E/F/H headerless layout."""
    parsed: list[ParsedRow] = []
    for row_index, values in enumerate(rows, start=1):
        if len(values) < 8:
            continue
        item_name = _raw_text(values[2])
        quantity = _raw_text(values[4])
        unit = _raw_text(values[5])
        unit_price = _raw_text(values[7])
        if not _is_safe_fixed_column_row(
            item_name,
            quantity,
            unit,
            unit_price,
        ):
            continue
        parsed.append(
            ParsedRow(
                sheet=sheet,
                page=page,
                row=row_index,
                cells=f"C{row_index}:H{row_index}",
                item_name=item_name,
                spec=None,
                unit=unit,
                quantity=quantity,
                unit_price=unit_price,
                amount=None,
                maker=None,
                warnings=(
                    "FALLBACK_FIXED_C_E_F_H",
                    "SPEC_COLUMN_NOT_FOUND",
                ),
            )
        )
    return parsed


def _is_safe_fixed_column_row(
    item_name: str | None,
    quantity: str | None,
    unit: str | None,
    unit_price: str | None,
) -> bool:
    item = (item_name or "").strip()
    normalized_unit = (unit or "").strip()
    return bool(
        len(item) >= 2
        and not _is_number(item)
        and normalized_unit
        and len(normalized_unit) <= 20
        and _positive_number(quantity)
        and _positive_number(unit_price)
    )


def _positive_number(value: str | None) -> bool:
    try:
        return Decimal((value or "").replace(",", "").strip()) > 0
    except InvalidOperation:
        return False


def _number_at_least(value: str | None, minimum: Decimal) -> bool:
    try:
        return Decimal((value or "").replace(",", "").strip()) >= minimum
    except InvalidOperation:
        return False


def _is_number(value: str) -> bool:
    try:
        Decimal(value.replace(",", "").strip())
        return True
    except InvalidOperation:
        return False
