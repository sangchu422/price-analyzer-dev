from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.cleansing.models import CleanDecision
    from app.documents.models import SourceDocument


class RawQuoteItem(Base):
    """Append-only parser output; cleansing writes separate decisions."""

    __tablename__ = "raw_quote_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_document.id"),
        nullable=False,
        index=True,
    )
    source_sheet: Mapped[str | None] = mapped_column(String(255))
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_row: Mapped[int | None] = mapped_column(Integer)
    source_cells: Mapped[str | None] = mapped_column(Text)
    item_name_raw: Mapped[str | None] = mapped_column(Text)
    spec_raw: Mapped[str | None] = mapped_column(Text)
    unit_raw: Mapped[str | None] = mapped_column(Text)
    quantity_raw: Mapped[str | None] = mapped_column(Text)
    unit_price_raw: Mapped[str | None] = mapped_column(Text)
    amount_raw: Mapped[str | None] = mapped_column(Text)
    maker_raw: Mapped[str | None] = mapped_column(Text)
    parser_name: Mapped[str | None] = mapped_column(String(100))
    parser_version: Mapped[str | None] = mapped_column(String(100))
    parse_warnings_json: Mapped[str] = mapped_column(Text, default="[]")

    document: Mapped[SourceDocument] = relationship(
        "SourceDocument",
        back_populates="raw_items",
    )
    decisions: Mapped[list[CleanDecision]] = relationship(
        "CleanDecision",
        back_populates="raw_item",
    )
