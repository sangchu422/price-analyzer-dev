"""Append-only snapshots for inflation data and quote-analysis runs."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.time import utc_now
from app.db.types import ExactDecimal, NaiveUTCDateTime


class _ImmutableAnalysisRow:
    __evidence_immutable__: ClassVar[bool] = True


class InflationSyncRun(_ImmutableAnalysisRow, Base):
    __tablename__ = "inflation_sync_run"
    __table_args__ = (
        UniqueConstraint("response_sha256", name="uq_inflation_sync_response"),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), default="KOSIS")
    org_id: Mapped[str] = mapped_column(String(32))
    table_id: Mapped[str] = mapped_column(String(64))
    item_id: Mapped[str] = mapped_column(String(64))
    classifier_code: Mapped[str] = mapped_column(String(100))
    period_type: Mapped[str] = mapped_column(String(8), default="M")
    unit: Mapped[str] = mapped_column(String(50))
    source_url: Mapped[str] = mapped_column(Text)
    source_last_changed: Mapped[date | None]
    response_sha256: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer)
    latest_period: Mapped[str] = mapped_column(String(6))
    fetched_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(), default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )


class InflationIndexPoint(_ImmutableAnalysisRow, Base):
    __tablename__ = "inflation_index_point"
    __table_args__ = (
        UniqueConstraint("sync_run_id", "period", name="uq_inflation_point_run_period"),
        CheckConstraint("length(period) = 6", name="ck_inflation_point_month"),
        CheckConstraint("index_value > 0", name="ck_inflation_point_positive"),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_run_id: Mapped[int] = mapped_column(
        ForeignKey("inflation_sync_run.id", ondelete="RESTRICT"), index=True
    )
    period: Mapped[str] = mapped_column(String(6), index=True)
    index_value: Mapped[Decimal] = mapped_column(ExactDecimal())


class QuoteAnalysisRun(_ImmutableAnalysisRow, Base):
    __tablename__ = "quote_analysis_run"
    __table_args__ = (
        CheckConstraint("review_percent >= 0", name="ck_analysis_run_review_nonnegative"),
        CheckConstraint("high_percent >= review_percent", name="ck_analysis_run_threshold_order"),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_document.id", ondelete="RESTRICT"), index=True
    )
    created_by: Mapped[str] = mapped_column(String(100))
    review_percent: Mapped[Decimal] = mapped_column(ExactDecimal())
    high_percent: Mapped[Decimal] = mapped_column(ExactDecimal())
    inflation_sync_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("inflation_sync_run.id", ondelete="RESTRICT"), index=True
    )
    target_period: Mapped[str | None] = mapped_column(String(6))
    target_index_value: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    total_line_count: Mapped[int] = mapped_column(Integer)
    target_available_count: Mapped[int] = mapped_column(Integer)
    target_unavailable_count: Mapped[int] = mapped_column(Integer)
    quote_total_amount: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    target_total_amount: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    created_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(), default=utc_now, server_default=text("CURRENT_TIMESTAMP")
    )


class QuoteAnalysisLineResult(_ImmutableAnalysisRow, Base):
    __tablename__ = "quote_analysis_line_result"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "raw_item_id", name="uq_analysis_line_run_raw"),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("quote_analysis_run.id", ondelete="RESTRICT"), index=True
    )
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_quote_item.id", ondelete="RESTRICT"), index=True
    )
    standard_price_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("standard_price_version.id", ondelete="RESTRICT")
    )
    match_status: Mapped[str] = mapped_column(String(32))
    assessment: Mapped[str] = mapped_column(String(32))
    reference_price: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    variance_amount: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    variance_percent: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    target_status: Mapped[str] = mapped_column(String(40))
    target_unit_price: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    target_amount: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    target_variance_amount: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    target_variance_percent: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    target_used_observation_count: Mapped[int] = mapped_column(Integer, default=0)
    target_excluded_observation_count: Mapped[int] = mapped_column(Integer, default=0)
    target_reason: Mapped[str] = mapped_column(Text)


class QuoteAnalysisTargetEvidence(_ImmutableAnalysisRow, Base):
    __tablename__ = "quote_analysis_target_evidence"
    __table_args__ = (
        UniqueConstraint("line_result_id", "raw_item_id", name="uq_target_evidence_line_raw"),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    line_result_id: Mapped[int] = mapped_column(
        ForeignKey("quote_analysis_line_result.id", ondelete="RESTRICT"), index=True
    )
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_quote_item.id", ondelete="RESTRICT")
    )
    metadata_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_metadata_version.id", ondelete="RESTRICT")
    )
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("source_document.id", ondelete="RESTRICT")
    )
    source_variant_id: Mapped[int] = mapped_column(
        ForeignKey("source_variant.id", ondelete="RESTRICT")
    )
    source_logical_name: Mapped[str] = mapped_column(Text)
    source_sheet: Mapped[str | None] = mapped_column(String(255))
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_row: Mapped[int | None] = mapped_column(Integer)
    source_cells: Mapped[str | None] = mapped_column(Text)
    quote_date: Mapped[date] = mapped_column()
    source_period: Mapped[str] = mapped_column(String(6))
    original_unit_price: Mapped[Decimal] = mapped_column(ExactDecimal())
    source_index_value: Mapped[Decimal] = mapped_column(ExactDecimal())
    target_index_value: Mapped[Decimal] = mapped_column(ExactDecimal())
    adjusted_unit_price: Mapped[Decimal] = mapped_column(ExactDecimal())
