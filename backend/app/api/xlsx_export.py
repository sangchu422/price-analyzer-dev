"""Shared helper for building downloadable Excel (.xlsx) responses."""

from __future__ import annotations

import io
from urllib.parse import quote

from fastapi import Response
from openpyxl import Workbook

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def build_xlsx_response(
    *,
    sheet_title: str,
    headers: list[str],
    rows: list[list[object]],
    filename: str,
) -> Response:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_title[:31]
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=buffer.getvalue(),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": disposition},
    )
