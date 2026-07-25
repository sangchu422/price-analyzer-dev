from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

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
    assert payload["failures"][0]["error_code"] == "UNREADABLE_SOURCE"
    assert payload["failures"][0]["detail"] == (
        "source file could not be read"
    )
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


def test_document_endpoints_publish_explicit_response_schemas(
    client: TestClient,
) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    list_schema = paths["/api/documents"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    scan_schema = paths["/api/documents/scan"]["post"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]

    assert list_schema["$ref"].endswith("/DocumentListResponse")
    assert scan_schema["$ref"].endswith("/ScanResponse")


def test_document_current_decisions_are_loaded_only_for_requested_page(
    client: TestClient,
    api_session: Session,
    quote_root: Path,
) -> None:
    for index in range(6):
        _write_quote(
            quote_root / f"{index:02d}.xlsx",
            item_name=f"ITEM {index}",
        )
    assert client.post("/api/documents/scan").status_code == 200
    api_session.expunge_all()
    loaded_ids: list[int] = []

    def record_load(decision: CleanDecision, context: object) -> None:
        loaded_ids.append(decision.raw_item_id)

    event.listen(CleanDecision, "load", record_load)
    try:
        response = client.get(
            "/api/documents",
            params={"limit": 1, "offset": 0},
        )
    finally:
        event.remove(CleanDecision, "load", record_load)

    assert response.status_code == 200
    assert response.json()["total"] == 6
    assert len(loaded_ids) == 1


def test_scan_reports_escaped_resolved_candidate_and_keeps_valid_document(
    client: TestClient,
    api_session: Session,
    quote_root: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    valid = quote_root / "valid.xlsx"
    escaped = tmp_path / "outside.xlsx"
    _write_quote(valid, item_name="VALID")
    _write_quote(escaped, item_name="OUTSIDE")
    monkeypatch.setattr(
        "app.api.documents._scan_supported_files",
        lambda root: [escaped, valid],
    )

    response = client.post("/api/documents/scan")

    assert response.status_code == 200
    payload = response.json()
    assert payload["documents_succeeded"] == 1
    assert payload["documents_failed"] == 1
    assert payload["failures"] == [
        {
            "logical_name": "outside",
            "error_code": "PATH_OUTSIDE_ROOT",
            "detail": "source path resolves outside configured quote root",
        }
    ]
    assert api_session.scalar(select(func.count(RawQuoteItem.id))) == 1


def test_scan_rejects_file_symlink_that_escapes_configured_root(
    client: TestClient,
    quote_root: Path,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.xlsx"
    _write_quote(outside, item_name="OUTSIDE")
    link = quote_root / "escaped-link.xlsx"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")

    response = client.post("/api/documents/scan")

    assert response.status_code == 200
    assert response.json()["documents_succeeded"] == 0
    assert response.json()["documents_failed"] == 1
    assert response.json()["failures"][0]["error_code"] == "PATH_OUTSIDE_ROOT"
    assert str(tmp_path) not in response.text


def test_scan_does_not_disguise_unexpected_programmer_errors(
    client: TestClient,
    quote_root: Path,
    monkeypatch,
) -> None:
    _write_quote(quote_root / "valid.xlsx", item_name="VALID")

    def fail_unexpectedly(*args, **kwargs):
        raise ValueError("programming bug with sensitive internals")

    monkeypatch.setattr(
        "app.api.documents.ingest_group",
        fail_unexpectedly,
    )

    with pytest.raises(ValueError, match="programming bug"):
        client.post("/api/documents/scan")
