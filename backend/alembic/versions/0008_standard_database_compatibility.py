"""Recover databases stamped with the rejected legacy 0007 migration.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-26
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_TABLES = (
    "standard_item_external_code",
    "legacy_reconciliation_decision",
    "legacy_raw_item_link",
    "legacy_source_row_snapshot",
    "legacy_standard_snapshot",
    "legacy_reconciliation_run",
)
_PROTECTED_LEGACY_TABLES = (
    "legacy_reconciliation_decision",
    "standard_item_external_code",
)


def _assert_legacy_cleanup_is_safe(tables: set[str]) -> None:
    """Refuse cleanup when rejected-workflow approval evidence exists."""

    connection = op.get_bind()
    protected_with_rows = [
        table_name
        for table_name in _PROTECTED_LEGACY_TABLES
        if table_name in tables
        and connection.execute(
            sa.text(f"SELECT COUNT(*) FROM {table_name}")
        ).scalar_one()
        > 0
    ]
    if protected_with_rows:
        raise RuntimeError(
            "Cannot automatically remove approved legacy decisions or "
            "external code evidence; manual recovery is required "
            f"for: {', '.join(protected_with_rows)}"
        )


def _create_quote_document_role(table_name: str) -> None:
    op.create_table(
        table_name,
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
        sa.Column("supersedes_role_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["supersedes_role_id", "document_id"],
            [f"{table_name}.id", f"{table_name}.document_id"],
            name="fk_quote_document_role_supersedes_same_document",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "document_id",
            name="uq_quote_document_role_id_document",
        ),
        sa.UniqueConstraint(
            "supersedes_role_id",
            name="uq_quote_document_role_supersedes",
        ),
    )


def _create_standard_database_build_run() -> None:
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


def _quote_document_role_needs_rebuild() -> bool:
    foreign_keys = sa.inspect(op.get_bind()).get_foreign_keys(
        "quote_document_role"
    )
    return not any(
        foreign_key["constrained_columns"]
        == ["supersedes_role_id", "document_id"]
        and foreign_key["referred_table"] == "quote_document_role"
        and foreign_key["referred_columns"] == ["id", "document_id"]
        for foreign_key in foreign_keys
    )


def _rebuild_quote_document_role() -> None:
    replacement = "_0008_quote_document_role_replacement"
    _create_quote_document_role(replacement)
    connection = op.get_bind()
    connection.execute(
        sa.text(
            f"""
            INSERT INTO {replacement} (
                id, document_id, purpose, supersedes_role_id,
                decided_by, reason_detail, decided_at
            )
            SELECT id, document_id, purpose, supersedes_role_id,
                   decided_by, reason_detail, decided_at
            FROM quote_document_role
            """
        )
    )
    op.drop_index(
        "ix_quote_document_role_document_id",
        table_name="quote_document_role",
    )
    op.drop_table("quote_document_role")
    op.rename_table(replacement, "quote_document_role")
    op.create_index(
        "ix_quote_document_role_document_id",
        "quote_document_role",
        ["document_id"],
    )


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    _assert_legacy_cleanup_is_safe(tables)
    if "quote_document_role" not in tables:
        _create_quote_document_role("quote_document_role")
        op.create_index(
            "ix_quote_document_role_document_id",
            "quote_document_role",
            ["document_id"],
        )
    elif _quote_document_role_needs_rebuild():
        _rebuild_quote_document_role()
    if "standard_database_build_run" not in tables:
        _create_standard_database_build_run()

    for table_name in _LEGACY_TABLES:
        if table_name in tables:
            op.drop_table(table_name)


def downgrade() -> None:
    """Compatibility repair is intentionally retained until 0007 downgrades."""
