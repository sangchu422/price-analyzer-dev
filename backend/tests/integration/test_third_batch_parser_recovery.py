from __future__ import annotations

from pathlib import Path

import pytest

from app.ingestion.readers import read_quote


THIRD_BATCH_ROOT = (
    Path(__file__).resolve().parents[3] / "견적서" / "3차 학습"
)


def _source_in(case_directory: str, extension: str) -> Path:
    matches = [
        path
        for path in THIRD_BATCH_ROOT.rglob(f"*.{extension}")
        if case_directory in path.parts
    ]
    if not matches:
        pytest.skip("local third-training source is not available")
    return sorted(matches)[0]


def test_actual_split_header_pdf_is_recovered() -> None:
    rows = read_quote(_source_in("A2017080001558", "pdf"))

    assert len(rows) >= 3
    assert any("FANUC" in (row.item_name or "") for row in rows)
    assert all(row.page is not None for row in rows)


def test_actual_cjk_header_xls_is_recovered_as_review_candidate() -> None:
    rows = read_quote(_source_in("A2018030002785", "xls"))

    assert len(rows) >= 10
    assert any("panel" in (row.item_name or "").casefold() for row in rows)
    assert any(
        "PARSER_SOURCE_REVIEW_REQUIRED" in row.warnings for row in rows
    )


def test_actual_quote_zip_preserves_member_evidence() -> None:
    rows = read_quote(_source_in("A2026050004991", "zip"))

    assert len(rows) >= 100
    assert all("ARCHIVE_MEMBER" in row.warnings for row in rows)
    assert all("::" in (row.sheet or "") for row in rows)
