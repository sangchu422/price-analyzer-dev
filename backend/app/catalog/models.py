from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.cleansing.models import CleanDecision, CleanStatus
from app.db.base import Base
from app.db.time import utc_now
from app.db.types import ExactDecimal, NaiveUTCDateTime

if TYPE_CHECKING:
    from app.documents.models import SourceDocument
    from app.quotes.models import RawQuoteItem


class MembershipStatus(StrEnum):
    MATCHED = "MATCHED"
    REJECTED = "REJECTED"


class CatalogIntegrityError(RuntimeError):
    """Raised when an ORM write would create mutable catalog evidence."""


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
    change_reason: Mapped[str] = mapped_column(
        Text,
        default="INITIAL_CATALOG_VERSION",
        server_default=text("'INITIAL_CATALOG_VERSION'"),
    )
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
    reason_detail: Mapped[str] = mapped_column(
        Text,
        default="DOCUMENT_METADATA_REVIEW",
        server_default=text("'DOCUMENT_METADATA_REVIEW'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    source_document: Mapped[SourceDocument] = relationship("SourceDocument")


class ItemMembershipDecision(_ImmutableCatalogRow, Base):
    __tablename__ = "item_membership_decision"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "raw_item_id",
            name="uq_item_membership_id_raw_item",
        ),
        UniqueConstraint(
            "id",
            "raw_item_id",
            "standard_item_id",
            "status",
            name="uq_item_membership_evidence_key",
        ),
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
        ForeignKeyConstraint(
            ["supersedes_decision_id", "raw_item_id"],
            [
                "item_membership_decision.id",
                "item_membership_decision.raw_item_id",
            ],
            name="fk_item_membership_supersedes_same_raw",
            ondelete="RESTRICT",
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
        primaryjoin="ItemMembershipDecision.supersedes_decision_id "
        "== ItemMembershipDecision.id",
    )


class StandardPriceVersion(_ImmutableCatalogRow, Base):
    __session_core_insert_forbidden__: ClassVar[bool] = True
    __tablename__ = "standard_price_version"
    __table_args__ = (
        UniqueConstraint(
            "standard_item_id",
            "version_number",
            name="uq_standard_price_version_parent_number",
        ),
        UniqueConstraint(
            "id",
            "standard_item_id",
            name="uq_standard_price_id_standard_item",
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
        {
            "info": {
                "evidence_immutable": True,
                "session_core_insert_forbidden": True,
            }
        },
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
    observations: Mapped[list[StandardPriceObservation]] = relationship(
        "StandardPriceObservation",
        back_populates="standard_price_version",
        overlaps="membership_decision",
    )


class StandardPriceObservation(_ImmutableCatalogRow, Base):
    """Normalized evidence link belonging to one immutable price version."""

    __tablename__ = "standard_price_observation"
    __session_core_insert_forbidden__: ClassVar[bool] = True
    __table_args__ = (
        UniqueConstraint(
            "standard_price_version_id",
            "raw_item_id",
            name="uq_standard_price_observation_raw_item",
        ),
        UniqueConstraint(
            "standard_price_version_id",
            "clean_decision_id",
            name="uq_standard_price_observation_clean_decision",
        ),
        UniqueConstraint(
            "standard_price_version_id",
            "membership_decision_id",
            name="uq_standard_price_observation_membership",
        ),
        CheckConstraint(
            "clean_status = 'INCLUDED'",
            name="ck_standard_price_observation_included",
        ),
        CheckConstraint(
            "membership_status = 'MATCHED'",
            name="ck_standard_price_observation_matched",
        ),
        ForeignKeyConstraint(
            ["standard_price_version_id", "standard_item_id"],
            [
                "standard_price_version.id",
                "standard_price_version.standard_item_id",
            ],
            name="fk_price_observation_price_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["clean_decision_id", "raw_item_id", "clean_status"],
            [
                "clean_decision.id",
                "clean_decision.raw_item_id",
                "clean_decision.status",
            ],
            name="fk_price_observation_clean_raw",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "membership_decision_id",
                "raw_item_id",
                "standard_item_id",
                "membership_status",
            ],
            [
                "item_membership_decision.id",
                "item_membership_decision.raw_item_id",
                "item_membership_decision.standard_item_id",
                "item_membership_decision.status",
            ],
            name="fk_price_observation_membership_evidence",
            ondelete="RESTRICT",
        ),
        {
            "info": {
                "evidence_immutable": True,
                "session_core_insert_forbidden": True,
            }
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    standard_price_version_id: Mapped[int] = mapped_column(index=True)
    standard_item_id: Mapped[int] = mapped_column(index=True)
    raw_item_id: Mapped[int] = mapped_column(index=True)
    clean_decision_id: Mapped[int] = mapped_column(index=True)
    clean_status: Mapped[CleanStatus] = mapped_column(
        Enum(
            CleanStatus,
            name="price_observation_clean_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=CleanStatus.INCLUDED,
        server_default=text("'INCLUDED'"),
    )
    membership_decision_id: Mapped[int] = mapped_column(index=True)
    membership_status: Mapped[MembershipStatus] = mapped_column(
        Enum(
            MembershipStatus,
            name="price_observation_membership_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=MembershipStatus.MATCHED,
        server_default=text("'MATCHED'"),
    )

    standard_price_version: Mapped[StandardPriceVersion] = relationship(
        "StandardPriceVersion",
        back_populates="observations",
        foreign_keys=[standard_price_version_id, standard_item_id],
        overlaps="membership_decision",
    )
    clean_decision: Mapped[CleanDecision] = relationship(
        "CleanDecision",
        foreign_keys=[clean_decision_id, raw_item_id, clean_status],
        overlaps="membership_decision,standard_price_version",
    )
    membership_decision: Mapped[ItemMembershipDecision] = relationship(
        "ItemMembershipDecision",
        foreign_keys=[
            membership_decision_id,
            raw_item_id,
            standard_item_id,
            membership_status,
        ],
        overlaps="clean_decision,standard_price_version",
    )


@event.listens_for(Session, "before_flush")
def validate_new_standard_price_evidence(
    session: Session,
    flush_context: object,
    instances: object,
) -> None:
    """Keep price evidence atomic in application-managed transactions.

    Native database maintenance outside a SQLAlchemy Session remains a
    trusted boundary; relational foreign keys still protect link integrity.
    """

    new_rows = set(session.new)
    for row in new_rows:
        if isinstance(row, StandardPriceVersion):
            if len(row.observations) != row.observation_count:
                raise CatalogIntegrityError(
                    "a new standard price version must include exactly "
                    "observation_count normalized observations"
                )
        elif isinstance(row, StandardPriceObservation):
            if row.standard_price_version not in new_rows:
                raise CatalogIntegrityError(
                    "price observations may only be created atomically "
                    "with a new standard price version"
                )
