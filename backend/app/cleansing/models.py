from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Enum, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.time import utc_now
from app.db.types import ExactDecimal, NaiveUTCDateTime

if TYPE_CHECKING:
    from app.quotes.models import RawQuoteItem


def _raw_quote_item_model() -> type[RawQuoteItem]:
    from app.quotes.models import RawQuoteItem

    return RawQuoteItem


class CleanStatus(StrEnum):
    INCLUDED = "INCLUDED"
    EXCLUDED = "EXCLUDED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class CleanDecision(Base):
    __tablename__ = "clean_decision"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "raw_item_id",
            "status",
            name="uq_clean_decision_evidence_key",
        ),
        {"info": {"evidence_immutable": True}},
    )
    __evidence_immutable__: ClassVar[bool] = True

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_quote_item.id"),
        nullable=False,
        index=True,
    )
    status: Mapped[CleanStatus] = mapped_column(
        Enum(
            CleanStatus,
            name="clean_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
    )
    reason_code: Mapped[str] = mapped_column(String(100))
    reason_detail: Mapped[str | None] = mapped_column(Text)
    item_name_norm: Mapped[str | None] = mapped_column(Text)
    spec_norm: Mapped[str | None] = mapped_column(Text)
    unit_norm: Mapped[str | None] = mapped_column(String(100))
    maker_norm: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    unit_price: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    amount: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    rule_version: Mapped[str] = mapped_column(String(100))
    decided_by: Mapped[str] = mapped_column(
        String(100),
        default="SYSTEM",
        server_default=text("'SYSTEM'"),
    )
    decided_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    raw_item: Mapped[RawQuoteItem] = relationship(
        _raw_quote_item_model,
        back_populates="decisions",
    )
