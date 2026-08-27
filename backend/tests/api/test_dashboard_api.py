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


class _IndicatorResponse:
    def __init__(self, *, text: str = "", payload: object | None = None) -> None:
        self.text = text
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


def test_dashboard_exposes_operational_counts_and_real_indicator_states(
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
    assert payload["monthly_performance"]["available_years"] == [2025, 2026]
    assert len(payload["monthly_performance"]["series_by_year"]["2026"]) == 12
    assert all(
        indicator["source_status"] == "UNAVAILABLE"
        and indicator["points"] == []
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


def test_indicator_sync_uses_public_series_and_persists_cache(
    client: TestClient,
    monkeypatch,
) -> None:
    def fake_get(url: str, *, params: dict[str, str], **_kwargs):
        if "fredgraph.csv" in url:
            series = params["id"]
            return _IndicatorResponse(text=f"DATE,{series}\n2026-07-01,100\n2026-08-01,110\n")
        item_id = params["itmId"]
        value = "160" if item_id.endswith("_7") else "4000000"
        return _IndicatorResponse(payload=[{
            "ITM_ID": item_id,
            "PRD_DE": params["startPrdDe"],
            "DT": value,
        }])

    monkeypatch.setattr("app.procurement.indicators.httpx.get", fake_get)
    response = client.post("/api/dashboard/indicators/sync")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload) == 5
    assert all(item["source_status"] == "LIVE_CACHE" for item in payload)
    wage = next(item for item in payload if item["code"] == "WAGE")
    assert wage["points"][-1]["value"] == "25000.000000"
    exchange = next(item for item in payload if item["code"] == "USD_KRW")
    assert exchange["points"][-1] == {"period": "2026-08", "value": "110.000000"}


def test_monthly_performance_uses_supplied_department_counts(
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

    august = overview["monthly_performance"]["series_by_year"][2026][7]
    assert august == {
        "month": 8,
        "label": "8월",
        "equipment_purchase": 81,
        "integrated_purchase": 474,
        "total": 555,
        "kind": "ACTUAL",
    }
    september = overview["monthly_performance"]["series_by_year"][2026][8]
    assert september["total"] == 351
    assert september["kind"] == "FORECAST"


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


def test_dashboard_and_review_queue_exclude_unapproved_incoming_quotes(
    client: TestClient,
    api_session: Session,
) -> None:
    for index, purpose in enumerate(
        (QuoteDocumentPurpose.HISTORICAL_REFERENCE, QuoteDocumentPurpose.INCOMING_BID),
        start=1,
    ):
        document = SourceDocument(logical_name=f"quote-{index}.xlsx")
        api_session.add(document)
        api_session.flush()
        variant = SourceVariant(
            document=document,
            path=f"quote-{index}.xlsx",
            sha256=str(index) * 64,
            extension=".xlsx",
            security_state="UNLOCKED",
            selected_for_parsing_at_ingest=True,
        )
        raw = RawQuoteItem(
            source_variant=variant,
            source_sheet="견적",
            source_row=1,
            item_name_raw=f"ITEM {index}",
            parser_name="quote-reader",
            parser_version="reader-v2",
        )
        api_session.add_all(
            [
                QuoteDocumentRole(
                    document_id=document.id,
                    purpose=purpose,
                    supersedes_role_id=None,
                    decided_by="buyer",
                    reason_detail="역할 지정",
                ),
                CleanDecision(
                    raw_item=raw,
                    status=CleanStatus.REVIEW_REQUIRED,
                    reason_code="AMOUNT_MISMATCH",
                    item_name_norm=f"ITEM {index}",
                    rule_version="clean-v2",
                ),
            ]
        )
    api_session.commit()

    dashboard = client.get("/api/dashboard/overview").json()
    queue = client.get("/api/cleansing/review-queue", params={"limit": 10}).json()

    assert dashboard["catalog"]["historical_quote_document_count"] == 1
    assert dashboard["cleansing_todo"]["count"] == 1
    assert queue["remaining"] == 1
