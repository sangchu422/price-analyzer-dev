from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.analysis.models import QuoteAnalysisRun
from app.api.market import _market_worker_count
from app.cleansing.models import CleanDecision, CleanStatus
from app.documents.models import SourceDocument, SourceVariant
from app.quotes.models import RawQuoteItem


def _analysis_run(session: Session) -> QuoteAnalysisRun:
    document = SourceDocument(logical_name="incoming.xlsx")
    session.add(document)
    session.flush()
    run = QuoteAnalysisRun(
        document_id=document.id,
        created_by="tester",
        review_percent=Decimal("10"),
        high_percent=Decimal("20"),
        total_line_count=0,
        target_available_count=0,
        target_unavailable_count=0,
    )
    session.add(run)
    session.commit()
    return run


def test_market_batch_serializes_local_sqlite_writes() -> None:
    sqlite_bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    server_bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    assert _market_worker_count(sqlite_bind, 4) == 1
    assert _market_worker_count(server_bind, 6) == 4
    assert _market_worker_count(sqlite_bind, 0) == 0


def test_automatic_market_batch_explains_missing_rows(
    client: TestClient, api_session: Session
) -> None:
    run = _analysis_run(api_session)

    response = client.post(
        "/api/market/lookup-batch",
        json={"analysis_run_id": run.id, "raw_item_ids": [999_999]},
    )

    assert response.status_code == 422
    assert "해당 분석 실행" in response.json()["detail"]


def test_automatic_market_batch_404s_when_analysis_run_missing(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/market/lookup-batch",
        json={"analysis_run_id": 999_999, "raw_item_ids": [1]},
    )

    assert response.status_code == 404


def test_lookup_market_price_404s_when_analysis_run_missing(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/market/lookup/1",
        params={"analysis_run_id": 999_999},
    )

    assert response.status_code == 404


def test_lookup_market_price_503s_on_unexpected_storage_error(
    client: TestClient, api_session: Session, monkeypatch
) -> None:
    run = _analysis_run(api_session)
    variant = SourceVariant(
        document_id=run.document_id,
        path="incoming.xlsx",
        sha256="a" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    raw = RawQuoteItem(
        source_variant=variant,
        source_sheet="Sheet1",
        source_row=1,
        item_name_raw="MODEL PART",
        spec_raw="ABC-1234",
        unit_raw="EA",
        quantity_raw="1",
        unit_price_raw="100",
        amount_raw="100",
        parser_name="fixture",
        parser_version="1",
    )
    api_session.add(raw)
    api_session.flush()
    api_session.add(
        CleanDecision(
            raw_item_id=raw.id,
            status=CleanStatus.INCLUDED,
            reason_code="VALID",
            reason_detail=None,
            item_name_norm="MODEL PART",
            spec_norm="ABC-1234",
            unit_norm="EA",
            maker_norm=None,
            quantity=Decimal("1"),
            unit_price=Decimal("100"),
            amount=Decimal("100"),
            rule_version="clean-v2",
        )
    )
    api_session.commit()

    def _raise_locked(self, *args, **kwargs):
        raise OperationalError("statement", {}, Exception("database is locked"))

    monkeypatch.setattr(
        "app.market.service.MarketLookupService.lookup_raw_item", _raise_locked
    )

    response = client.post(
        f"/api/market/lookup/{raw.id}",
        params={"analysis_run_id": run.id},
    )

    assert response.status_code == 503
    assert "다시 조회" in response.json()["detail"]


def test_automatic_market_batch_requires_a_product_identifier(
    client: TestClient, api_session: Session
) -> None:
    run = _analysis_run(api_session)
    variant = SourceVariant(
        document_id=run.document_id,
        path="incoming.xlsx",
        sha256="b" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    raw = RawQuoteItem(
        source_variant=variant,
        source_sheet="Sheet1",
        source_row=2,
        item_name_raw="SPECIAL JIG",
        spec_raw="CUSTOM",
        unit_raw="EA",
        quantity_raw="1",
        unit_price_raw="100",
        amount_raw="100",
        parser_name="fixture",
        parser_version="1",
    )
    api_session.add(raw)
    api_session.flush()
    api_session.add(
        CleanDecision(
            raw_item_id=raw.id,
            status=CleanStatus.INCLUDED,
            reason_code="VALID",
            reason_detail=None,
            item_name_norm="SPECIAL JIG",
            spec_norm="CUSTOM",
            unit_norm="EA",
            maker_norm=None,
            quantity=Decimal("1"),
            unit_price=Decimal("100"),
            amount=Decimal("100"),
            rule_version="clean-v2",
        )
    )
    api_session.commit()

    response = client.post(
        "/api/market/lookup-batch",
        json={"analysis_run_id": run.id, "raw_item_ids": [raw.id]},
    )

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "raw_item_id": raw.id,
            "status": "IDENTIFIER_REQUIRED",
            "detail": "정확한 제품 모델명이 없어 자동 시장가 조회에서 제외했습니다.",
            "result": None,
        }
    ]
    assert response.json()["completed"] == 0
    assert response.json()["unavailable"] == 1
