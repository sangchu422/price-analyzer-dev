from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings as default_settings
from app.db.session import get_session
from app.settings.service import HCHAT_API_KEY_SETTING, get_setting, set_setting

router = APIRouter()


class HchatSettingsResponse(BaseModel):
    enabled: bool
    has_key: bool


class HchatSettingsUpdateRequest(BaseModel):
    api_key: str


def _response(session: Session) -> HchatSettingsResponse:
    return HchatSettingsResponse(
        enabled=default_settings.hchat_embedding_enabled,
        has_key=bool(get_setting(session, HCHAT_API_KEY_SETTING)),
    )


@router.get("/hchat", response_model=HchatSettingsResponse)
def get_hchat_settings(
    session: Session = Depends(get_session),
) -> HchatSettingsResponse:
    return _response(session)


@router.put("/hchat", response_model=HchatSettingsResponse)
def update_hchat_settings(
    payload: HchatSettingsUpdateRequest,
    session: Session = Depends(get_session),
) -> HchatSettingsResponse:
    set_setting(session, HCHAT_API_KEY_SETTING, payload.api_key.strip())
    session.commit()
    return _response(session)
