def test_legacy_reconciliation_endpoint_is_not_available(client) -> None:
    response = client.get("/api/reconciliation/legacy-standard-db/runs/1")

    assert response.status_code == 404
