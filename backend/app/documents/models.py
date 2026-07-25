from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.time import utc_now
from app.db.types import NaiveUTCDateTime

if TYPE_CHECKING:
    from app.quotes.models import RawQuoteItem


def _raw_quote_item_model() -> type[RawQuoteItem]:
    from app.quotes.models import RawQuoteItem

    return RawQuoteItem


class SourceDocument(Base):
    __tablename__ = "source_document"
    __table_args__ = {"info": {"evidence_immutable": True}}
    __evidence_immutable__: ClassVar[bool] = True

    id: Mapped[int] = mapped_column(primary_key=True)
    logical_name: Mapped[str] = mapped_column(String(500), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    variants: Mapped[list[SourceVariant]] = relationship(
        "SourceVariant",
        back_populates="document",
    )
    raw_items: Mapped[list[RawQuoteItem]] = relationship(
        _raw_quote_item_model,
        secondary=lambda: SourceVariant.__table__,
        primaryjoin=lambda: SourceDocument.id == SourceVariant.document_id,
        secondaryjoin=lambda: (
            SourceVariant.id
            == _raw_quote_item_model().source_variant_id
        ),
        viewonly=True,
    )


class SourceVariant(Base):
    __tablename__ = "source_variant"
    __table_args__ = (
        Index("ix_source_variant_sha256", "sha256"),
        {"info": {"evidence_immutable": True}},
    )
    __evidence_immutable__: ClassVar[bool] = True

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_document.id"),
        nullable=False,
        index=True,
    )
    path: Mapped[str] = mapped_column(String(1024), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    extension: Mapped[str] = mapped_column(String(32))
    security_state: Mapped[str] = mapped_column(String(32))
    selected_for_parsing_at_ingest: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("0"),
    )
    registered_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    document: Mapped[SourceDocument] = relationship(
        "SourceDocument",
        back_populates="variants",
    )
    raw_items: Mapped[list[RawQuoteItem]] = relationship(
        _raw_quote_item_model,
        back_populates="source_variant",
    )
