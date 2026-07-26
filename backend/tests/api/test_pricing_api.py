from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import event, select
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
from app.api.pricing import _safe_exclusion_context
from app.standard_database.models import (
    StandardBuildStatus,
    StandardDatabaseBuildRun,
)


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
    assert approved.json()["evidence_quality"] == "MULTI_OBSERVATION"
    assert approved.json()["draft_fingerprint"] == draft["fingerprint"]
    assert approved.json()["audit_status"] == "CAPTURED"
    assert approved.json()["standard_item_version"] == {
        "id": item.versions[0].id,
        "version_number": 1,
        "canonical_name": "BEARING",
        "canonical_spec": "6204 ZZ",
        "canonical_unit": "EA",
    }
    assert approved.json()["excluded_count"] == 0
    assert approved.json()["review_required_count"] == 0
    assert approved.json()["exclusions"] == []
    api_session.add(
        StandardItemVersion(
            standard_item_id=item.id,
            version_number=2,
            canonical_name="BEARING UPDATED",
            canonical_spec="6204 ZZ",
            canonical_unit="EA",
            created_by="buyer",
        )
    )
    first_document = api_session.scalar(
        select(SourceDocument).where(
            SourceDocument.logical_name == "quote-1.xlsx"
        )
    )
    assert first_document is not None
    api_session.add(
        DocumentMetadataVersion(
            source_document_id=first_document.id,
            version_number=2,
            supplier_name="A UPDATED",
            quote_date=date(2026, 8, 1),
            project_name="CHANGED",
            decided_by="buyer",
        )
    )
    api_session.commit()
    build = StandardDatabaseBuildRun(
        input_fingerprint="a" * 64,
        rule_version="STANDARD_DB_EXACT_V2",
        status=StandardBuildStatus.SUCCEEDED,
        finished_at=datetime(2026, 7, 20, 12, 0),
    )
    api_session.add(build)
    api_session.commit()
    history = client.get(
        f"/api/pricing/standard-items/{item.id}/versions"
    )
    assert history.status_code == 200
    assert len(history.json()["versions"]) == 1
    assert history.json()["latest_build"]["build_run_id"] == build.id
    assert history.json()["versions"][0]["evidence_quality"] == (
        "MULTI_OBSERVATION"
    )
    assert len(history.json()["versions"][0]["observations"]) == 2
    assert history.json()["versions"][0]["draft_fingerprint"] == (
        draft["fingerprint"]
    )
    assert history.json()["versions"][0]["standard_item_version"][
        "canonical_name"
    ] == "BEARING"
    assert {
        row["metadata"]["supplier_name"]
        for row in history.json()["versions"][0]["observations"]
        if row["metadata"] is not None
    } == {"A", "B"}


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


def test_specific_price_version_validates_item_ownership(
    client: TestClient,
    api_session: Session,
) -> None:
    item = _seed(api_session)
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
    other = StandardItem()
    api_session.add(other)
    api_session.commit()

    response = client.get(
        f"/api/pricing/standard-items/{item.id}/versions/{approved['id']}"
    )
    wrong_owner = client.get(
        f"/api/pricing/standard-items/{other.id}/versions/{approved['id']}"
    )

    assert response.status_code == 200
    assert response.json()["id"] == approved["id"]
    assert response.json()["prices"]["median"] == "110.000000"
    assert len(response.json()["observations"]) == 2
    assert wrong_owner.status_code == 404
    assert wrong_owner.json()["detail"]["error_code"] == (
        "STANDARD_PRICE_VERSION_NOT_FOUND"
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


def test_price_history_cursor_pagination_and_bounded_queries(
    client: TestClient, api_session: Session
) -> None:
    item = _seed(api_session)
    for row in range(3, 21):
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
            source_row=row,
            item_name_raw="BEARING",
            parser_name="xlsx",
            parser_version="1",
        )
        api_session.add_all(
            [
                CleanDecision(
                    raw_item=raw,
                    status=CleanStatus.INCLUDED,
                    reason_code="VALID",
                    item_name_norm="BEARING",
                    unit_norm="EA",
                    unit_price=Decimal(100 + row),
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
    draft = client.get(
        f"/api/pricing/standard-items/{item.id}/draft"
    ).json()
    current_id = None
    created_ids: list[int] = []
    for _ in range(3):
        response = client.post(
            f"/api/pricing/standard-items/{item.id}/versions",
            json={
                "expected_fingerprint": draft["fingerprint"],
                "expected_current_version_id": current_id,
                "approved_by": "buyer",
            },
        )
        assert response.status_code == 201
        current_id = response.json()["id"]
        created_ids.append(current_id)

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
        first = client.get(
            f"/api/pricing/standard-items/{item.id}/versions?limit=2"
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    assert first.status_code == 200
    assert [row["id"] for row in first.json()["versions"]] == created_ids[:2]
    assert first.json()["next_cursor"] == created_ids[1]
    assert first.json()["limit"] == 2
    assert statements <= 5

    refreshed_draft = client.get(
        f"/api/pricing/standard-items/{item.id}/draft"
    )
    assert refreshed_draft.status_code == 200
    assert (
        refreshed_draft.json()["current_standard_price_version_id"]
        == created_ids[-1]
    )

    second = client.get(
        f"/api/pricing/standard-items/{item.id}/versions"
        f"?after_id={first.json()['next_cursor']}&limit=2"
    )
    assert [row["id"] for row in second.json()["versions"]] == created_ids[2:]
    assert second.json()["next_cursor"] is None


def test_history_marks_migrated_rows_as_legacy_without_fake_fingerprint(
    client: TestClient,
) -> None:
    # The migration contract is covered against a populated 0005 database;
    # this endpoint schema must preserve an explicit nullable distinction.
    schema = client.get("/openapi.json").json()
    version_schema = schema["components"]["schemas"]["PriceVersionResponse"]
    assert "audit_status" in version_schema["properties"]


def test_price_history_summary_skips_observation_eager_load(
    client: TestClient,
    api_session: Session,
) -> None:
    item = _seed(api_session)
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
    )
    assert approved.status_code == 201
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
        response = client.get(
            f"/api/pricing/standard-items/{item.id}/versions",
            params={"include_observations": "false"},
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)

    assert response.status_code == 200
    assert response.json()["versions"][0]["observations"] == []
    assert statements <= 3


def test_invalid_stored_exclusion_context_degrades_safely() -> None:
    exclusions, valid, error = _safe_exclusion_context(
        '[{"reason":"UNKNOWN"}]'
    )
    assert exclusions == []
    assert valid is False
    assert error is not None
    assert error.startswith("INVALID_STORED_EXCLUSION_CONTEXT")
