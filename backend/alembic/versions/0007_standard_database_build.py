"""Add append-only document roles and standard-database build evidence.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-26
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quote_document_role",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("source_document.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "purpose",
            sa.Enum(
                "HISTORICAL_REFERENCE",
                "INCOMING_BID",
                name="quote_document_purpose",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "supersedes_role_id",
            sa.Integer(),
            sa.ForeignKey("quote_document_role.id", ondelete="RESTRICT"),
            unique=True,
            nullable=True,
        ),
        sa.Column("decided_by", sa.String(100), nullable=False),
        sa.Column("reason_detail", sa.Text(), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "supersedes_role_id IS NULL OR supersedes_role_id <> id",
            name="ck_quote_document_role_not_self_superseding",
        ),
    )
    op.create_index(
        "ix_quote_document_role_document_id",
        "quote_document_role",
        ["document_id"],
    )
    op.create_table(
        "standard_database_build_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("rule_version", sa.String(100), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "RUNNING",
                "SUCCEEDED",
                "FAILED",
                name="standard_build_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default=sa.text("'RUNNING'"),
            nullable=False,
        ),
        sa.Column("report_path", sa.String(1024), nullable=True),
        sa.Column(
            "counts_json",
            sa.Text(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64",
            name="ck_standard_database_build_input_fingerprint",
        ),
    )
    op.create_index(
        "uq_standard_database_build_success_input_rule",
        "standard_database_build_run",
        ["input_fingerprint", "rule_version"],
        unique=True,
        sqlite_where=sa.text("status = 'SUCCEEDED'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_standard_database_build_success_input_rule",
        table_name="standard_database_build_run",
    )
    op.drop_table("standard_database_build_run")
    op.drop_index(
        "ix_quote_document_role_document_id",
        table_name="quote_document_role",
    )
    op.drop_table("quote_document_role")
