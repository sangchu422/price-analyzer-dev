"""Reader-neutral extraction of quote rows with source provenance."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import xlrd
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from pypdf import PdfReader


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
        "자재명",
        "장치",
        "내용",
        "item",
        "itemname",
        "description",
    ),
    "spec": ("규격", "사양", "spec", "specification", "model"),
    "unit": ("단위", "unit"),
    "quantity": ("수량", "qty", "quantity"),
    "unit_price": ("단가", "unitprice", "price"),
    "amount": ("금액", "합계", "amount", "total"),
    "maker": ("메이커", "제조사", "브랜드", "maker", "manufacturer"),
}
_HEADER_SEPARATORS = re.compile(r"[\s_\-./()\[\]:]+")
_PDF_COLUMNS = re.compile(r"\t+|\s{2,}")


def read_quote(path: Path) -> list[ParsedRow]:
    """Read a supported quote without normalizing its field values."""
    extension = path.suffix.lower()
    if extension == ".xlsx":
        return read_xlsx(path)
    if extension == ".xls":
        return read_xls(path)
    if extension == ".pdf":
        return read_pdf(path)
    raise ValueError(f"unsupported quote extension: {path.suffix}")


def read_xlsx(path: Path) -> list[ParsedRow]:
    workbook = load_workbook(path, data_only=True, read_only=True)
    try:
        parsed: list[ParsedRow] = []
        for sheet in workbook.worksheets:
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
    parsed: list[ParsedRow] = []
    for sheet in workbook.sheets():
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


def read_pdf(path: Path) -> list[ParsedRow]:
    parsed: list[ParsedRow] = []
    for page_number, page in enumerate(PdfReader(str(path)).pages, start=1):
        lines = (page.extract_text() or "").splitlines()
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
    return parsed


def _parse_tabular_rows(
    rows: list[list[Any]],
    *,
    sheet: str | None,
    page: int | None,
    row_numbers: bool,
    cell_ranges: bool,
) -> list[ParsedRow]:
    header_index, columns = _find_header(rows)
    if header_index is None:
        return []

    parsed: list[ParsedRow] = []
    mapped_columns = sorted(columns.values())
    for row_index, values in enumerate(
        rows[header_index + 1 :],
        start=header_index + 1,
    ):
        fields = {
            field: _raw_text(
                values[column] if column < len(values) else None
            )
            for field, column in columns.items()
        }
        if not any(fields.values()):
            continue

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
                item_name=fields.get("item_name"),
                spec=fields.get("spec"),
                unit=fields.get("unit"),
                quantity=fields.get("quantity"),
                unit_price=fields.get("unit_price"),
                amount=fields.get("amount"),
                maker=fields.get("maker"),
            )
        )
    return parsed


def _find_header(
    rows: list[list[Any]],
) -> tuple[int | None, dict[str, int]]:
    for row_index, row in enumerate(rows):
        columns: dict[str, int] = {}
        for column_index, value in enumerate(row):
            field = _field_for_header(value)
            if field is not None and field not in columns:
                columns[field] = column_index
        if "item_name" not in columns and (
            "unit_price" in columns or "amount" in columns
        ):
            item_column = _fallback_item_column(row, columns)
            if item_column is not None:
                columns["item_name"] = item_column
        has_item = "item_name" in columns
        has_price = (
            "unit_price" in columns or "amount" in columns
        )
        if has_item and has_price:
            return row_index, columns
    return None, {}


def _field_for_header(value: Any) -> str | None:
    normalized = _HEADER_SEPARATORS.sub(
        "",
        _raw_text(value) or "",
    ).casefold()
    if not normalized:
        return None
    for field, aliases in _FIELD_ALIASES.items():
        if normalized in aliases:
            return field
    undecorated = normalized.removesuffix("원").removesuffix("krw")
    for field in ("unit_price", "amount"):
        if undecorated in _FIELD_ALIASES[field]:
            return field
    return None


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


def _raw_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
