from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.documents import _safe_error_detail
from app.cleansing.models import CleanDecision
from app.documents.models import SourceDocument, SourceVariant
from app.quotes.models import RawQuoteItem


def _write_quote(
    path: Path,
    *,
    item_name: str,
    unit_price: int = 1000,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "내역"
    sheet.append(["품명", "규격", "단위", "수량", "단가", "금액"])
    sheet.append([item_name, "A-1", "EA", 2, unit_price, unit_price * 2])
    workbook.save(path)


def test_scan_prefers_unlocked_and_is_idempotent(
    client: TestClient,
    api_session: Session,
    quote_root: Path,
) -> None:
    original = quote_root / "설비 견적.xlsx"
    unlocked = quote_root / "설비 견적_보안해제.xlsx"
    _write_quote(original, item_name="PROTECTED")
    _write_quote(unlocked, item_name="UNLOCKED")

    first = client.post("/api/documents/scan")
    second = client.post("/api/documents/scan")

    assert first.status_code == 200
    assert first.json()["documents_succeeded"] == 1
    assert first.json()["documents_failed"] == 0
    assert first.json()["raw_items_created"] == 1
    assert first.json()["decisions_created"] == 1
    assert second.status_code == 200
    assert second.json()["raw_items_created"] == 0
    assert second.json()["decisions_created"] == 0
    assert api_session.scalar(select(func.count(SourceVariant.id))) == 2
    assert api_session.scalar(select(func.count(RawQuoteItem.id))) == 1
    assert api_session.scalar(select(func.count(CleanDecision.id))) == 1
    assert api_session.scalar(select(RawQuoteItem)).item_name_raw == "UNLOCKED"


def test_scan_isolates_bad_documents_and_reports_failures(
    client: TestClient,
    api_session: Session,
    quote_root: Path,
) -> None:
    _write_quote(quote_root / "good.xlsx", item_name="GOOD")
    bad = quote_root / "bad.xlsx"
    bad.write_bytes(b"not an xlsx archive")

    response = client.post("/api/documents/scan")

    assert response.status_code == 200
    payload = response.json()
    assert payload["documents_found"] == 2
    assert payload["documents_succeeded"] == 1
    assert payload["documents_failed"] == 1
    assert payload["failures"][0]["logical_name"] == "bad"
    assert payload["failures"][0]["error_type"]
    assert payload["failures"][0]["detail"]
    assert api_session.scalar(select(func.count(SourceDocument.id))) == 1
    assert api_session.scalar(select(func.count(RawQuoteItem.id))) == 1


def test_documents_returns_variants_preference_and_current_counts(
    client: TestClient,
    quote_root: Path,
) -> None:
    _write_quote(quote_root / "nested" / "quote.xlsx", item_name="LOCKED")
    _write_quote(
        quote_root / "nested" / "quote_보안해제.xlsx",
        item_name="UNLOCKED",
    )
    assert client.post("/api/documents/scan").status_code == 200

    response = client.get("/api/documents", params={"limit": 10, "offset": 0})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    document = payload["items"][0]
    assert document["logical_name"] == "nested/quote"
    assert [item["path"] for item in document["variants"]] == [
        "nested/quote.xlsx",
        "nested/quote_보안해제.xlsx",
    ]
    selected = [
        item
        for item in document["variants"]
        if item["selected_for_parsing_at_ingest"]
    ]
    assert [item["path"] for item in selected] == [
        "nested/quote_보안해제.xlsx"
    ]
    assert document["preferred_variant"]["path"] == (
        "nested/quote_보안해제.xlsx"
    )
    assert document["counts"] == {
        "raw_items": 1,
        "INCLUDED": 1,
        "EXCLUDED": 0,
        "REVIEW_REQUIRED": 0,
        "UNDECIDED": 0,
    }


def test_scan_empty_folder_is_explicit_not_silent(
    client: TestClient,
) -> None:
    response = client.post("/api/documents/scan")

    assert response.status_code == 200
    assert response.json() == {
        "files_found": 0,
        "documents_found": 0,
        "documents_succeeded": 0,
        "documents_failed": 0,
        "variants_created": 0,
        "raw_items_created": 0,
        "decisions_created": 0,
        "failures": [],
    }


def test_scan_error_detail_does_not_leak_configured_absolute_root(
    quote_root: Path,
) -> None:
    detail = f"failed to parse {quote_root / 'nested' / 'bad.xlsx'}"

    safe = _safe_error_detail(RuntimeError(detail), quote_root)

    assert str(quote_root) not in safe
    assert "nested" in safe
    assert "bad.xlsx" in safe
