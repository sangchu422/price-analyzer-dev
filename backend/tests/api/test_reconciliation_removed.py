from app.main import app


def test_legacy_reconciliation_endpoint_is_not_available(client) -> None:
    response = client.get("/api/reconciliation/legacy-standard-db/runs/1")

    assert response.status_code == 404
    route_paths = [
        route.path
        for route in app.routes
        if isinstance(getattr(route, "path", None), str)
    ]
    route_paths.extend(
        f"{route.include_context.prefix}{child.path}"
        for route in app.routes
        if hasattr(route, "original_router")
        for child in route.original_router.routes
    )
    assert not any(path.startswith("/api/reconciliation") for path in route_paths)
    assert not any(
        path.startswith("/api/reconciliation")
        for path in app.openapi()["paths"]
    )
