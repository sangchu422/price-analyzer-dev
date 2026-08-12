"""Add versioned parsing and cleansing reassessment evidence.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "source_parse_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_variant_id", sa.Integer(), nullable=False),
        sa.Column("parser_name", sa.String(100), nullable=False),
        sa.Column("parser_version", sa.String(100), nullable=False),
        sa.Column("code_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_detail", sa.Text()),
        sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("finished_at", sa.DateTime()),
        sa.CheckConstraint("length(code_fingerprint) = 64", name="ck_source_parse_run_code_fingerprint"),
        sa.ForeignKeyConstraint(["source_variant_id"], ["source_variant.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("source_variant_id", "parser_version", "code_fingerprint", name="uq_source_parse_run_provenance"),
    )
    op.create_index("ix_source_parse_run_source_variant_id", "source_parse_run", ["source_variant_id"])
    op.create_index("ix_source_parse_run_current", "source_parse_run", ["source_variant_id", "status", "id"])
    op.create_table(
        "source_parse_output",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("parse_run_id", sa.Integer(), nullable=False),
        sa.Column("raw_item_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["parse_run_id"], ["source_parse_run.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["raw_item_id"], ["raw_quote_item.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("parse_run_id", "raw_item_id", name="uq_source_parse_output_run_raw"),
        sa.UniqueConstraint("raw_item_id", name="uq_source_parse_output_raw"),
    )
    op.create_index("ix_source_parse_output_parse_run_id", "source_parse_output", ["parse_run_id"])
    op.create_index("ix_source_parse_output_raw_item_id", "source_parse_output", ["raw_item_id"])
    op.create_table(
        "cleansing_reassessment_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("rule_version", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("counts_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("report_path", sa.String(1024)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("length(input_fingerprint) = 64", name="ck_cleansing_reassessment_fingerprint"),
        sa.UniqueConstraint("input_fingerprint", "rule_version", name="uq_cleansing_reassessment_provenance"),
    )
    op.create_table(
        "cleansing_reassessment_entry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("raw_item_id", sa.Integer(), nullable=False),
        sa.Column("previous_decision_id", sa.Integer()),
        sa.Column("new_decision_id", sa.Integer(), nullable=False),
        sa.Column("disposition", sa.String(64), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.ForeignKeyConstraint(["run_id"], ["cleansing_reassessment_run.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["raw_item_id"], ["raw_quote_item.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["previous_decision_id"], ["clean_decision.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["new_decision_id"], ["clean_decision.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("run_id", "raw_item_id", name="uq_cleansing_reassessment_entry"),
    )
    op.create_index("ix_cleansing_reassessment_entry_run_id", "cleansing_reassessment_entry", ["run_id"])
    op.create_index("ix_cleansing_reassessment_entry_raw_item_id", "cleansing_reassessment_entry", ["raw_item_id"])

    # Register immutable legacy outputs as reader-v1 projections.  This does
    # not alter any raw row and lets reader-v2 append a new current projection.
    op.execute(sa.text(
        "INSERT INTO source_parse_run "
        "(source_variant_id, parser_name, parser_version, code_fingerprint, status, row_count, finished_at) "
        "SELECT source_variant_id, 'quote-reader', 'reader-v1', "
        "'8d437cdfcc9ef0946c6d94b25dfc0f6cb7fb77c7dfef9c6570ecbc838720cb50', "
        "'SUCCEEDED', COUNT(*), CURRENT_TIMESTAMP FROM raw_quote_item GROUP BY source_variant_id"
    ))
    op.execute(sa.text(
        "INSERT INTO source_parse_output (parse_run_id, raw_item_id) "
        "SELECT run.id, raw.id FROM raw_quote_item raw JOIN source_parse_run run "
        "ON run.source_variant_id = raw.source_variant_id AND run.parser_version = 'reader-v1'"
    ))


def downgrade() -> None:
    op.drop_table("cleansing_reassessment_entry")
    op.drop_table("cleansing_reassessment_run")
    op.drop_table("source_parse_output")
    op.drop_table("source_parse_run")
