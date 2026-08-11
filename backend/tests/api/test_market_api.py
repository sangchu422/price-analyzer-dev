from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.market import _market_worker_count


def test_market_batch_serializes_local_sqlite_writes() -> None:
    sqlite_bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    server_bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    assert _market_worker_count(sqlite_bind, 4) == 1
    assert _market_worker_count(server_bind, 6) == 4
    assert _market_worker_count(sqlite_bind, 0) == 0


def test_automatic_market_batch_explains_missing_rows(client: TestClient) -> None:
    response = client.post(
        "/api/market/lookup-batch",
        json={"raw_item_ids": [999_999]},
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
