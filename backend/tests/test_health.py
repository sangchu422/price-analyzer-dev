from fastapi.testclient import TestClient

from app.core import config
from app.core.config import Settings
from app.main import app


def test_paths_are_resolved_from_repository_root(tmp_path):
    settings = Settings(project_root=tmp_path, quote_folder="견적서")
    assert settings.quote_path == tmp_path / "견적서"
    assert settings.database_path == (
        tmp_path
        / "backend"
        / ".local"
        / "standard-item-migration-v2.sqlite3"
    )


def test_paths_are_loaded_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("QUOTE_FOLDER", raising=False)
    monkeypatch.delenv("DATABASE_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "QUOTE_FOLDER=불러온견적서\nDATABASE_FILE=storage/prices.sqlite3\n",
        encoding="utf-8",
    )

    settings = Settings(project_root=tmp_path)

    assert settings.quote_path == tmp_path / "불러온견적서"
    assert settings.database_path == tmp_path / "storage" / "prices.sqlite3"


def test_shared_settings_is_public():
    assert isinstance(config.settings, Settings)


def test_health_endpoint():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
