"""Add auditable inflation-backed quote analysis runs.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-11
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _money(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.BigInteger(), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "inflation_sync_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("org_id", sa.String(32), nullable=False),
        sa.Column("table_id", sa.String(64), nullable=False),
        sa.Column("item_id", sa.String(64), nullable=False),
        sa.Column("classifier_code", sa.String(100), nullable=False),
        sa.Column("period_type", sa.String(8), nullable=False),
        sa.Column("unit", sa.String(50), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_last_changed", sa.Date(), nullable=True),
        sa.Column("response_sha256", sa.String(64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("latest_period", sa.String(6), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("response_sha256", name="uq_inflation_sync_response"),
    )
    op.create_table(
        "inflation_index_point",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sync_run_id", sa.Integer(), sa.ForeignKey("inflation_sync_run.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("period", sa.String(6), nullable=False),
        _money("index_value", nullable=False),
        sa.CheckConstraint("length(period) = 6", name="ck_inflation_point_month"),
        sa.CheckConstraint("index_value > 0", name="ck_inflation_point_positive"),
        sa.UniqueConstraint("sync_run_id", "period", name="uq_inflation_point_run_period"),
    )
    op.create_index("ix_inflation_index_point_sync_run_id", "inflation_index_point", ["sync_run_id"])
    op.create_index("ix_inflation_index_point_period", "inflation_index_point", ["period"])

    op.create_table(
        "quote_analysis_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("source_document.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        _money("review_percent", nullable=False),
        _money("high_percent", nullable=False),
        sa.Column("inflation_sync_run_id", sa.Integer(), sa.ForeignKey("inflation_sync_run.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("target_period", sa.String(6), nullable=True),
        _money("target_index_value"),
        sa.Column("total_line_count", sa.Integer(), nullable=False),
        sa.Column("target_available_count", sa.Integer(), nullable=False),
        sa.Column("target_unavailable_count", sa.Integer(), nullable=False),
        _money("quote_total_amount"),
        _money("target_total_amount"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("review_percent >= 0", name="ck_analysis_run_review_nonnegative"),
        sa.CheckConstraint("high_percent >= review_percent", name="ck_analysis_run_threshold_order"),
    )
    op.create_index("ix_quote_analysis_run_document_id", "quote_analysis_run", ["document_id"])
    op.create_index("ix_quote_analysis_run_inflation_sync_run_id", "quote_analysis_run", ["inflation_sync_run_id"])

    op.create_table(
        "quote_analysis_line_result",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_run_id", sa.Integer(), sa.ForeignKey("quote_analysis_run.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("raw_item_id", sa.Integer(), sa.ForeignKey("raw_quote_item.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("standard_price_version_id", sa.Integer(), sa.ForeignKey("standard_price_version.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("match_status", sa.String(32), nullable=False),
        sa.Column("assessment", sa.String(32), nullable=False),
        _money("reference_price"), _money("variance_amount"), _money("variance_percent"),
        sa.Column("target_status", sa.String(40), nullable=False),
        _money("target_unit_price"), _money("target_amount"), _money("target_variance_amount"), _money("target_variance_percent"),
        sa.Column("target_used_observation_count", sa.Integer(), nullable=False),
        sa.Column("target_excluded_observation_count", sa.Integer(), nullable=False),
        sa.Column("target_reason", sa.Text(), nullable=False),
        sa.UniqueConstraint("analysis_run_id", "raw_item_id", name="uq_analysis_line_run_raw"),
    )
    op.create_index("ix_quote_analysis_line_result_analysis_run_id", "quote_analysis_line_result", ["analysis_run_id"])
    op.create_index("ix_quote_analysis_line_result_raw_item_id", "quote_analysis_line_result", ["raw_item_id"])

    op.create_table(
        "quote_analysis_target_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("line_result_id", sa.Integer(), sa.ForeignKey("quote_analysis_line_result.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("raw_item_id", sa.Integer(), sa.ForeignKey("raw_quote_item.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("metadata_version_id", sa.Integer(), sa.ForeignKey("document_metadata_version.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_document_id", sa.Integer(), sa.ForeignKey("source_document.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_variant_id", sa.Integer(), sa.ForeignKey("source_variant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_logical_name", sa.Text(), nullable=False),
        sa.Column("source_sheet", sa.String(255)), sa.Column("source_page", sa.Integer()), sa.Column("source_row", sa.Integer()), sa.Column("source_cells", sa.Text()),
        sa.Column("quote_date", sa.Date(), nullable=False),
        sa.Column("source_period", sa.String(6), nullable=False),
        _money("original_unit_price", nullable=False), _money("source_index_value", nullable=False), _money("target_index_value", nullable=False), _money("adjusted_unit_price", nullable=False),
        sa.UniqueConstraint("line_result_id", "raw_item_id", name="uq_target_evidence_line_raw"),
    )
    op.create_index("ix_quote_analysis_target_evidence_line_result_id", "quote_analysis_target_evidence", ["line_result_id"])


def downgrade() -> None:
    op.drop_table("quote_analysis_target_evidence")
    op.drop_table("quote_analysis_line_result")
    op.drop_table("quote_analysis_run")
    op.drop_table("inflation_index_point")
    op.drop_table("inflation_sync_run")
