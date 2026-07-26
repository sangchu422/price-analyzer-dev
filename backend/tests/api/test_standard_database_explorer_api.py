from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.catalog.models import DocumentMetadataVersion
from app.catalog.models import (
    ItemMembershipDecision,
    MembershipStatus,
    StandardItemVersion,
    StandardItem,
    StandardPriceVersion,
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


def _seed_member_only_catalog(session: Session, count: int) -> None:
    document = SourceDocument(logical_name="quotes/member-only.xlsx")
    variant = SourceVariant(
        document=document,
        path="quotes/member-only.xlsx",
        sha256="a" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    session.add(document)
    session.flush()
    session.add(
        QuoteDocumentRole(
            document_id=document.id,
            purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
            decided_by="fixture",
            reason_detail="large catalog fixture",
        )
    )
    for index in range(count):
        name = f"ITEM-{index:04d}"
        item = StandardItem()
        raw = RawQuoteItem(
            source_variant=variant,
            source_sheet="Sheet1",
            source_row=index + 1,
            item_name_raw=name,
            spec_raw="SPEC",
            unit_raw="EA",
            parser_name="xlsx",
            parser_version="1",
        )
        session.add_all(
            [
                item,
                StandardItemVersion(
                    standard_item=item,
                    version_number=1,
                    canonical_name=name,
                    canonical_spec="SPEC",
                    canonical_unit="EA",
                    aliases_json="[]",
                    created_by="fixture",
                ),
                CleanDecision(
                    raw_item=raw,
                    status=CleanStatus.INCLUDED,
                    reason_code="PRICE_MISSING",
                    item_name_norm=name,
                    spec_norm="SPEC",
                    unit_norm="EA",
                    unit_price=None,
                    rule_version="clean-v1",
                ),
                ItemMembershipDecision(
                    raw_item=raw,
                    standard_item=item,
                    status=MembershipStatus.MATCHED,
                    method="FIXTURE",
                    evidence_json="{}",
                    decided_by="fixture",
                ),
            ]
        )
    session.commit()


def _seed_priced_unique_rows(
    session: Session,
    count: int,
) -> list[RawQuoteItem]:
    document = SourceDocument(logical_name="quotes/unique-priced.xlsx")
    variant = SourceVariant(
        document=document,
        path="quotes/unique-priced.xlsx",
        sha256="b" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    session.add(document)
    session.flush()
    session.add(
        QuoteDocumentRole(
            document_id=document.id,
            purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
            decided_by="fixture",
            reason_detail="filtered pagination fixture",
        )
    )
    rows: list[RawQuoteItem] = []
    for index in range(count):
        name = f"FILTER-{index:04d}"
        raw = RawQuoteItem(
            source_variant=variant,
            source_sheet="Sheet1",
            source_row=index + 1,
            item_name_raw=name,
            spec_raw="SPEC",
            unit_raw="EA",
            parser_name="xlsx",
            parser_version="1",
        )
        session.add(
            CleanDecision(
                raw_item=raw,
                status=CleanStatus.INCLUDED,
                reason_code="VALID",
                item_name_norm=name,
                spec_norm="SPEC",
                unit_norm="EA",
                unit_price=Decimal("100"),
                rule_version="clean-v1",
            )
        )
        rows.append(raw)
    session.flush()
    build_standard_database(session)
    session.commit()
    return rows


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
    # The projection uses a fixed set of batched reads for current evidence,
    # price observations, summaries, and build provenance. The bound must not
    # grow with the number of catalog items.
    assert statements <= 8


def test_standard_catalog_materializes_only_a_fixed_page_chunk(
    client: TestClient,
    api_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_member_only_catalog(api_session, 2_000)
    api_session.expunge_all()
    loaded_version_ids: list[int] = []
    price_batch_sizes: list[int] = []

    def record_load(
        target: StandardItemVersion,
        context: object,
    ) -> None:
        loaded_version_ids.append(target.id)

    def no_prices(
        session: Session,
        standard_item_ids: object,
    ) -> dict[int, StandardPriceVersion]:
        item_ids = list(standard_item_ids)
        price_batch_sizes.append(len(item_ids))
        return {}

    monkeypatch.setattr(
        "app.standard_database.read_service.operational_standard_prices",
        no_prices,
    )
    event.listen(StandardItemVersion, "load", record_load)
    try:
        first = client.get("/api/catalog/standard-items?limit=100")
    finally:
        event.remove(StandardItemVersion, "load", record_load)

    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert len(first_payload["items"]) == 100
    assert first_payload["next_cursor"] is not None
    assert len(loaded_version_ids) <= 128
    assert price_batch_sizes and max(price_batch_sizes) <= 128


def test_single_quality_pagination_skips_stale_chunks_without_gaps(
    client: TestClient,
    api_session: Session,
) -> None:
    rows = _seed_priced_unique_rows(api_session, 143)
    for index, raw in enumerate(rows[:140]):
        api_session.add(
            CleanDecision(
                raw_item_id=raw.id,
                status=CleanStatus.INCLUDED,
                reason_code="CORRECTED_PRICE",
                item_name_norm=f"FILTER-{index:04d}",
                spec_norm="SPEC",
                unit_norm="EA",
                unit_price=Decimal("101"),
                rule_version="clean-v2",
            )
        )
    api_session.commit()

    first = client.get(
        "/api/catalog/standard-items",
        params={"limit": 2, "evidence_quality": "SINGLE_OBSERVATION"},
    )
    assert first.status_code == 200, first.text
    first_payload = first.json()
    first_names = [
        row["current_version"]["canonical_name"]
        for row in first_payload["items"]
    ]
    assert first_names == ["FILTER-0140", "FILTER-0141"]
    assert first_payload["next_cursor"] is not None

    second = client.get(
        "/api/catalog/standard-items",
        params={
            "limit": 2,
            "evidence_quality": "SINGLE_OBSERVATION",
            "after_id": first_payload["next_cursor"],
        },
    )
    assert second.status_code == 200, second.text
    second_payload = second.json()
    second_names = [
        row["current_version"]["canonical_name"]
        for row in second_payload["items"]
    ]
    assert second_names == ["FILTER-0142"]
    assert second_payload["next_cursor"] is None
    assert not set(first_names) & set(second_names)


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
    api_session.add(document)
    api_session.flush()
    api_session.add_all(
        [
            QuoteDocumentRole(
                document_id=document.id,
                purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
                decided_by="fixture",
                reason_detail="fixture",
            ),
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
    assert item["observation_count"] == 0
    assert item["current_price_version_id"] is None

    result = build_standard_database(api_session)
    api_session.commit()
    assert result.created_price_versions == 1

    rebuilt = client.get(
        "/api/catalog/standard-items",
        params={"search": "BEARING"},
    ).json()["items"][0]
    assert rebuilt["member_count"] == 3
    assert rebuilt["observation_count"] == 2
    assert rebuilt["current_price"]["median"] == "110.000000"


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
    api_session.add_all([document, item])
    api_session.flush()
    api_session.add_all(
        [
            QuoteDocumentRole(
                document_id=document.id,
                purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
                decided_by="fixture",
                reason_detail="fixture",
            ),
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


@pytest.mark.parametrize(
    "replacement_status",
    [CleanStatus.EXCLUDED, CleanStatus.REVIEW_REQUIRED],
)
def test_single_evidence_standard_is_hidden_when_latest_clean_is_not_included(
    client: TestClient,
    api_session: Session,
    replacement_status: CleanStatus,
) -> None:
    _built_catalog(api_session)
    sensor = api_session.query(StandardItemVersion).filter_by(
        canonical_name="SENSOR"
    ).one()
    membership = (
        api_session.query(ItemMembershipDecision)
        .filter_by(
            standard_item_id=sensor.standard_item_id,
            status=MembershipStatus.MATCHED,
        )
        .one()
    )
    api_session.add(
        CleanDecision(
            raw_item_id=membership.raw_item_id,
            status=replacement_status,
            reason_code="LIFECYCLE_CHANGE",
            item_name_norm="SENSOR",
            spec_norm="PX-1",
            unit_norm="EA",
            rule_version="clean-v2",
        )
    )
    api_session.commit()

    response = client.get(
        "/api/catalog/standard-items",
        params={"search": "SENSOR"},
    )

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert (
        api_session.query(StandardPriceVersion)
        .filter_by(standard_item_id=sensor.standard_item_id)
        .count()
        == 1
    )


def test_standard_is_hidden_when_latest_document_role_becomes_incoming(
    client: TestClient,
    api_session: Session,
) -> None:
    _built_catalog(api_session)
    sensor = api_session.query(StandardItemVersion).filter_by(
        canonical_name="SENSOR"
    ).one()
    membership = (
        api_session.query(ItemMembershipDecision)
        .filter_by(standard_item_id=sensor.standard_item_id)
        .one()
    )
    raw = api_session.get(RawQuoteItem, membership.raw_item_id)
    prior_role = (
        api_session.query(QuoteDocumentRole)
        .join(
            SourceVariant,
            SourceVariant.document_id == QuoteDocumentRole.document_id,
        )
        .filter(SourceVariant.id == raw.source_variant_id)
        .order_by(QuoteDocumentRole.id.desc())
        .first()
    )
    api_session.add(
        QuoteDocumentRole(
            document_id=prior_role.document_id,
            purpose=QuoteDocumentPurpose.INCOMING_BID,
            supersedes_role_id=prior_role.id,
            decided_by="fixture",
            reason_detail="lifecycle change",
        )
    )
    api_session.commit()

    response = client.get(
        "/api/catalog/standard-items",
        params={"search": "SENSOR"},
    )

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_price_is_inactive_until_rebuild_matches_remaining_evidence(
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
        .order_by(ItemMembershipDecision.raw_item_id)
        .all()
    )
    old_price = (
        api_session.query(StandardPriceVersion)
        .filter_by(standard_item_id=bearing.standard_item_id)
        .one()
    )
    api_session.add(
        CleanDecision(
            raw_item_id=memberships[0].raw_item_id,
            status=CleanStatus.EXCLUDED,
            reason_code="LIFECYCLE_CHANGE",
            item_name_norm="BEARING",
            spec_norm="6204 ZZ",
            unit_norm="EA",
            rule_version="clean-v2",
        )
    )
    api_session.commit()

    stale = client.get(
        "/api/catalog/standard-items",
        params={"search": "BEARING"},
    ).json()["items"][0]

    assert stale["member_count"] == 1
    assert stale["current_price_version_id"] is None
    assert stale["current_price"] is None
    assert (
        api_session.query(StandardPriceVersion)
        .filter_by(standard_item_id=bearing.standard_item_id)
        .count()
        == 1
    )

    result = build_standard_database(api_session)
    api_session.commit()
    assert result.created_price_versions == 1

    rebuilt = client.get(
        "/api/catalog/standard-items",
        params={"search": "BEARING"},
    ).json()["items"][0]
    assert rebuilt["member_count"] == 1
    assert rebuilt["observation_count"] == 1
    assert rebuilt["current_price_version_id"] != old_price.id
    versions = (
        api_session.query(StandardPriceVersion)
        .filter_by(standard_item_id=bearing.standard_item_id)
        .order_by(StandardPriceVersion.id)
        .all()
    )
    assert [version.id for version in versions] == [
        old_price.id,
        rebuilt["current_price_version_id"],
    ]


def test_clean_value_change_hides_explorer_price_until_rebuild(
    client: TestClient,
    api_session: Session,
) -> None:
    _built_catalog(api_session)
    bearing = api_session.query(StandardItemVersion).filter_by(
        canonical_name="BEARING"
    ).one()
    membership = (
        api_session.query(ItemMembershipDecision)
        .filter_by(
            standard_item_id=bearing.standard_item_id,
            status=MembershipStatus.MATCHED,
        )
        .order_by(ItemMembershipDecision.raw_item_id)
        .first()
    )
    old_price = (
        api_session.query(StandardPriceVersion)
        .filter_by(standard_item_id=bearing.standard_item_id)
        .one()
    )
    api_session.add(
        CleanDecision(
            raw_item_id=membership.raw_item_id,
            status=CleanStatus.INCLUDED,
            reason_code="CORRECTED_PRICE",
            item_name_norm="BEARING",
            spec_norm="6204 ZZ",
            unit_norm="EA",
            unit_price=Decimal("200"),
            maker_norm="SKF",
            rule_version="clean-v2",
        )
    )
    api_session.commit()

    stale = client.get(
        "/api/catalog/standard-items",
        params={"search": "BEARING"},
    ).json()["items"][0]
    assert stale["member_count"] == 2
    assert stale["current_price_version_id"] is None
    assert stale["current_price"] is None

    result = build_standard_database(api_session)
    api_session.commit()
    assert result.created_price_versions == 1

    rebuilt = client.get(
        "/api/catalog/standard-items",
        params={"search": "BEARING"},
    ).json()["items"][0]
    assert rebuilt["current_price_version_id"] != old_price.id
    assert rebuilt["current_price"]["median"] == "160.000000"
    assert (
        api_session.query(StandardPriceVersion)
        .filter_by(standard_item_id=bearing.standard_item_id)
        .count()
        == 2
    )


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
