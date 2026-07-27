"""Add external market-price cache and evidence tables.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-27
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa

from app.db.types import ExactDecimal


revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_collection_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source",
            sa.Enum(
                "DEVICEMART",
                "MOUSER",
                name="market_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("query_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "SUCCEEDED",
                "FAILED",
                name="market_collection_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "collected_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(query_fingerprint) = 64",
            name="ck_market_collection_query_fingerprint",
        ),
    )
    op.create_index(
        "ix_market_collection_run_source",
        "market_collection_run",
        ["source"],
    )
    op.create_index(
        "ix_market_collection_run_query_fingerprint",
        "market_collection_run",
        ["query_fingerprint"],
    )
    op.create_index(
        "ix_market_collection_run_collected_at",
        "market_collection_run",
        ["collected_at"],
    )
    op.create_index(
        "ix_market_collection_run_expires_at",
        "market_collection_run",
        ["expires_at"],
    )

    op.create_table(
        "market_product",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source",
            sa.Enum(
                "DEVICEMART",
                "MOUSER",
                name="market_product_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("source_product_id", sa.String(255), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("manufacturer", sa.Text(), nullable=True),
        sa.Column("model_number", sa.Text(), nullable=True),
        sa.Column("product_url", sa.Text(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "source",
            "source_product_id",
            name="uq_market_product_source_id",
        ),
    )
    op.create_index("ix_market_product_source", "market_product", ["source"])

    op.create_table(
        "market_price_observation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "collection_run_id",
            sa.Integer(),
            sa.ForeignKey("market_collection_run.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("market_product.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.Column("unit_price", ExactDecimal(), nullable=False),
        sa.Column("stock_quantity", sa.Integer(), nullable=True),
        sa.Column("stock_text", sa.Text(), nullable=True),
        sa.Column("moq", sa.Integer(), nullable=True),
        sa.Column("vat_note", sa.Text(), nullable=True),
        sa.Column("shipping_note", sa.Text(), nullable=True),
        sa.Column("raw_evidence_path", sa.Text(), nullable=False),
        sa.Column("raw_sha256", sa.String(64), nullable=False),
        sa.Column("image_evidence_path", sa.Text(), nullable=True),
        sa.Column("image_sha256", sa.String(64), nullable=True),
        sa.Column("screenshot_evidence_path", sa.Text(), nullable=True),
        sa.Column("screenshot_sha256", sa.String(64), nullable=True),
        sa.CheckConstraint(
            "unit_price > 0",
            name="ck_market_observation_positive_price",
        ),
        sa.CheckConstraint(
            "moq IS NULL OR moq > 0",
            name="ck_market_observation_positive_moq",
        ),
        sa.CheckConstraint(
            "length(raw_sha256) = 64",
            name="ck_market_observation_raw_sha256",
        ),
    )
    op.create_index(
        "ix_market_price_observation_collection_run_id",
        "market_price_observation",
        ["collection_run_id"],
    )
    op.create_index(
        "ix_market_price_observation_product_id",
        "market_price_observation",
        ["product_id"],
    )

    op.create_table(
        "market_price_tier",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "observation_id",
            sa.Integer(),
            sa.ForeignKey("market_price_observation.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("minimum_quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", ExactDecimal(), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.CheckConstraint(
            "minimum_quantity > 0 AND unit_price > 0",
            name="ck_market_tier_positive_values",
        ),
        sa.UniqueConstraint(
            "observation_id",
            "minimum_quantity",
            name="uq_market_tier_observation_quantity",
        ),
    )
    op.create_index(
        "ix_market_price_tier_observation_id",
        "market_price_tier",
        ["observation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_market_price_tier_observation_id",
        table_name="market_price_tier",
    )
    op.drop_table("market_price_tier")
    op.drop_index(
        "ix_market_price_observation_product_id",
        table_name="market_price_observation",
    )
    op.drop_index(
        "ix_market_price_observation_collection_run_id",
        table_name="market_price_observation",
    )
    op.drop_table("market_price_observation")
    op.drop_index("ix_market_product_source", table_name="market_product")
    op.drop_table("market_product")
    op.drop_index(
        "ix_market_collection_run_expires_at",
        table_name="market_collection_run",
    )
    op.drop_index(
        "ix_market_collection_run_collected_at",
        table_name="market_collection_run",
    )
    op.drop_index(
        "ix_market_collection_run_query_fingerprint",
        table_name="market_collection_run",
    )
    op.drop_index(
        "ix_market_collection_run_source",
        table_name="market_collection_run",
    )
    op.drop_table("market_collection_run")
