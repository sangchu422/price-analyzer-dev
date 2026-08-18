from __future__ import annotations

from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.settings.models import AppSetting

HCHAT_API_KEY_SETTING = "hchat_api_key"


def get_setting(session: Session, key: str) -> str | None:
    setting = session.get(AppSetting, key)
    return setting.value if setting is not None else None


def set_setting(session: Session, key: str, value: str | None) -> None:
    setting = session.get(AppSetting, key)
    if not value:
        if setting is not None:
            session.delete(setting)
        return
    if setting is None:
        session.add(AppSetting(key=key, value=value))
    else:
        setting.value = value


def resolve_settings_with_stored_hchat_key(
    session: Session, settings: Settings
) -> Settings:
    """Prefer a per-installation hChat key stored locally over the env value."""

    stored_key = get_setting(session, HCHAT_API_KEY_SETTING)
    if not stored_key:
        return settings
    return settings.model_copy(
        update={"hchat_embedding_api_key": SecretStr(stored_key)}
    )
