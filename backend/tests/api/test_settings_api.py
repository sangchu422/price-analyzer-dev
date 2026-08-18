from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings


def test_get_hchat_settings_reports_disabled_by_default(
    client: TestClient,
) -> None:
    response = client.get("/api/settings/hchat")

    assert response.status_code == 200
    assert response.json() == {"enabled": False, "has_key": False}


def test_get_hchat_settings_reports_enabled_when_configured(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "hchat_embedding_enabled", True)

    response = client.get("/api/settings/hchat")

    assert response.json() == {"enabled": True, "has_key": False}


def test_put_hchat_settings_saves_the_key_and_never_echoes_it(
    client: TestClient,
) -> None:
    response = client.put(
        "/api/settings/hchat", json={"api_key": "sk-test-123"}
    )

    assert response.status_code == 200
    assert response.json() == {"enabled": False, "has_key": True}
    assert "sk-test-123" not in response.text

    follow_up = client.get("/api/settings/hchat")
    assert follow_up.json()["has_key"] is True


def test_put_hchat_settings_with_empty_key_clears_it(
    client: TestClient,
) -> None:
    client.put("/api/settings/hchat", json={"api_key": "sk-test-123"})

    response = client.put("/api/settings/hchat", json={"api_key": ""})

    assert response.json()["has_key"] is False
