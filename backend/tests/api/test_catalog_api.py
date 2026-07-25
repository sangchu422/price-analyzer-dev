from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.catalog.models import (
    DocumentMetadataVersion,
    ItemMembershipDecision,
    StandardItemVersion,
)
from app.cleansing.models import CleanDecision, CleanStatus
from app.documents.models import SourceDocument, SourceVariant
from app.quotes.models import RawQuoteItem


def _source(
    session: Session,
    *,
    status: CleanStatus = CleanStatus.INCLUDED,
) -> tuple[SourceDocument, RawQuoteItem]:
    document = SourceDocument(logical_name="quotes/api-sample.xlsx")
    variant = SourceVariant(
        document=document,
        path="quotes/api-sample.xlsx",
        sha256="b" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    raw = RawQuoteItem(
        source_variant=variant,
        source_sheet="Sheet1",
        source_row=7,
        source_cells="A7:G7",
        item_name_raw="Bearing",
        spec_raw="6204 ZZ",
        unit_raw="EA",
        unit_price_raw="120",
        parser_name="xlsx",
        parser_version="1",
    )
    session.add_all(
        [
            document,
            CleanDecision(
                raw_item=raw,
                status=status,
                reason_code="VALID",
                item_name_norm="BEARING",
                spec_norm="6204 ZZ",
                unit_norm="EA",
                unit_price=Decimal("120"),
                rule_version="clean-v1",
            ),
        ]
    )
    session.commit()
    return document, raw


def _create_standard_item(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/catalog/standard-items",
        json={
            "canonical_name": "BALL BEARING",
            "canonical_spec": "6204-ZZ",
            "canonical_unit": "EA",
            "aliases": ["BEARING"],
            "created_by": "buyer-1",
            "reason_detail": "create approved canonical item",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_candidate_api_returns_evidence_without_auto_matching(
    client: TestClient,
    api_session: Session,
) -> None:
    _, raw = _source(api_session)
    item = _create_standard_item(client)

    response = client.get(f"/api/catalog/raw-items/{raw.id}/candidates")

    assert response.status_code == 200
    payload = response.json()
    assert payload["match_status"] == "CANDIDATE"
    assert payload["candidates"][0]["standard_item_id"] == item["id"]
    assert payload["source"]["path"] == "quotes/api-sample.xlsx"
    assert payload["source"]["row"] == 7
    assert payload["current_cleansing_decision"]["status"] == "INCLUDED"
    assert payload["candidates"][0]["embedding_status"] == "DISABLED"
    assert (
        api_session.scalar(select(func.count(ItemMembershipDecision.id))) == 0
    )


def test_match_approval_uses_optimistic_concurrency(
    client: TestClient,
    api_session: Session,
) -> None:
    _, raw = _source(api_session)
    item = _create_standard_item(client)
    body = {
        "standard_item_id": item["id"],
        "status": "MATCHED",
        "expected_current_decision_id": None,
        "candidate_score": "0.920000",
        "method": "MANUAL_CANDIDATE",
        "evidence": {"matched_tokens": ["6204-ZZ"]},
        "decided_by": "buyer-1",
        "reason_detail": "model and unit confirmed",
    }

    first = client.post(
        f"/api/catalog/raw-items/{raw.id}/memberships",
        json=body,
    )
    stale = client.post(
        f"/api/catalog/raw-items/{raw.id}/memberships",
        json=body,
    )

    assert first.status_code == 201, first.text
    assert stale.status_code == 409
    assert stale.json()["detail"]["error_code"] == "STALE_CATALOG_DECISION"
    assert stale.json()["detail"]["current_decision_id"] == first.json()["id"]


def test_rejected_or_not_included_rows_are_never_matched(
    client: TestClient,
    api_session: Session,
) -> None:
    _, raw = _source(api_session, status=CleanStatus.EXCLUDED)
    item = _create_standard_item(client)

    response = client.post(
        f"/api/catalog/raw-items/{raw.id}/memberships",
        json={
            "standard_item_id": item["id"],
            "status": "MATCHED",
            "expected_current_decision_id": None,
            "candidate_score": None,
            "method": "MANUAL",
            "evidence": {},
            "decided_by": "buyer-1",
            "reason_detail": "attempted override",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "RAW_ITEM_NOT_INCLUDED"


def test_item_metadata_and_document_metadata_append_versions(
    client: TestClient,
    api_session: Session,
) -> None:
    document, _ = _source(api_session)
    item = _create_standard_item(client)
    item_update = client.post(
        f"/api/catalog/standard-items/{item['id']}/versions",
        json={
            "canonical_name": "DEEP GROOVE BALL BEARING",
            "canonical_spec": "6204-ZZ",
            "canonical_unit": "EA",
            "aliases": ["BEARING", "BALL BEARING"],
            "expected_current_version_id": item["current_version"]["id"],
            "created_by": "buyer-2",
            "reason_detail": "clarify canonical name",
        },
    )
    first_metadata = client.post(
        f"/api/catalog/documents/{document.id}/metadata",
        json={
            "supplier_name": "SUPPLIER A",
            "quote_date": "2026-07-01",
            "project_name": "PUNE LINE",
            "expected_current_version_id": None,
            "decided_by": "buyer-1",
            "reason_detail": "read from quote header",
        },
    )
    second_metadata = client.post(
        f"/api/catalog/documents/{document.id}/metadata",
        json={
            "supplier_name": "SUPPLIER A CO.",
            "quote_date": "2026-07-01",
            "project_name": "PUNE LINE",
            "expected_current_version_id": first_metadata.json()["id"],
            "decided_by": "buyer-2",
            "reason_detail": "correct legal supplier name",
        },
    )

    assert item_update.status_code == 201, item_update.text
    assert item_update.json()["version_number"] == 2
    assert first_metadata.status_code == 201, first_metadata.text
    assert second_metadata.status_code == 201, second_metadata.text
    assert second_metadata.json()["version_number"] == 2
    assert second_metadata.json()["reason_detail"] == (
        "correct legal supplier name"
    )
    assert (
        api_session.scalar(select(func.count(StandardItemVersion.id))) == 2
    )
    assert (
        api_session.scalar(select(func.count(DocumentMetadataVersion.id))) == 2
    )


def test_stale_metadata_write_is_atomic(
    client: TestClient,
    api_session: Session,
) -> None:
    document, _ = _source(api_session)
    body = {
        "supplier_name": "SUPPLIER A",
        "quote_date": None,
        "project_name": None,
        "expected_current_version_id": None,
        "decided_by": "buyer-1",
        "reason_detail": "initial metadata",
    }
    first = client.post(
        f"/api/catalog/documents/{document.id}/metadata",
        json=body,
    )
    stale = client.post(
        f"/api/catalog/documents/{document.id}/metadata",
        json=body,
    )

    assert first.status_code == 201
    assert stale.status_code == 409
    assert stale.json()["detail"]["error_code"] == "STALE_CATALOG_DECISION"
    assert stale.json()["detail"]["current_version_id"] == first.json()["id"]
    assert (
        api_session.scalar(select(func.count(DocumentMetadataVersion.id))) == 1
    )
