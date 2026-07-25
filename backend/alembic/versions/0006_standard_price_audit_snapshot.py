"""Freeze draft fingerprints and exclusion context on price versions.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-26
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BACKUP_TABLE = "_0006_price_observation_backup"


def _detach_price_observations() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            f"""
            CREATE TEMPORARY TABLE {_BACKUP_TABLE} AS
            SELECT id, standard_price_version_id, standard_item_id,
                   raw_item_id, clean_decision_id, clean_status,
                   membership_decision_id, membership_status
            FROM standard_price_observation
            """
        )
    )
    connection.execute(sa.text("DELETE FROM standard_price_observation"))


def _restore_price_observations() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            f"""
            INSERT INTO standard_price_observation (
                id, standard_price_version_id, standard_item_id,
                raw_item_id, clean_decision_id, clean_status,
                membership_decision_id, membership_status
            )
            SELECT id, standard_price_version_id, standard_item_id,
                   raw_item_id, clean_decision_id, clean_status,
                   membership_decision_id, membership_status
            FROM {_BACKUP_TABLE}
            ORDER BY id
            """
        )
    )
    connection.execute(sa.text(f"DROP TABLE {_BACKUP_TABLE}"))


def upgrade() -> None:
    _detach_price_observations()
    try:
        with op.batch_alter_table("standard_price_version") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "standard_item_version_id",
                    sa.Integer(),
                    nullable=True,
                )
            )
            batch_op.add_column(
                sa.Column(
                    "audit_status",
                    sa.Enum(
                        "CAPTURED",
                        "LEGACY_BACKFILL",
                        name="price_audit_status",
                        native_enum=False,
                        create_constraint=True,
                    ),
                    server_default=sa.text("'LEGACY_BACKFILL'"),
                    nullable=False,
                )
            )
            batch_op.add_column(
                sa.Column(
                    "draft_fingerprint",
                    sa.String(length=64),
                    nullable=True,
                )
            )
            batch_op.add_column(
                sa.Column(
                    "excluded_count",
                    sa.Integer(),
                    server_default=sa.text("0"),
                    nullable=False,
                )
            )
            batch_op.add_column(
                sa.Column(
                    "review_required_count",
                    sa.Integer(),
                    server_default=sa.text("0"),
                    nullable=False,
                )
            )
            batch_op.add_column(
                sa.Column(
                    "exclusion_context_json",
                    sa.Text(),
                    server_default=sa.text("'[]'"),
                    nullable=False,
                )
            )
            batch_op.create_foreign_key(
                "fk_standard_price_standard_item_version",
                "standard_item_version",
                ["standard_item_version_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.create_check_constraint(
                "ck_standard_price_audit_capture",
                "(audit_status = 'CAPTURED' "
                "AND standard_item_version_id IS NOT NULL "
                "AND draft_fingerprint IS NOT NULL "
                "AND length(draft_fingerprint) = 64 "
                "AND draft_fingerprint NOT GLOB '*[^0-9a-f]*') "
                "OR (audit_status = 'LEGACY_BACKFILL' "
                "AND draft_fingerprint IS NULL)",
            )
            batch_op.create_check_constraint(
                "ck_standard_price_exclusion_counts",
                "excluded_count >= 0 AND review_required_count >= 0",
            )
            batch_op.create_check_constraint(
                "ck_standard_price_exclusion_context_json",
                "json_valid(exclusion_context_json) "
                "AND json_type(exclusion_context_json) = 'array'",
            )
        op.create_index(
            "ix_standard_price_version_standard_item_version_id",
            "standard_price_version",
            ["standard_item_version_id"],
        )
        with op.batch_alter_table(
            "standard_price_observation"
        ) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "metadata_version_id",
                    sa.Integer(),
                    nullable=True,
                )
            )
            batch_op.create_foreign_key(
                "fk_price_observation_metadata_version",
                "document_metadata_version",
                ["metadata_version_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.create_index(
                "ix_standard_price_observation_metadata_version_id",
                ["metadata_version_id"],
            )
    finally:
        _restore_price_observations()


def downgrade() -> None:
    _detach_price_observations()
    try:
        with op.batch_alter_table(
            "standard_price_observation"
        ) as batch_op:
            batch_op.drop_index(
                "ix_standard_price_observation_metadata_version_id"
            )
            batch_op.drop_constraint(
                "fk_price_observation_metadata_version",
                type_="foreignkey",
            )
            batch_op.drop_column("metadata_version_id")
        op.drop_index(
            "ix_standard_price_version_standard_item_version_id",
            table_name="standard_price_version",
        )
        with op.batch_alter_table("standard_price_version") as batch_op:
            batch_op.drop_constraint(
                "ck_standard_price_exclusion_context_json",
                type_="check",
            )
            batch_op.drop_constraint(
                "ck_standard_price_exclusion_counts",
                type_="check",
            )
            batch_op.drop_constraint(
                "ck_standard_price_audit_capture",
                type_="check",
            )
            batch_op.drop_constraint(
                "price_audit_status",
                type_="check",
            )
            batch_op.drop_constraint(
                "fk_standard_price_standard_item_version",
                type_="foreignkey",
            )
            batch_op.drop_column("exclusion_context_json")
            batch_op.drop_column("review_required_count")
            batch_op.drop_column("excluded_count")
            batch_op.drop_column("draft_fingerprint")
            batch_op.drop_column("audit_status")
            batch_op.drop_column("standard_item_version_id")
    finally:
        _restore_price_observations()
