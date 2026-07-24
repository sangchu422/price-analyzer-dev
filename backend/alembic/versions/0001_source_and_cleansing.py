"""Create immutable source and cleansing tables.

Revision ID: 0001
Revises:
Create Date: 2026-07-25
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("logical_name", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("logical_name"),
    )
    op.create_table(
        "source_variant",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("extension", sa.String(length=32), nullable=False),
        sa.Column("security_state", sa.String(length=32), nullable=False),
        sa.Column(
            "preferred_for_parsing",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "registered_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["source_document.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path"),
    )
    op.create_index(
        "ux_source_variant_sha256",
        "source_variant",
        ["sha256"],
        unique=True,
    )
    op.create_table(
        "raw_quote_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("source_variant_id", sa.Integer(), nullable=False),
        sa.Column("source_sheet", sa.String(length=255), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("source_cells", sa.Text(), nullable=True),
        sa.Column("item_name_raw", sa.Text(), nullable=True),
        sa.Column("spec_raw", sa.Text(), nullable=True),
        sa.Column("unit_raw", sa.Text(), nullable=True),
        sa.Column("quantity_raw", sa.Text(), nullable=True),
        sa.Column("unit_price_raw", sa.Text(), nullable=True),
        sa.Column("amount_raw", sa.Text(), nullable=True),
        sa.Column("maker_raw", sa.Text(), nullable=True),
        sa.Column("parser_name", sa.String(length=100), nullable=False),
        sa.Column("parser_version", sa.String(length=100), nullable=False),
        sa.Column(
            "parse_warnings_json",
            sa.Text(),
            server_default="[]",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["source_document.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_variant_id"],
            ["source_variant.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_raw_quote_item_document_id",
        "raw_quote_item",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "ix_raw_quote_item_source_variant_id",
        "raw_quote_item",
        ["source_variant_id"],
        unique=False,
    )
    op.create_table(
        "clean_decision",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("raw_item_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "INCLUDED",
                "EXCLUDED",
                "REVIEW_REQUIRED",
                name="clean_status",
                native_enum=False,
                create_constraint=True,
                validate_strings=True,
            ),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(length=100), nullable=False),
        sa.Column("reason_detail", sa.Text(), nullable=True),
        sa.Column("item_name_norm", sa.Text(), nullable=True),
        sa.Column("spec_norm", sa.Text(), nullable=True),
        sa.Column("unit_norm", sa.String(length=100), nullable=True),
        sa.Column("maker_norm", sa.Text(), nullable=True),
        sa.Column("quantity", sa.Text(), nullable=True),
        sa.Column("unit_price", sa.Text(), nullable=True),
        sa.Column("amount", sa.Text(), nullable=True),
        sa.Column("rule_version", sa.String(length=100), nullable=False),
        sa.Column(
            "decided_by",
            sa.String(length=100),
            server_default="SYSTEM",
            nullable=False,
        ),
        sa.Column(
            "decided_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["raw_item_id"],
            ["raw_quote_item.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_clean_decision_raw_item_id",
        "clean_decision",
        ["raw_item_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_clean_decision_raw_item_id",
        table_name="clean_decision",
    )
    op.drop_table("clean_decision")
    op.drop_index(
        "ix_raw_quote_item_source_variant_id",
        table_name="raw_quote_item",
    )
    op.drop_index(
        "ix_raw_quote_item_document_id",
        table_name="raw_quote_item",
    )
    op.drop_table("raw_quote_item")
    op.drop_index(
        "ux_source_variant_sha256",
        table_name="source_variant",
    )
    op.drop_table("source_variant")
    op.drop_table("source_document")
