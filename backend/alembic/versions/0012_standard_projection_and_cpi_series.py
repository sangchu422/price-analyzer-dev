"""Add standard-build projections and multi-series inflation support.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-12
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LEGACY_CALCULATION_FINGERPRINT = (
    "d804f3dc7779498eca8cc4dc0004810d9dbe21c6e8ea7246f1825c34ec55d5f0"
)
_LEGACY_CODE_FINGERPRINT = (
    "be76a22a98c8b6eac6ad52507dbfcdb095076faed4f5dcdd8d1f3bf45bbe8557"
)


def _upgrade_build_run_provenance() -> None:
    with op.batch_alter_table(
        "standard_database_build_run",
        recreate="always",
    ) as batch_op:
        batch_op.drop_index("uq_standard_database_build_success_input_rule")
        batch_op.add_column(
            sa.Column(
                "calculation_fingerprint",
                sa.String(length=64),
                nullable=False,
                server_default=sa.text(
                    f"'{_LEGACY_CALCULATION_FINGERPRINT}'"
                ),
            )
        )
        batch_op.add_column(
            sa.Column(
                "code_fingerprint",
                sa.String(length=64),
                nullable=False,
                server_default=sa.text(f"'{_LEGACY_CODE_FINGERPRINT}'"),
            )
        )
        batch_op.create_check_constraint(
            "ck_standard_database_build_calculation_fingerprint",
            "length(calculation_fingerprint) = 64",
        )
        batch_op.create_check_constraint(
            "ck_standard_database_build_code_fingerprint",
            "length(code_fingerprint) = 64",
        )
    # Pre-0012 allowed the same evidence input under different rule versions.
    # The new provenance key is stricter, so give every immutable legacy run a
    # deterministic, per-run calculation marker before creating its index.
    # It is intentionally not comparable with a V6 calculation fingerprint.
    op.execute(
        sa.text(
            "UPDATE standard_database_build_run SET "
            "calculation_fingerprint = "
            "printf('%016x', id) || substr(input_fingerprint, 1, 48)"
        )
    )
    op.create_index(
        "uq_standard_database_build_success_provenance",
        "standard_database_build_run",
        [
            "input_fingerprint",
            "calculation_fingerprint",
            "code_fingerprint",
        ],
        unique=True,
        sqlite_where=sa.text("status = 'SUCCEEDED'"),
    )


def _downgrade_build_run_provenance() -> None:
    with op.batch_alter_table(
        "standard_database_build_run",
        recreate="always",
    ) as batch_op:
        batch_op.drop_index("uq_standard_database_build_success_provenance")
        batch_op.drop_constraint(
            "ck_standard_database_build_code_fingerprint",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_standard_database_build_calculation_fingerprint",
            type_="check",
        )
        batch_op.drop_column("code_fingerprint")
        batch_op.drop_column("calculation_fingerprint")
        batch_op.create_index(
            "uq_standard_database_build_success_input_rule",
            ["input_fingerprint", "rule_version"],
            unique=True,
            sqlite_where=sa.text("status = 'SUCCEEDED'"),
        )


def _upgrade_inflation_schema() -> None:
    op.add_column(
        "inflation_sync_run",
        sa.Column(
            "series_kind",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'PPI_ALL'"),
        ),
    )
    op.create_index(
        "ix_inflation_sync_run_series_latest",
        "inflation_sync_run",
        ["series_kind", "fetched_at", "id"],
    )
    with op.batch_alter_table(
        "inflation_index_point",
        recreate="always",
    ) as batch_op:
        batch_op.drop_constraint(
            "ck_inflation_point_month",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_inflation_point_positive",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_inflation_point_month",
            "length(period) IN (4, 6)",
        )
        batch_op.create_check_constraint(
            "ck_inflation_point_positive",
            "index_value > -100000000",
        )


def _downgrade_inflation_schema() -> None:
    # The pre-0012 schema only admits positive six-digit monthly PPI points.
    # Drop the new annual CPI series before recreating those constraints so a
    # reversible downgrade cannot fail after the CPI cache has been used.
    op.execute(
        sa.text(
            "DELETE FROM inflation_index_point "
            "WHERE sync_run_id IN ("
            "SELECT id FROM inflation_sync_run WHERE series_kind = 'CPI_ALL'"
            ")"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM inflation_sync_run WHERE series_kind = 'CPI_ALL'"
        )
    )
    with op.batch_alter_table(
        "inflation_index_point",
        recreate="always",
    ) as batch_op:
        batch_op.drop_constraint(
            "ck_inflation_point_month",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_inflation_point_positive",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_inflation_point_month",
            "length(period) = 6",
        )
        batch_op.create_check_constraint(
            "ck_inflation_point_positive",
            "index_value > 0",
        )
    op.drop_index(
        "ix_inflation_sync_run_series_latest",
        table_name="inflation_sync_run",
    )
    with op.batch_alter_table(
        "inflation_sync_run",
        recreate="always",
    ) as batch_op:
        batch_op.drop_column("series_kind")


def _create_standard_projection_tables() -> None:
    op.create_table(
        "standard_database_build_projection",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("build_run_id", sa.Integer(), nullable=False),
        sa.Column("standard_item_id", sa.Integer(), nullable=False),
        sa.Column("standard_item_version_id", sa.Integer(), nullable=True),
        sa.Column("standard_price_version_id", sa.Integer(), nullable=True),
        sa.Column(
            "operational_status",
            sa.Enum(
                "ACTIVE",
                "REBUILD_REQUIRED",
                "NO_ELIGIBLE_EVIDENCE",
                name="standard_operational_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "projected_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "(operational_status = 'NO_ELIGIBLE_EVIDENCE' "
            "AND standard_price_version_id IS NULL) OR "
            "(operational_status IN ('ACTIVE', 'REBUILD_REQUIRED') "
            "AND standard_price_version_id IS NOT NULL)",
            name="ck_standard_database_projection_price_status",
        ),
        sa.ForeignKeyConstraint(
            ["build_run_id"],
            ["standard_database_build_run.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["standard_item_id"],
            ["standard_item.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["standard_item_version_id", "standard_item_id"],
            [
                "standard_item_version.id",
                "standard_item_version.standard_item_id",
            ],
            name="fk_standard_database_projection_item_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["standard_price_version_id", "standard_item_id"],
            [
                "standard_price_version.id",
                "standard_price_version.standard_item_id",
            ],
            name="fk_standard_database_projection_price_version",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "build_run_id",
            "standard_item_id",
            name="uq_standard_database_build_projection_item",
        ),
    )
    op.create_index(
        "ix_standard_database_build_projection_build_run_id",
        "standard_database_build_projection",
        ["build_run_id"],
    )
    op.create_index(
        "ix_standard_database_build_projection_standard_item_id",
        "standard_database_build_projection",
        ["standard_item_id"],
    )
    op.create_index(
        "ix_standard_database_build_projection_standard_item_version_id",
        "standard_database_build_projection",
        ["standard_item_version_id"],
    )
    op.create_index(
        "ix_standard_database_build_projection_standard_price_version_id",
        "standard_database_build_projection",
        ["standard_price_version_id"],
    )

    op.create_table(
        "standard_price_observation_lineage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "standard_price_observation_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("raw_item_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["standard_price_observation_id"],
            ["standard_price_observation.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["raw_item_id"],
            ["raw_quote_item.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "standard_price_observation_id",
            "raw_item_id",
            name="uq_standard_price_observation_lineage_raw",
        ),
    )
    op.create_index(
        "ix_standard_price_observation_lineage_standard_price_observation_id",
        "standard_price_observation_lineage",
        ["standard_price_observation_id"],
    )
    op.create_index(
        "ix_standard_price_observation_lineage_raw_item_id",
        "standard_price_observation_lineage",
        ["raw_item_id"],
    )
    op.execute(
        sa.text(
            "INSERT INTO standard_price_observation_lineage "
            "(standard_price_observation_id, raw_item_id) "
            "SELECT id, raw_item_id FROM standard_price_observation"
        )
    )


def _drop_standard_projection_tables() -> None:
    op.drop_index(
        "ix_standard_price_observation_lineage_raw_item_id",
        table_name="standard_price_observation_lineage",
    )
    op.drop_index(
        "ix_standard_price_observation_lineage_standard_price_observation_id",
        table_name="standard_price_observation_lineage",
    )
    op.drop_table("standard_price_observation_lineage")
    for index_name in (
        "ix_standard_database_build_projection_standard_price_version_id",
        "ix_standard_database_build_projection_standard_item_version_id",
        "ix_standard_database_build_projection_standard_item_id",
        "ix_standard_database_build_projection_build_run_id",
    ):
        op.drop_index(index_name, table_name="standard_database_build_projection")
    op.drop_table("standard_database_build_projection")


def upgrade() -> None:
    _upgrade_build_run_provenance()
    _create_standard_projection_tables()
    _upgrade_inflation_schema()


def downgrade() -> None:
    _downgrade_inflation_schema()
    _drop_standard_projection_tables()
    _downgrade_build_run_provenance()
