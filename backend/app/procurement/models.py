"""Append-only procurement command-center projections."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.time import utc_now
from app.db.types import ExactDecimal, NaiveUTCDateTime


class _ImmutableProcurementRow:
    __evidence_immutable__: ClassVar[bool] = True


class ItemCategory(_ImmutableProcurementRow, Base):
    __tablename__ = "item_category"
    __table_args__ = (
        UniqueConstraint("code", name="uq_item_category_code"),
        CheckConstraint("sort_order >= 0", name="ck_item_category_sort_order"),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class StandardItemCategoryAssignment(_ImmutableProcurementRow, Base):
    __tablename__ = "standard_item_category_assignment"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "standard_item_id",
            name="uq_standard_item_category_assignment_evidence_key",
        ),
        UniqueConstraint(
            "supersedes_assignment_id",
            name="uq_standard_item_category_assignment_supersedes",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 100000000",
            name="ck_standard_item_category_assignment_confidence",
        ),
        CheckConstraint(
            "json_valid(evidence_json)",
            name="ck_standard_item_category_assignment_evidence_json",
        ),
        CheckConstraint(
            "supersedes_assignment_id IS NULL OR supersedes_assignment_id <> id",
            name="ck_standard_item_category_assignment_not_self",
        ),
        ForeignKeyConstraint(
            ["supersedes_assignment_id", "standard_item_id"],
            [
                "standard_item_category_assignment.id",
                "standard_item_category_assignment.standard_item_id",
            ],
            name="fk_standard_item_category_assignment_same_item",
            ondelete="RESTRICT",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    standard_item_id: Mapped[int] = mapped_column(
        ForeignKey("standard_item.id", ondelete="RESTRICT"),
        index=True,
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("item_category.id", ondelete="RESTRICT"),
        index=True,
    )
    confidence: Mapped[Decimal] = mapped_column(ExactDecimal())
    method: Mapped[str] = mapped_column(String(100))
    evidence_json: Mapped[str] = mapped_column(Text, server_default=text("'{}'"))
    supersedes_assignment_id: Mapped[int | None] = mapped_column()
    assigned_by: Mapped[str] = mapped_column(String(100))
    assigned_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class QuoteAnalysisEquipmentGroup(_ImmutableProcurementRow, Base):
    __tablename__ = "quote_analysis_equipment_group"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id",
            "equipment_key",
            name="uq_quote_analysis_equipment_group_key",
        ),
        CheckConstraint(
            "quote_amount >= 0 AND target_amount >= 0 "
            "AND negotiation_amount >= 0 AND unallocated_amount >= 0",
            name="ck_quote_analysis_equipment_amounts_nonnegative",
        ),
        CheckConstraint(
            "line_count >= 0 AND target_available_count >= 0 "
            "AND target_available_count <= line_count",
            name="ck_quote_analysis_equipment_counts",
        ),
        CheckConstraint(
            "json_valid(mapping_evidence_json)",
            name="ck_quote_analysis_equipment_evidence_json",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("quote_analysis_run.id", ondelete="RESTRICT"),
        index=True,
    )
    equipment_key: Mapped[str] = mapped_column(String(255))
    equipment_name: Mapped[str] = mapped_column(Text)
    source_kind: Mapped[str] = mapped_column(String(64))
    quote_amount: Mapped[Decimal] = mapped_column(ExactDecimal())
    target_amount: Mapped[Decimal] = mapped_column(ExactDecimal())
    negotiation_amount: Mapped[Decimal] = mapped_column(ExactDecimal())
    unallocated_amount: Mapped[Decimal] = mapped_column(ExactDecimal())
    line_count: Mapped[int] = mapped_column(Integer)
    target_available_count: Mapped[int] = mapped_column(Integer)
    mapping_evidence_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class QuoteAnalysisEquipmentLine(_ImmutableProcurementRow, Base):
    __tablename__ = "quote_analysis_equipment_line"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id",
            "raw_item_id",
            name="uq_quote_analysis_equipment_line_raw",
        ),
        CheckConstraint(
            "quote_amount >= 0 AND negotiation_amount >= 0",
            name="ck_quote_analysis_equipment_line_amounts",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("quote_analysis_run.id", ondelete="RESTRICT"),
        index=True,
    )
    equipment_group_id: Mapped[int] = mapped_column(
        ForeignKey("quote_analysis_equipment_group.id", ondelete="RESTRICT"),
        index=True,
    )
    line_result_id: Mapped[int] = mapped_column(
        ForeignKey("quote_analysis_line_result.id", ondelete="RESTRICT"),
        unique=True,
    )
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_quote_item.id", ondelete="RESTRICT"),
        index=True,
    )
    quote_amount: Mapped[Decimal] = mapped_column(ExactDecimal())
    target_amount: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    negotiation_amount: Mapped[Decimal] = mapped_column(ExactDecimal())


class QuoteCatalogActivationRun(_ImmutableProcurementRow, Base):
    __tablename__ = "quote_catalog_activation_run"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id",
            name="uq_quote_catalog_activation_analysis_run",
        ),
        CheckConstraint(
            "json_valid(counts_json)",
            name="ck_quote_catalog_activation_counts_json",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("quote_analysis_run.id", ondelete="RESTRICT"),
        index=True,
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_document.id", ondelete="RESTRICT"),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32))
    counts_json: Mapped[str] = mapped_column(Text)
    activated_by: Mapped[str] = mapped_column(String(100))
    reason_detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class QuoteCatalogActivationEntry(_ImmutableProcurementRow, Base):
    __tablename__ = "quote_catalog_activation_entry"
    __table_args__ = (
        UniqueConstraint(
            "activation_run_id",
            "raw_item_id",
            name="uq_quote_catalog_activation_entry_raw",
        ),
        CheckConstraint(
            "json_valid(evidence_json)",
            name="ck_quote_catalog_activation_entry_evidence_json",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    activation_run_id: Mapped[int] = mapped_column(
        ForeignKey("quote_catalog_activation_run.id", ondelete="RESTRICT"),
        index=True,
    )
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_quote_item.id", ondelete="RESTRICT"),
        index=True,
    )
    action: Mapped[str] = mapped_column(String(64))
    standard_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("standard_item.id", ondelete="RESTRICT"),
    )
    standard_price_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("standard_price_version.id", ondelete="RESTRICT"),
    )
    evidence_json: Mapped[str] = mapped_column(Text)


class QuoteCatalogStateDecision(_ImmutableProcurementRow, Base):
    """Append-only document-level choice to include an analysis in the catalog."""

    __tablename__ = "quote_catalog_state_decision"
    __table_args__ = (
        UniqueConstraint(
            "supersedes_decision_id",
            name="uq_quote_catalog_state_decision_supersedes",
        ),
        CheckConstraint(
            "state IN ('NOT_INCLUDED', 'INCLUDED', 'EXCLUDED')",
            name="ck_quote_catalog_state_decision_state",
        ),
        CheckConstraint(
            "supersedes_decision_id IS NULL OR supersedes_decision_id <> id",
            name="ck_quote_catalog_state_decision_not_self",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("quote_analysis_run.id", ondelete="RESTRICT"),
        index=True,
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_document.id", ondelete="RESTRICT"),
        index=True,
    )
    state: Mapped[str] = mapped_column(String(32))
    supersedes_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("quote_catalog_state_decision.id", ondelete="RESTRICT")
    )
    decided_by: Mapped[str] = mapped_column(String(100))
    reason_detail: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class ProcurementIndicatorSyncRun(_ImmutableProcurementRow, Base):
    __tablename__ = "procurement_indicator_sync_run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('SUCCEEDED', 'FAILED')",
            name="ck_procurement_indicator_sync_status",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    indicator_code: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32))
    source_label: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(Text)
    synced_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class ProcurementIndicatorPoint(_ImmutableProcurementRow, Base):
    __tablename__ = "procurement_indicator_point"
    __table_args__ = (
        UniqueConstraint(
            "sync_run_id",
            "period",
            name="uq_procurement_indicator_point_period",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_run_id: Mapped[int] = mapped_column(
        ForeignKey("procurement_indicator_sync_run.id", ondelete="RESTRICT"),
        index=True,
    )
    period: Mapped[str] = mapped_column(String(10))
    value: Mapped[Decimal] = mapped_column(ExactDecimal())


class ProcurementPriceAlert(_ImmutableProcurementRow, Base):
    __tablename__ = "procurement_price_alert"
    __table_args__ = (
        UniqueConstraint(
            "activation_run_id",
            "raw_item_id",
            name="uq_procurement_price_alert_raw",
        ),
        CheckConstraint(
            "current_unit_price > 0 AND reference_unit_price > 0",
            name="ck_procurement_price_alert_prices",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    activation_run_id: Mapped[int] = mapped_column(
        ForeignKey("quote_catalog_activation_run.id", ondelete="RESTRICT"),
        index=True,
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_document.id", ondelete="RESTRICT"),
        index=True,
    )
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_quote_item.id", ondelete="RESTRICT"),
        index=True,
    )
    standard_item_id: Mapped[int] = mapped_column(
        ForeignKey("standard_item.id", ondelete="RESTRICT"),
        index=True,
    )
    severity: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(16))
    current_unit_price: Mapped[Decimal] = mapped_column(ExactDecimal())
    reference_unit_price: Mapped[Decimal] = mapped_column(ExactDecimal())
    difference_percent: Mapped[Decimal] = mapped_column(ExactDecimal())
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class AlertNotificationDelivery(_ImmutableProcurementRow, Base):
    __tablename__ = "alert_notification_delivery"
    __table_args__ = (
        UniqueConstraint(
            "alert_id",
            "channel",
            "recipient",
            name="uq_alert_notification_delivery_target",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(
        ForeignKey("procurement_price_alert.id", ondelete="RESTRICT"),
        index=True,
    )
    channel: Mapped[str] = mapped_column(String(32))
    recipient: Mapped[str] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(Text)
    attempted_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
