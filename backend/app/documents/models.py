from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.quotes.models import RawQuoteItem


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SourceDocument(Base):
    __tablename__ = "source_document"

    id: Mapped[int] = mapped_column(primary_key=True)
    logical_name: Mapped[str] = mapped_column(String(500), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
    )

    variants: Mapped[list[SourceVariant]] = relationship(
        "SourceVariant",
        back_populates="document",
    )
    raw_items: Mapped[list[RawQuoteItem]] = relationship(
        "RawQuoteItem",
        back_populates="document",
    )


class SourceVariant(Base):
    __tablename__ = "source_variant"
    __table_args__ = (UniqueConstraint("sha256"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_document.id"),
        nullable=False,
    )
    path: Mapped[str] = mapped_column(String(1024), unique=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    extension: Mapped[str] = mapped_column(String(32))
    security_state: Mapped[str] = mapped_column(String(32))
    preferred_for_parsing: Mapped[bool] = mapped_column(Boolean, default=False)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
    )

    document: Mapped[SourceDocument] = relationship(
        "SourceDocument",
        back_populates="variants",
    )
