"""Add quote catalog state history and procurement indicator caches.

Revision ID: 0017
Revises: 0016
"""

from alembic import op
import sqlalchemy as sa


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quote_catalog_state_decision",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_run_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("supersedes_decision_id", sa.Integer()),
        sa.Column("decided_by", sa.String(100), nullable=False),
        sa.Column("reason_detail", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("state IN ('NOT_INCLUDED', 'INCLUDED', 'EXCLUDED')", name="ck_quote_catalog_state_decision_state"),
        sa.CheckConstraint("supersedes_decision_id IS NULL OR supersedes_decision_id <> id", name="ck_quote_catalog_state_decision_not_self"),
        sa.ForeignKeyConstraint(["analysis_run_id"], ["quote_analysis_run.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["document_id"], ["source_document.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supersedes_decision_id"], ["quote_catalog_state_decision.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("supersedes_decision_id", name="uq_quote_catalog_state_decision_supersedes"),
    )
    op.create_index("ix_quote_catalog_state_decision_analysis_run_id", "quote_catalog_state_decision", ["analysis_run_id"])
    op.create_index("ix_quote_catalog_state_decision_document_id", "quote_catalog_state_decision", ["document_id"])

    op.create_table(
        "procurement_indicator_sync_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("indicator_code", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("source_label", sa.String(255), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(64), nullable=False),
        sa.Column("error_detail", sa.Text()),
        sa.Column("synced_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("status IN ('SUCCEEDED', 'FAILED')", name="ck_procurement_indicator_sync_status"),
    )
    op.create_index("ix_procurement_indicator_sync_run_indicator_code", "procurement_indicator_sync_run", ["indicator_code"])
    op.create_table(
        "procurement_indicator_point",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sync_run_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(10), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["sync_run_id"], ["procurement_indicator_sync_run.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("sync_run_id", "period", name="uq_procurement_indicator_point_period"),
    )
    op.create_index("ix_procurement_indicator_point_sync_run_id", "procurement_indicator_point", ["sync_run_id"])

    # Existing successful activations are already included. Backfill their
    # current state without changing any source, membership, or price row.
    op.execute(sa.text("""
        INSERT INTO quote_catalog_state_decision
            (analysis_run_id, document_id, state, decided_by, reason_detail, decided_at)
        SELECT analysis_run_id, document_id, 'INCLUDED', activated_by,
               '0017_BACKFILL_EXISTING_ACTIVATION', created_at
        FROM quote_catalog_activation_run
        WHERE status = 'SUCCEEDED'
    """))


def downgrade() -> None:
    op.drop_table("procurement_indicator_point")
    op.drop_table("procurement_indicator_sync_run")
    op.drop_table("quote_catalog_state_decision")
