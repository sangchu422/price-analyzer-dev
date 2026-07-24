from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app


def test_paths_are_resolved_from_repository_root(tmp_path):
    settings = Settings(project_root=tmp_path, quote_folder="견적서")
    assert settings.quote_path == tmp_path / "견적서"
    assert settings.database_path == tmp_path / "data" / "price_analyzer.sqlite3"


def test_health_endpoint():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
