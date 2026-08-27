from __future__ import annotations

from datetime import date, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.catalog.models import StandardItem, StandardItemVersion
from app.cleansing.models import CleanDecision, CleanStatus
from app.documents.models import SourceDocument, SourceVariant
from app.procurement.dashboard import dashboard_overview
from app.procurement.models import ItemCategory, StandardItemCategoryAssignment
from app.quotes.models import RawQuoteItem
from app.standard_database.models import QuoteDocumentPurpose, QuoteDocumentRole


def test_dashboard_exposes_operational_counts_and_labels_demo_indicators(
    client: TestClient,
    api_session: Session,
) -> None:
    category = ItemCategory(
        code="GENERAL_COMPONENT",
        name="공통 설비·부품",
        description="전문군이 불명확한 산업 설비·부품의 탐색 분류",
        sort_order=900,
    )
    item = StandardItem()
    version = StandardItemVersion(
        standard_item=item,
        version_number=1,
        canonical_name="SPECIAL ASSEMBLY",
        canonical_spec="ZX-991",
        canonical_unit="SET",
        aliases_json="[]",
        created_by="buyer",
        change_reason="seed",
    )
    api_session.add_all([category, item, version])
    api_session.flush()
    api_session.add(
        StandardItemCategoryAssignment(
            standard_item_id=item.id,
            category_id=category.id,
            confidence="25",
            method="category-keyword-v2",
            evidence_json='{"fallback":true}',
            supersedes_assignment_id=None,
            assigned_by="buyer",
        )
    )
    api_session.commit()

    response = client.get("/api/dashboard/overview")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["catalog"]["total_standard_items"] == 1
    assert payload["catalog"]["categorized_items"] == 0
    assert payload["catalog"]["family_classified_items"] == 0
    assert payload["categories"] == []
    assert payload["families"] == []
    assert len(payload["monthly_performance"]["series"]) == 12
    assert all(
        indicator["source_status"] == "DEMO"
        and "공식 데이터 연동 전" in indicator["source_label"]
        for indicator in payload["indicators"]
    )

    trend = client.get(f"/api/dashboard/standard-items/{item.id}/price-trend")
    assert trend.status_code == 200
    assert trend.json()["points"] == []
    assert trend.json()["undated_observation_count"] == 0


def test_dashboard_price_trend_returns_404_for_unknown_item(
    client: TestClient,
) -> None:
    response = client.get("/api/dashboard/standard-items/999/price-trend")
    assert response.status_code == 404


def test_monthly_performance_keeps_an_activated_incoming_quote(
    api_session: Session,
) -> None:
    document = SourceDocument(
        logical_name="received-then-activated.xlsx",
        created_at=datetime(2026, 8, 12, 9, 0, 0),
    )
    api_session.add(document)
    api_session.flush()
    incoming = QuoteDocumentRole(
        document_id=document.id,
        purpose=QuoteDocumentPurpose.INCOMING_BID,
        supersedes_role_id=None,
        decided_by="buyer",
        reason_detail="신규 견적 접수",
    )
    api_session.add(incoming)
    api_session.flush()
    api_session.add(
        QuoteDocumentRole(
            document_id=document.id,
            purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
            supersedes_role_id=incoming.id,
            decided_by="buyer",
            reason_detail="표준 DB 승인 반영",
        )
    )
    api_session.commit()

    overview = dashboard_overview(api_session, today=date(2026, 8, 26))

    august = overview["monthly_performance"]["series"][7]
    assert august == {
        "month": 8,
        "label": "8월",
        "count": 1,
        "kind": "ACTUAL",
    }


def test_dashboard_todo_matches_document_grouped_review_queue(
    client: TestClient,
    api_session: Session,
) -> None:
    document = SourceDocument(logical_name="문서 단위 검토.xlsx")
    variant = SourceVariant(
        document=document,
        path="문서 단위 검토.xlsx",
        sha256="e" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    for row_number in (10, 11):
        raw = RawQuoteItem(
            source_variant=variant,
            source_sheet="견적",
            source_row=row_number,
            item_name_raw=f"PARSER ROW {row_number}",
            parser_name="legacy-reader",
            parser_version="reader-v1",
        )
        api_session.add(
            CleanDecision(
                raw_item=raw,
                status=CleanStatus.REVIEW_REQUIRED,
                reason_code="PARSER_SOURCE_REVIEW_REQUIRED",
                item_name_norm=f"PARSER ROW {row_number}",
                rule_version="clean-v2",
            )
        )
    row_review = RawQuoteItem(
        source_variant=variant,
        source_sheet="견적",
        source_row=12,
        item_name_raw="AMOUNT ROW",
        parser_name="quote-reader",
        parser_version="reader-v2",
    )
    api_session.add(
        CleanDecision(
            raw_item=row_review,
            status=CleanStatus.REVIEW_REQUIRED,
            reason_code="AMOUNT_MISMATCH",
            item_name_norm="AMOUNT ROW",
            rule_version="clean-v2",
        )
    )
    api_session.commit()

    dashboard = client.get("/api/dashboard/overview")
    review_queue = client.get("/api/cleansing/review-queue", params={"limit": 10})

    assert dashboard.status_code == 200
    assert review_queue.status_code == 200
    assert dashboard.json()["cleansing_todo"]["count"] == 2
    assert dashboard.json()["catalog"]["cleansing_todo_items"] == 2
    assert review_queue.json()["remaining"] == 2
    assert dashboard.json()["cleansing_todo"]["top_reasons"] == [
        {"reason_code": "PARSER_SOURCE_REVIEW_REQUIRED", "count": 1},
        {"reason_code": "AMOUNT_MISMATCH", "count": 1},
    ]
