from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.catalog.models import DocumentMetadataVersion
from app.catalog.models import (
    ItemMembershipDecision,
    MembershipStatus,
    StandardItemVersion,
    StandardItem,
)
from app.cleansing.models import CleanDecision, CleanStatus
from app.documents.models import SourceDocument, SourceVariant
from app.quotes.models import RawQuoteItem
from app.standard_database.models import (
    QuoteDocumentPurpose,
    QuoteDocumentRole,
)
from app.standard_database.service import build_standard_database


def _historical_row(
    session: Session,
    *,
    row: int,
    name: str,
    spec: str,
    unit: str,
    price: str,
    supplier: str,
    maker: str,
) -> None:
    document = SourceDocument(logical_name=f"quotes/vendor-{row}.xlsx")
    variant = SourceVariant(
        document=document,
        path=f"quotes/vendor-{row}.xlsx",
        sha256=f"{row:064x}",
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    raw = RawQuoteItem(
        source_variant=variant,
        source_sheet="견적",
        source_row=row,
        source_cells=f"A{row}:G{row}",
        item_name_raw=name,
        spec_raw=spec,
        unit_raw=unit,
        maker_raw=maker,
        parser_name="xlsx",
        parser_version="1",
    )
    session.add_all(
        [
            document,
            CleanDecision(
                raw_item=raw,
                status=CleanStatus.INCLUDED,
                reason_code="VALID",
                item_name_norm=name,
                spec_norm=spec,
                unit_norm=unit,
                unit_price=Decimal(price),
                maker_norm=maker,
                rule_version="clean-v1",
            ),
            DocumentMetadataVersion(
                source_document=document,
                version_number=1,
                supplier_name=supplier,
                quote_date=date(2026, 7, row),
                project_name="LINE-A",
                decided_by="fixture",
            ),
        ]
    )
    session.flush()
    session.add(
        QuoteDocumentRole(
            document_id=document.id,
            purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
            decided_by="fixture",
            reason_detail="fixture",
        )
    )


def _built_catalog(session: Session) -> int:
    _historical_row(
        session,
        row=1,
        name="BEARING",
        spec="6204 ZZ",
        unit="EA",
        price="100",
        supplier="SUPPLIER A",
        maker="SKF",
    )
    _historical_row(
        session,
        row=2,
        name="BEARING",
        spec="6204 ZZ",
        unit="EA",
        price="120",
        supplier="SUPPLIER B",
        maker="NSK",
    )
    _historical_row(
        session,
        row=3,
        name="SENSOR",
        spec="PX-1",
        unit="EA",
        price="50",
        supplier="SUPPLIER C",
        maker="OMRON",
    )
    session.flush()
    result = build_standard_database(session)
    session.commit()
    return result.run_id


def test_standard_catalog_explorer_exposes_current_price_and_provenance(
    client: TestClient,
    api_session: Session,
) -> None:
    run_id = _built_catalog(api_session)

    response = client.get(
        "/api/catalog/standard-items",
        params={"limit": 20, "search": "6204", "evidence_quality": "MULTI_OBSERVATION"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["latest_build"] == {
        "build_run_id": run_id,
        "status": "SUCCEEDED",
        "built_at": payload["latest_build"]["built_at"],
        "rule_version": "STANDARD_DB_EXACT_V2",
    }
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["current_version"]["canonical_name"] == "BEARING"
    assert item["observation_count"] == 2
    assert item["evidence_quality"] == "MULTI_OBSERVATION"
    assert item["current_price"] == {
        "minimum": "100.000000",
        "median": "110.000000",
        "average": "110.000000",
        "maximum": "120.000000",
    }
    assert item["supplier_summary"] == ["SUPPLIER A", "SUPPLIER B"]
    assert item["maker_summary"] == ["NSK", "SKF"]
    assert item["quote_date_start"] == "2026-07-01"
    assert item["quote_date_end"] == "2026-07-02"
    assert item["provenance"]["build_run_id"] == run_id
    assert "legacy_codes" not in item
    assert "reconciliation_run_id" not in item


def test_standard_catalog_explorer_returns_single_observation_evidence_links(
    client: TestClient,
    api_session: Session,
) -> None:
    run_id = _built_catalog(api_session)
    listing = client.get(
        "/api/catalog/standard-items",
        params={"search": "SENSOR", "evidence_quality": "SINGLE_OBSERVATION"},
    ).json()
    item = listing["items"][0]

    response = client.get(
        f"/api/catalog/standard-items/{item['id']}/evidence",
        params={
            "limit": 20,
            "price_version_id": item["current_price_version_id"],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["evidence_quality"] == "SINGLE_OBSERVATION"
    assert payload["observation_count"] == 1
    assert payload["provenance"]["build_run_id"] == run_id
    assert payload["observations"] == [
        {
            "raw_item_id": payload["observations"][0]["raw_item_id"],
            "unit_price": "50.000000",
            "supplier_name": "SUPPLIER C",
            "maker": "OMRON",
            "quote_date": "2026-07-03",
            "source": {
                "document_id": payload["observations"][0]["source"]["document_id"],
                "logical_name": "quotes/vendor-3.xlsx",
                "variant_id": payload["observations"][0]["source"]["variant_id"],
                "path": "quotes/vendor-3.xlsx",
                "sheet": "견적",
                "page": None,
                "row": 3,
                "cells": "A3:G3",
            },
        }
    ]
    assert payload["next_cursor"] is None


def test_standard_catalog_list_query_count_is_bounded(
    client: TestClient,
    api_session: Session,
) -> None:
    _built_catalog(api_session)
    statements = 0
    engine = api_session.get_bind()

    def count_selects(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        nonlocal statements
        if statement.lstrip().upper().startswith("SELECT"):
            statements += 1

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        response = client.get("/api/catalog/standard-items?limit=100")
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)

    assert response.status_code == 200, response.text
    assert statements <= 5


def test_member_count_includes_current_matched_rows_without_price_observation(
    client: TestClient,
    api_session: Session,
) -> None:
    _built_catalog(api_session)
    bearing = api_session.query(StandardItemVersion).filter_by(
        canonical_name="BEARING"
    ).one()
    document = SourceDocument(logical_name="quotes/no-price.xlsx")
    variant = SourceVariant(
        document=document,
        path="quotes/no-price.xlsx",
        sha256="f" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    raw = RawQuoteItem(
        source_variant=variant,
        item_name_raw="BEARING",
        spec_raw="6204 ZZ",
        unit_raw="EA",
        parser_name="xlsx",
        parser_version="1",
    )
    api_session.add_all(
        [
            CleanDecision(
                raw_item=raw,
                status=CleanStatus.INCLUDED,
                reason_code="PRICE_MISSING",
                item_name_norm="BEARING",
                spec_norm="6204 ZZ",
                unit_norm="EA",
                unit_price=None,
                rule_version="clean-v1",
            ),
            ItemMembershipDecision(
                raw_item=raw,
                standard_item_id=bearing.standard_item_id,
                status=MembershipStatus.MATCHED,
                method="MANUAL",
                evidence_json="{}",
                decided_by="buyer",
            ),
        ]
    )
    api_session.commit()

    response = client.get(
        "/api/catalog/standard-items",
        params={"search": "BEARING"},
    )

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["member_count"] == 3
    assert item["observation_count"] == 2


def test_catalog_hides_stale_standard_when_all_current_members_are_excluded(
    client: TestClient,
    api_session: Session,
) -> None:
    _built_catalog(api_session)
    bearing = api_session.query(StandardItemVersion).filter_by(
        canonical_name="BEARING"
    ).one()
    memberships = (
        api_session.query(ItemMembershipDecision)
        .filter_by(
            standard_item_id=bearing.standard_item_id,
            status=MembershipStatus.MATCHED,
        )
        .all()
    )
    for membership in memberships:
        api_session.add(
            CleanDecision(
                raw_item_id=membership.raw_item_id,
                status=CleanStatus.EXCLUDED,
                reason_code="LATER_EXCLUDED",
                item_name_norm="BEARING",
                spec_norm="6204 ZZ",
                unit_norm="EA",
                rule_version="clean-v2",
            )
        )
    api_session.commit()

    response = client.get(
        "/api/catalog/standard-items",
        params={"search": "BEARING"},
    )

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_catalog_keeps_included_member_without_current_price(
    client: TestClient,
    api_session: Session,
) -> None:
    document = SourceDocument(logical_name="quotes/no-current-price.xlsx")
    variant = SourceVariant(
        document=document,
        path="quotes/no-current-price.xlsx",
        sha256="e" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    raw = RawQuoteItem(
        source_variant=variant,
        item_name_raw="NO PRICE ITEM",
        unit_raw="EA",
        parser_name="xlsx",
        parser_version="1",
    )
    item = StandardItem()
    item.versions.append(
        StandardItemVersion(
            version_number=1,
            canonical_name="NO PRICE ITEM",
            canonical_spec=None,
            canonical_unit="EA",
            aliases_json="[]",
            created_by="buyer",
        )
    )
    api_session.add_all(
        [
            document,
            item,
            CleanDecision(
                raw_item=raw,
                status=CleanStatus.INCLUDED,
                reason_code="PRICE_MISSING",
                item_name_norm="NO PRICE ITEM",
                unit_norm="EA",
                unit_price=None,
                rule_version="clean-v1",
            ),
            ItemMembershipDecision(
                raw_item=raw,
                standard_item=item,
                status=MembershipStatus.MATCHED,
                method="MANUAL",
                evidence_json="{}",
                decided_by="buyer",
            ),
        ]
    )
    api_session.commit()

    response = client.get(
        "/api/catalog/standard-items",
        params={"search": "NO PRICE ITEM"},
    )

    assert response.status_code == 200
    payload = response.json()["items"][0]
    assert payload["member_count"] == 1
    assert payload["observation_count"] == 0
    assert payload["current_price_version_id"] is None
    assert payload["current_price"] is None
    assert payload["evidence_quality"] is None


def test_evidence_pins_requested_price_version_across_new_approval(
    client: TestClient,
    api_session: Session,
) -> None:
    _built_catalog(api_session)
    listing = client.get(
        "/api/catalog/standard-items",
        params={"search": "BEARING"},
    ).json()["items"][0]
    item_id = listing["id"]
    original_version_id = listing["current_price_version_id"]
    first = client.get(
        f"/api/catalog/standard-items/{item_id}/evidence",
        params={"price_version_id": original_version_id, "limit": 1},
    )
    assert first.status_code == 200
    next_cursor = first.json()["next_cursor"]

    draft = client.get(
        f"/api/pricing/standard-items/{item_id}/draft"
    ).json()
    approved = client.post(
        f"/api/pricing/standard-items/{item_id}/versions",
        json={
            "expected_fingerprint": draft["fingerprint"],
            "expected_current_version_id": original_version_id,
            "approved_by": "buyer",
        },
    )
    assert approved.status_code == 201
    assert approved.json()["id"] != original_version_id

    second = client.get(
        f"/api/catalog/standard-items/{item_id}/evidence",
        params={
            "price_version_id": original_version_id,
            "after_id": next_cursor,
            "limit": 1,
        },
    )
    assert second.status_code == 200
    assert second.json()["standard_price_version_id"] == original_version_id
    assert {
        row["raw_item_id"]
        for row in first.json()["observations"] + second.json()["observations"]
    } == {
        membership.raw_item_id
        for membership in api_session.query(ItemMembershipDecision)
        .filter_by(
            standard_item_id=item_id,
            status=MembershipStatus.MATCHED,
        )
        .all()
    }


def test_evidence_rejects_cross_item_price_version(
    client: TestClient,
    api_session: Session,
) -> None:
    _built_catalog(api_session)
    items = client.get("/api/catalog/standard-items").json()["items"]
    bearing = next(
        item
        for item in items
        if item["current_version"]["canonical_name"] == "BEARING"
    )
    sensor = next(
        item
        for item in items
        if item["current_version"]["canonical_name"] == "SENSOR"
    )

    response = client.get(
        f"/api/catalog/standard-items/{bearing['id']}/evidence",
        params={"price_version_id": sensor["current_price_version_id"]},
    )

    assert response.status_code == 404
