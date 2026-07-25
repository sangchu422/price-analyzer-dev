from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.catalog.models import (
    DocumentMetadataVersion,
    ItemMembershipDecision,
    MembershipStatus,
    StandardItem,
    StandardItemVersion,
)
from app.cleansing.models import CleanDecision, CleanStatus
from app.documents.models import SourceDocument, SourceVariant
from app.quotes.models import RawQuoteItem


def _seed(session: Session) -> StandardItem:
    item = StandardItem()
    item.versions.append(
        StandardItemVersion(
            version_number=1,
            canonical_name="BEARING",
            canonical_spec="6204 ZZ",
            canonical_unit="EA",
            created_by="buyer",
        )
    )
    for row, price, supplier in [(1, "100", "A"), (2, "120", "B")]:
        document = SourceDocument(logical_name=f"quote-{row}.xlsx")
        variant = SourceVariant(
            document=document,
            path=f"quote-{row}.xlsx",
            sha256=f"{row:064x}",
            extension=".xlsx",
            security_state="UNLOCKED",
            selected_for_parsing_at_ingest=True,
        )
        raw = RawQuoteItem(
            source_variant=variant,
            source_sheet="Sheet1",
            source_row=row,
            item_name_raw="BEARING",
            parser_name="xlsx",
            parser_version="1",
        )
        clean = CleanDecision(
            raw_item=raw,
            status=CleanStatus.INCLUDED,
            reason_code="VALID",
            item_name_norm="BEARING",
            spec_norm="6204 ZZ",
            unit_norm="EA",
            unit_price=Decimal(price),
            rule_version="clean-v1",
        )
        membership = ItemMembershipDecision(
            raw_item=raw,
            standard_item=item,
            status=MembershipStatus.MATCHED,
            method="MANUAL",
            evidence_json="{}",
            decided_by="buyer",
        )
        metadata = DocumentMetadataVersion(
            source_document=document,
            version_number=1,
            supplier_name=supplier,
            quote_date=date(2026, 7, row),
            project_name=None,
            decided_by="buyer",
        )
        session.add_all([document, clean, membership, metadata])
    session.add(item)
    session.commit()
    return item


def test_pricing_api_draft_approval_and_history(
    client: TestClient, api_session: Session
) -> None:
    item = _seed(api_session)
    draft_response = client.get(
        f"/api/pricing/standard-items/{item.id}/draft"
    )
    assert draft_response.status_code == 200
    draft = draft_response.json()
    assert draft["prices"] == {
        "minimum": "100.000000",
        "median": "110.000000",
        "average": "110.000000",
        "maximum": "120.000000",
    }
    assert draft["supplier_count"] == 2
    assert draft["latest_quote_date"] == "2026-07-02"
    assert draft["observations"][0]["source"]["path"] == "quote-1.xlsx"

    approved = client.post(
        f"/api/pricing/standard-items/{item.id}/versions",
        json={
            "expected_fingerprint": draft["fingerprint"],
            "expected_current_version_id": None,
            "approved_by": "buyer-1",
        },
    )
    assert approved.status_code == 201
    assert approved.json()["version_number"] == 1
    assert approved.json()["draft_fingerprint"] == draft["fingerprint"]
    assert approved.json()["excluded_count"] == 0
    assert approved.json()["review_required_count"] == 0
    assert approved.json()["exclusions"] == []
    history = client.get(
        f"/api/pricing/standard-items/{item.id}/versions"
    )
    assert history.status_code == 200
    assert len(history.json()["versions"]) == 1
    assert len(history.json()["versions"][0]["observations"]) == 2
    assert history.json()["versions"][0]["draft_fingerprint"] == (
        draft["fingerprint"]
    )


def test_pricing_api_returns_typed_stale_error(
    client: TestClient, api_session: Session
) -> None:
    item = _seed(api_session)
    draft = client.get(
        f"/api/pricing/standard-items/{item.id}/draft"
    ).json()
    first = client.post(
        f"/api/pricing/standard-items/{item.id}/versions",
        json={
            "expected_fingerprint": draft["fingerprint"],
            "expected_current_version_id": None,
            "approved_by": "buyer-1",
        },
    )
    assert first.status_code == 201
    stale = client.post(
        f"/api/pricing/standard-items/{item.id}/versions",
        json={
            "expected_fingerprint": draft["fingerprint"],
            "expected_current_version_id": None,
            "approved_by": "buyer-1",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["error_code"] == "PRICE_DRAFT_CHANGED"


def test_pricing_api_returns_typed_not_found(client: TestClient) -> None:
    response = client.get("/api/pricing/standard-items/999/draft")
    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == (
        "STANDARD_ITEM_NOT_FOUND"
    )


def test_price_history_returns_frozen_exclusion_audit(
    client: TestClient, api_session: Session
) -> None:
    item = _seed(api_session)
    document = SourceDocument(logical_name="excluded.xlsx")
    variant = SourceVariant(
        document=document,
        path="excluded.xlsx",
        sha256="f" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    raw = RawQuoteItem(
        source_variant=variant,
        source_row=7,
        item_name_raw="BEARING",
        parser_name="xlsx",
        parser_version="1",
    )
    excluded = CleanDecision(
        raw_item=raw,
        status=CleanStatus.EXCLUDED,
        reason_code="OUTLIER",
        unit_price=Decimal("999"),
        rule_version="clean-v1",
    )
    membership = ItemMembershipDecision(
        raw_item=raw,
        standard_item=item,
        status=MembershipStatus.MATCHED,
        method="MANUAL",
        evidence_json="{}",
        decided_by="buyer",
    )
    api_session.add_all([document, excluded, membership])
    api_session.commit()
    draft = client.get(
        f"/api/pricing/standard-items/{item.id}/draft"
    ).json()
    approved = client.post(
        f"/api/pricing/standard-items/{item.id}/versions",
        json={
            "expected_fingerprint": draft["fingerprint"],
            "expected_current_version_id": None,
            "approved_by": "buyer",
        },
    ).json()
    assert approved["draft_fingerprint"] == draft["fingerprint"]
    assert approved["excluded_count"] == 1
    assert approved["review_required_count"] == 0
    assert approved["exclusions"][0] == {
        "raw_item_id": raw.id,
        "reason": "EXCLUDED",
        "clean_decision_id": excluded.id,
        "clean_status": "EXCLUDED",
        "membership_decision_id": membership.id,
        "membership_status": "MATCHED",
        "membership_standard_item_id": item.id,
        "source": {
            "document_id": document.id,
            "logical_name": "excluded.xlsx",
            "variant_id": variant.id,
            "path": "excluded.xlsx",
            "sheet": None,
            "page": None,
            "row": 7,
        },
    }
    api_session.add(
        CleanDecision(
            raw_item_id=raw.id,
            status=CleanStatus.INCLUDED,
            reason_code="RESTORED",
            unit_price=Decimal("999"),
            unit_norm="EA",
            rule_version="clean-v2",
        )
    )
    api_session.commit()
    history = client.get(
        f"/api/pricing/standard-items/{item.id}/versions"
    ).json()["versions"][0]
    assert history["excluded_count"] == 1
    assert history["exclusions"] == approved["exclusions"]
