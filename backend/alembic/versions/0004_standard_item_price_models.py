"""Add immutable standard-item and standard-price histories.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-25
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("clean_decision") as batch_op:
        batch_op.create_unique_constraint(
            "uq_clean_decision_evidence_key",
            ["id", "raw_item_id", "status"],
        )

    op.create_table(
        "standard_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "standard_item_version",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("standard_item_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("canonical_spec", sa.Text(), nullable=True),
        sa.Column("canonical_unit", sa.String(length=100), nullable=True),
        sa.Column(
            "aliases_json",
            sa.Text(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "json_valid(aliases_json) "
            "AND json_type(aliases_json) = 'array'",
            name="ck_standard_item_version_aliases_json",
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name="ck_standard_item_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["standard_item_id"],
            ["standard_item.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "standard_item_id",
            "version_number",
            name="uq_standard_item_version_parent_number",
        ),
    )
    op.create_index(
        "ix_standard_item_version_standard_item_id",
        "standard_item_version",
        ["standard_item_id"],
    )
    op.create_table(
        "document_metadata_version",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("supplier_name", sa.Text(), nullable=True),
        sa.Column("quote_date", sa.Date(), nullable=True),
        sa.Column("project_name", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name="ck_document_metadata_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_document.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_document_id",
            "version_number",
            name="uq_document_metadata_version_parent_number",
        ),
    )
    op.create_index(
        "ix_document_metadata_version_source_document_id",
        "document_metadata_version",
        ["source_document_id"],
    )
    op.create_table(
        "item_membership_decision",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("raw_item_id", sa.Integer(), nullable=False),
        sa.Column("standard_item_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "MATCHED",
                "REJECTED",
                name="membership_status",
                native_enum=False,
                create_constraint=True,
                validate_strings=True,
            ),
            nullable=False,
        ),
        sa.Column("candidate_score", sa.BigInteger(), nullable=True),
        sa.Column("method", sa.String(length=100), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("supersedes_decision_id", sa.Integer(), nullable=True),
        sa.Column("decided_by", sa.String(length=100), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "candidate_score IS NULL OR "
            "(candidate_score >= 0 AND candidate_score <= 1000000)",
            name="ck_item_membership_candidate_score",
        ),
        sa.CheckConstraint(
            "json_valid(evidence_json)",
            name="ck_item_membership_evidence_json",
        ),
        sa.CheckConstraint(
            "supersedes_decision_id IS NULL OR "
            "supersedes_decision_id <> id",
            name="ck_item_membership_not_self_superseding",
        ),
        sa.CheckConstraint(
            "(status = 'MATCHED' AND standard_item_id IS NOT NULL) OR "
            "(status = 'REJECTED' AND standard_item_id IS NULL)",
            name="ck_item_membership_status_target",
        ),
        sa.ForeignKeyConstraint(
            ["raw_item_id"],
            ["raw_quote_item.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["standard_item_id"],
            ["standard_item.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_decision_id", "raw_item_id"],
            [
                "item_membership_decision.id",
                "item_membership_decision.raw_item_id",
            ],
            name="fk_item_membership_supersedes_same_raw",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "raw_item_id",
            name="uq_item_membership_id_raw_item",
        ),
        sa.UniqueConstraint(
            "id",
            "raw_item_id",
            "standard_item_id",
            "status",
            name="uq_item_membership_evidence_key",
        ),
        sa.UniqueConstraint("supersedes_decision_id"),
    )
    op.create_index(
        "ix_item_membership_decision_raw_item_id",
        "item_membership_decision",
        ["raw_item_id"],
    )
    op.create_index(
        "ix_item_membership_decision_standard_item_id",
        "item_membership_decision",
        ["standard_item_id"],
    )
    op.create_table(
        "standard_price_version",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("standard_item_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("supplier_count", sa.Integer(), nullable=False),
        sa.Column("latest_quote_date", sa.Date(), nullable=True),
        sa.Column("minimum_price", sa.BigInteger(), nullable=False),
        sa.Column("median_price", sa.BigInteger(), nullable=False),
        sa.Column("average_price", sa.BigInteger(), nullable=False),
        sa.Column("maximum_price", sa.BigInteger(), nullable=False),
        sa.Column("calculation_version", sa.String(length=100), nullable=False),
        sa.Column("approved_by", sa.String(length=100), nullable=False),
        sa.Column(
            "approved_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "observation_count > 0",
            name="ck_standard_price_observation_count",
        ),
        sa.CheckConstraint(
            "minimum_price > 0 AND median_price > 0 "
            "AND average_price > 0 AND maximum_price > 0",
            name="ck_standard_price_positive_values",
        ),
        sa.CheckConstraint(
            "supplier_count >= 0 AND supplier_count <= observation_count",
            name="ck_standard_price_supplier_count",
        ),
        sa.CheckConstraint(
            "minimum_price <= median_price "
            "AND median_price <= maximum_price "
            "AND minimum_price <= average_price "
            "AND average_price <= maximum_price",
            name="ck_standard_price_value_order",
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name="ck_standard_price_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["standard_item_id"],
            ["standard_item.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "standard_item_id",
            "version_number",
            name="uq_standard_price_version_parent_number",
        ),
        sa.UniqueConstraint(
            "id",
            "standard_item_id",
            name="uq_standard_price_id_standard_item",
        ),
    )
    op.create_index(
        "ix_standard_price_version_standard_item_id",
        "standard_price_version",
        ["standard_item_id"],
    )
    op.create_table(
        "standard_price_observation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "standard_price_version_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("standard_item_id", sa.Integer(), nullable=False),
        sa.Column("raw_item_id", sa.Integer(), nullable=False),
        sa.Column("clean_decision_id", sa.Integer(), nullable=False),
        sa.Column(
            "clean_status",
            sa.Enum(
                "INCLUDED",
                "EXCLUDED",
                "REVIEW_REQUIRED",
                name="price_observation_clean_status",
                native_enum=False,
                create_constraint=True,
                validate_strings=True,
            ),
            server_default=sa.text("'INCLUDED'"),
            nullable=False,
        ),
        sa.Column("membership_decision_id", sa.Integer(), nullable=False),
        sa.Column(
            "membership_status",
            sa.Enum(
                "MATCHED",
                "REJECTED",
                name="price_observation_membership_status",
                native_enum=False,
                create_constraint=True,
                validate_strings=True,
            ),
            server_default=sa.text("'MATCHED'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "clean_status = 'INCLUDED'",
            name="ck_standard_price_observation_included",
        ),
        sa.CheckConstraint(
            "membership_status = 'MATCHED'",
            name="ck_standard_price_observation_matched",
        ),
        sa.ForeignKeyConstraint(
            ["clean_decision_id", "raw_item_id", "clean_status"],
            [
                "clean_decision.id",
                "clean_decision.raw_item_id",
                "clean_decision.status",
            ],
            name="fk_price_observation_clean_raw",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["standard_price_version_id", "standard_item_id"],
            [
                "standard_price_version.id",
                "standard_price_version.standard_item_id",
            ],
            name="fk_price_observation_price_item",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "standard_price_version_id",
            "clean_decision_id",
            name="uq_standard_price_observation_clean_decision",
        ),
        sa.UniqueConstraint(
            "standard_price_version_id",
            "membership_decision_id",
            name="uq_standard_price_observation_membership",
        ),
        sa.UniqueConstraint(
            "standard_price_version_id",
            "raw_item_id",
            name="uq_standard_price_observation_raw_item",
        ),
    )
    op.create_index(
        "ix_standard_price_observation_clean_decision_id",
        "standard_price_observation",
        ["clean_decision_id"],
    )
    op.create_index(
        "ix_standard_price_observation_membership_decision_id",
        "standard_price_observation",
        ["membership_decision_id"],
    )
    op.create_index(
        "ix_standard_price_observation_raw_item_id",
        "standard_price_observation",
        ["raw_item_id"],
    )
    op.create_index(
        "ix_standard_price_observation_standard_item_id",
        "standard_price_observation",
        ["standard_item_id"],
    )
    op.create_index(
        "ix_standard_price_observation_standard_price_version_id",
        "standard_price_observation",
        ["standard_price_version_id"],
    )


def _unknown_dependent_tables() -> list[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    owned_tables = {
        "standard_item",
        "standard_item_version",
        "document_metadata_version",
        "item_membership_decision",
        "standard_price_observation",
        "standard_price_version",
    }
    dependencies: list[str] = []
    for table_name in inspector.get_table_names():
        if table_name in owned_tables:
            continue
        if any(
            foreign_key["referred_table"] in owned_tables
            for foreign_key in inspector.get_foreign_keys(table_name)
        ):
            dependencies.append(table_name)
    return sorted(dependencies)


def downgrade() -> None:
    dependent_tables = _unknown_dependent_tables()
    if dependent_tables:
        raise RuntimeError(
            "Cannot downgrade 0004 before removing later dependent "
            f"tables: {', '.join(dependent_tables)}"
        )

    op.drop_index(
        "ix_standard_price_observation_standard_price_version_id",
        table_name="standard_price_observation",
    )
    op.drop_index(
        "ix_standard_price_observation_standard_item_id",
        table_name="standard_price_observation",
    )
    op.drop_index(
        "ix_standard_price_observation_raw_item_id",
        table_name="standard_price_observation",
    )
    op.drop_index(
        "ix_standard_price_observation_membership_decision_id",
        table_name="standard_price_observation",
    )
    op.drop_index(
        "ix_standard_price_observation_clean_decision_id",
        table_name="standard_price_observation",
    )
    op.drop_table("standard_price_observation")
    op.drop_index(
        "ix_standard_price_version_standard_item_id",
        table_name="standard_price_version",
    )
    op.drop_table("standard_price_version")
    op.drop_index(
        "ix_item_membership_decision_standard_item_id",
        table_name="item_membership_decision",
    )
    op.drop_index(
        "ix_item_membership_decision_raw_item_id",
        table_name="item_membership_decision",
    )
    op.drop_table("item_membership_decision")
    op.drop_index(
        "ix_document_metadata_version_source_document_id",
        table_name="document_metadata_version",
    )
    op.drop_table("document_metadata_version")
    op.drop_index(
        "ix_standard_item_version_standard_item_id",
        table_name="standard_item_version",
    )
    op.drop_table("standard_item_version")
    op.drop_table("standard_item")
    with op.batch_alter_table("clean_decision") as batch_op:
        batch_op.drop_constraint(
            "uq_clean_decision_evidence_key",
            type_="unique",
        )
