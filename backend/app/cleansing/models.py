from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.quotes.models import RawQuoteItem


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CleanStatus(StrEnum):
    INCLUDED = "INCLUDED"
    EXCLUDED = "EXCLUDED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class CleanDecision(Base):
    __tablename__ = "clean_decision"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_quote_item.id"),
        nullable=False,
        index=True,
    )
    status: Mapped[CleanStatus] = mapped_column(
        Enum(CleanStatus, native_enum=False),
    )
    reason_code: Mapped[str] = mapped_column(String(100))
    reason_detail: Mapped[str | None] = mapped_column(Text)
    item_name_norm: Mapped[str | None] = mapped_column(Text)
    spec_norm: Mapped[str | None] = mapped_column(Text)
    unit_norm: Mapped[str | None] = mapped_column(String(100))
    maker_norm: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    rule_version: Mapped[str] = mapped_column(String(100))
    decided_by: Mapped[str] = mapped_column(String(100), default="SYSTEM")
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
    )

    raw_item: Mapped[RawQuoteItem] = relationship(
        "RawQuoteItem",
        back_populates="decisions",
    )
