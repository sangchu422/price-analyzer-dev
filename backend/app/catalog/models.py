from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.time import utc_now
from app.db.types import ExactDecimal, NaiveUTCDateTime

if TYPE_CHECKING:
    from app.documents.models import SourceDocument
    from app.quotes.models import RawQuoteItem


class MembershipStatus(StrEnum):
    MATCHED = "MATCHED"
    REJECTED = "REJECTED"


class _ImmutableCatalogRow:
    __evidence_immutable__: ClassVar[bool] = True


class StandardItem(_ImmutableCatalogRow, Base):
    """Stable identity; all descriptive state lives in version rows."""

    __tablename__ = "standard_item"
    __table_args__ = {"info": {"evidence_immutable": True}}

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    versions: Mapped[list[StandardItemVersion]] = relationship(
        "StandardItemVersion",
        back_populates="standard_item",
    )
    membership_decisions: Mapped[list[ItemMembershipDecision]] = relationship(
        "ItemMembershipDecision",
        back_populates="standard_item",
    )
    price_versions: Mapped[list[StandardPriceVersion]] = relationship(
        "StandardPriceVersion",
        back_populates="standard_item",
    )


class StandardItemVersion(_ImmutableCatalogRow, Base):
    __tablename__ = "standard_item_version"
    __table_args__ = (
        UniqueConstraint(
            "standard_item_id",
            "version_number",
            name="uq_standard_item_version_parent_number",
        ),
        CheckConstraint(
            "version_number > 0",
            name="ck_standard_item_version_positive",
        ),
        CheckConstraint(
            "json_valid(aliases_json) "
            "AND json_type(aliases_json) = 'array'",
            name="ck_standard_item_version_aliases_json",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    standard_item_id: Mapped[int] = mapped_column(
        ForeignKey("standard_item.id", ondelete="RESTRICT"),
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer)
    canonical_name: Mapped[str] = mapped_column(Text)
    canonical_spec: Mapped[str | None] = mapped_column(Text)
    canonical_unit: Mapped[str | None] = mapped_column(String(100))
    aliases_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
        server_default=text("'[]'"),
    )
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    standard_item: Mapped[StandardItem] = relationship(
        "StandardItem",
        back_populates="versions",
    )


class DocumentMetadataVersion(_ImmutableCatalogRow, Base):
    __tablename__ = "document_metadata_version"
    __table_args__ = (
        UniqueConstraint(
            "source_document_id",
            "version_number",
            name="uq_document_metadata_version_parent_number",
        ),
        CheckConstraint(
            "version_number > 0",
            name="ck_document_metadata_version_positive",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("source_document.id", ondelete="RESTRICT"),
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer)
    supplier_name: Mapped[str | None] = mapped_column(Text)
    quote_date: Mapped[date | None]
    project_name: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    source_document: Mapped[SourceDocument] = relationship("SourceDocument")


class ItemMembershipDecision(_ImmutableCatalogRow, Base):
    __tablename__ = "item_membership_decision"
    __table_args__ = (
        CheckConstraint(
            "(status = 'MATCHED' AND standard_item_id IS NOT NULL) OR "
            "(status = 'REJECTED' AND standard_item_id IS NULL)",
            name="ck_item_membership_status_target",
        ),
        CheckConstraint(
            "candidate_score IS NULL OR "
            "(candidate_score >= 0 AND candidate_score <= 1000000)",
            name="ck_item_membership_candidate_score",
        ),
        CheckConstraint(
            "json_valid(evidence_json)",
            name="ck_item_membership_evidence_json",
        ),
        CheckConstraint(
            "supersedes_decision_id IS NULL OR "
            "supersedes_decision_id <> id",
            name="ck_item_membership_not_self_superseding",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_quote_item.id", ondelete="RESTRICT"),
        index=True,
    )
    standard_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("standard_item.id", ondelete="RESTRICT"),
        index=True,
    )
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(
            MembershipStatus,
            name="membership_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        )
    )
    candidate_score: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    method: Mapped[str] = mapped_column(String(100))
    evidence_json: Mapped[str] = mapped_column(Text)
    supersedes_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("item_membership_decision.id", ondelete="RESTRICT"),
        unique=True,
    )
    decided_by: Mapped[str] = mapped_column(String(100))
    decided_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    raw_item: Mapped[RawQuoteItem] = relationship("RawQuoteItem")
    standard_item: Mapped[StandardItem | None] = relationship(
        "StandardItem",
        back_populates="membership_decisions",
    )
    supersedes: Mapped[ItemMembershipDecision | None] = relationship(
        "ItemMembershipDecision",
        remote_side="ItemMembershipDecision.id",
        foreign_keys=[supersedes_decision_id],
    )


class StandardPriceVersion(_ImmutableCatalogRow, Base):
    __tablename__ = "standard_price_version"
    __table_args__ = (
        UniqueConstraint(
            "standard_item_id",
            "version_number",
            name="uq_standard_price_version_parent_number",
        ),
        CheckConstraint(
            "version_number > 0",
            name="ck_standard_price_version_positive",
        ),
        CheckConstraint(
            "observation_count > 0",
            name="ck_standard_price_observation_count",
        ),
        CheckConstraint(
            "supplier_count >= 0 AND supplier_count <= observation_count",
            name="ck_standard_price_supplier_count",
        ),
        CheckConstraint(
            "minimum_price > 0 AND median_price > 0 "
            "AND average_price > 0 AND maximum_price > 0",
            name="ck_standard_price_positive_values",
        ),
        CheckConstraint(
            "minimum_price <= median_price "
            "AND median_price <= maximum_price "
            "AND minimum_price <= average_price "
            "AND average_price <= maximum_price",
            name="ck_standard_price_value_order",
        ),
        CheckConstraint(
            "json_valid(observation_decision_ids_json) "
            "AND json_type(observation_decision_ids_json) = 'array' "
            "AND json_array_length(observation_decision_ids_json) > 0 "
            "AND json_array_length(observation_decision_ids_json) "
            "= observation_count",
            name="ck_standard_price_observation_ids_json",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    standard_item_id: Mapped[int] = mapped_column(
        ForeignKey("standard_item.id", ondelete="RESTRICT"),
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer)
    observation_count: Mapped[int] = mapped_column(Integer)
    supplier_count: Mapped[int] = mapped_column(Integer)
    latest_quote_date: Mapped[date | None]
    minimum_price: Mapped[Decimal] = mapped_column(ExactDecimal())
    median_price: Mapped[Decimal] = mapped_column(ExactDecimal())
    average_price: Mapped[Decimal] = mapped_column(ExactDecimal())
    maximum_price: Mapped[Decimal] = mapped_column(ExactDecimal())
    observation_decision_ids_json: Mapped[str] = mapped_column(Text)
    calculation_version: Mapped[str] = mapped_column(String(100))
    approved_by: Mapped[str] = mapped_column(String(100))
    approved_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    standard_item: Mapped[StandardItem] = relationship(
        "StandardItem",
        back_populates="price_versions",
    )
