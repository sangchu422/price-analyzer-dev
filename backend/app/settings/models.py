from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.time import utc_now
from app.db.types import NaiveUTCDateTime


class AppSetting(Base):
    """A locally-stored, user-editable runtime value (e.g. a personal API key).

    Never checked into git: this table lives only in each installation's
    local SQLite file.
    """

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text())
    updated_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        onupdate=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
