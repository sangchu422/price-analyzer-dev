from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.catalog.models import ItemMembershipDecision
from app.cleansing.models import CleanDecision, CleanStatus
from app.documents.models import SourceDocument, SourceVariant
from app.quotes.models import RawQuoteItem
from app.standard_database.models import (
    QuoteDocumentPurpose,
    QuoteDocumentRole,
)


def _document(
    session: Session,
    *,
    name: str = "quotes/new.xlsx",
    rows: int = 2,
) -> SourceDocument:
    document = SourceDocument(logical_name=name)
    variant = SourceVariant(
        document=document,
        path=name,
        sha256="a" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    for row in range(1, rows + 1):
        raw = RawQuoteItem(
            source_variant=variant,
            source_sheet="Sheet1",
            source_row=row,
            source_cells=f"A{row}:G{row}",
            item_name_raw=f"CUSTOM ITEM {row}",
            spec_raw=f"ZZ-{row}",
            unit_raw="EA",
            unit_price_raw=str(row * 100),
            parser_name="xlsx",
            parser_version="reader-v1",
        )
        session.add(
            CleanDecision(
                raw_item=raw,
                status=CleanStatus.INCLUDED,
                reason_code="VALID",
                item_name_norm=f"CUSTOM ITEM {row}",
                spec_norm=f"ZZ-{row}",
                unit_norm="EA",
                unit_price=Decimal(row * 100),
                rule_version="clean-v1",
            )
        )
    session.add(document)
    session.flush()
    session.add(
        QuoteDocumentRole(
            document_id=document.id,
            purpose=QuoteDocumentPurpose.INCOMING_BID,
            decided_by="test-submitter",
            reason_detail="API analysis fixture",
        )
    )
    session.commit()
    return document


def test_analysis_document_list_and_typed_detail(
    client: TestClient,
    api_session: Session,
) -> None:
    document = _document(api_session)

    listing = client.get("/api/analysis/documents?limit=10&offset=0")
    detail = client.get(
        f"/api/analysis/documents/{document.id}?limit=1"
    )

    assert listing.status_code == 200
    assert listing.json()["items"] == [
        {
            "id": document.id,
            "logical_name": "quotes/new.xlsx",
            "raw_item_count": 2,
            "included_count": 2,
            "excluded_count": 0,
            "review_required_count": 0,
            "undecided_count": 0,
            "analysis_ready": True,
        }
    ]
    assert listing.json()["total"] == 1
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["document"]["id"] == document.id
    assert len(payload["lines"]) == 1
    assert payload["lines"][0]["match_status"] == "NO_MATCH"
    assert payload["lines"][0]["canonical_name"] is None
    assert payload["lines"][0]["canonical_spec"] is None
    assert payload["lines"][0]["canonical_unit"] is None
    assert payload["lines"][0]["source"]["path"] == "quotes/new.xlsx"
    assert payload["next_cursor"] == payload["lines"][0]["raw_item_id"]


def test_analysis_list_excludes_historical_and_unclassified_documents(
    client: TestClient,
    api_session: Session,
) -> None:
    incoming = _document(api_session, name="incoming.xlsx", rows=1)
    historical = SourceDocument(logical_name="historical.xlsx")
    unclassified = SourceDocument(logical_name="unclassified.xlsx")
    api_session.add_all([historical, unclassified])
    api_session.flush()
    api_session.add(
        QuoteDocumentRole(
            document_id=historical.id,
            purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
            decided_by="data-owner",
            reason_detail="training evidence",
        )
    )
    api_session.commit()

    response = client.get("/api/analysis/documents?limit=10")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [item["id"] for item in response.json()["items"]] == [incoming.id]


def test_analysis_list_uses_only_latest_document_role(
    client: TestClient,
    api_session: Session,
) -> None:
    document = _document(api_session, name="reclassified.xlsx", rows=1)
    current = api_session.scalar(
        select(QuoteDocumentRole)
        .where(QuoteDocumentRole.document_id == document.id)
        .order_by(QuoteDocumentRole.id.desc())
    )
    api_session.add(
        QuoteDocumentRole(
            document_id=document.id,
            purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
            supersedes_role_id=current.id,
            decided_by="data-owner",
            reason_detail="corrected classification",
        )
    )
    api_session.commit()

    response = client.get("/api/analysis/documents?limit=10")

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["items"] == []


def test_detail_cursor_and_status_filter_are_stable(
    client: TestClient,
    api_session: Session,
) -> None:
    document = _document(api_session, rows=3)

    first = client.get(
        f"/api/analysis/documents/{document.id}"
        "?limit=1&match_status=NO_MATCH"
    ).json()
    second = client.get(
        f"/api/analysis/documents/{document.id}"
        f"?limit=1&match_status=NO_MATCH&after_id={first['next_cursor']}"
    ).json()

    assert first["lines"][0]["raw_item_id"] < (
        second["lines"][0]["raw_item_id"]
    )
    assert first["next_cursor"] is not None


def test_refresh_candidates_never_creates_membership(
    client: TestClient,
    api_session: Session,
) -> None:
    document = _document(api_session)
    before = api_session.scalar(
        select(func.count(ItemMembershipDecision.id))
    )

    response = client.post(
        f"/api/analysis/documents/{document.id}/refresh-candidates"
    )

    assert response.status_code == 200
    assert response.json()["refreshed_candidate_rows"] == 2
    assert response.json()["membership_rows_created"] == 0
    assert (
        api_session.scalar(select(func.count(ItemMembershipDecision.id)))
        == before
    )


def test_analysis_api_rejects_missing_documents_and_bad_page_bounds(
    client: TestClient,
) -> None:
    assert client.get("/api/analysis/documents/999").status_code == 404
    assert (
        client.get("/api/analysis/documents/999?limit=101").status_code
        == 422
    )
    assert (
        client.get("/api/analysis/documents?offset=-1").status_code
        == 422
    )
