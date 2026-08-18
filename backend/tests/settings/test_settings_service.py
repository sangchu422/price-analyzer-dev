from __future__ import annotations

from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import Base
from app.db.sqlite import configure_sqlite
from app.settings.service import (
    HCHAT_API_KEY_SETTING,
    get_setting,
    resolve_settings_with_stored_hchat_key,
    set_setting,
)


def _session() -> Session:
    engine = configure_sqlite(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(engine)
    return Session(engine)


def test_get_setting_is_none_when_never_set() -> None:
    with _session() as session:
        assert get_setting(session, "missing") is None


def test_set_and_get_setting_round_trips() -> None:
    with _session() as session:
        set_setting(session, HCHAT_API_KEY_SETTING, "sk-test-123")
        session.commit()

        assert get_setting(session, HCHAT_API_KEY_SETTING) == "sk-test-123"


def test_set_setting_overwrites_the_previous_value() -> None:
    with _session() as session:
        set_setting(session, HCHAT_API_KEY_SETTING, "sk-old")
        set_setting(session, HCHAT_API_KEY_SETTING, "sk-new")
        session.commit()

        assert get_setting(session, HCHAT_API_KEY_SETTING) == "sk-new"


def test_set_setting_with_empty_value_clears_it() -> None:
    with _session() as session:
        set_setting(session, HCHAT_API_KEY_SETTING, "sk-old")
        set_setting(session, HCHAT_API_KEY_SETTING, "")
        session.commit()

        assert get_setting(session, HCHAT_API_KEY_SETTING) is None


def test_resolve_settings_leaves_env_key_untouched_when_nothing_stored() -> None:
    with _session() as session:
        settings = Settings(hchat_embedding_api_key=SecretStr("env-key"))

        resolved = resolve_settings_with_stored_hchat_key(session, settings)

        assert resolved.hchat_embedding_api_key.get_secret_value() == "env-key"


def test_resolve_settings_prefers_the_stored_key_over_env() -> None:
    with _session() as session:
        set_setting(session, HCHAT_API_KEY_SETTING, "stored-key")
        session.commit()
        settings = Settings(hchat_embedding_api_key=SecretStr("env-key"))

        resolved = resolve_settings_with_stored_hchat_key(session, settings)

        assert resolved.hchat_embedding_api_key.get_secret_value() == "stored-key"
