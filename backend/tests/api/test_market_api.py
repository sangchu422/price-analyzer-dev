from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.analysis.models import QuoteAnalysisRun
from app.api.market import _market_worker_count
from app.documents.models import SourceDocument


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

    assert response.status_code == 200
    payload = response.json()
    assert payload["completed"] == 0
    assert payload["unavailable"] == 1
    assert payload["items"] == [
        {
            "raw_item_id": 999_999,
            "status": "NOT_FOUND",
            "detail": "견적 품목을 찾을 수 없습니다.",
            "result": None,
        }
    ]


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
