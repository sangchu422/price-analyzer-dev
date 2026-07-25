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

_LEGACY_FINGERPRINT = "0" * 64


def _detach_price_observations() -> list[dict[str, object]]:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT id, standard_price_version_id, standard_item_id,
                   raw_item_id, clean_decision_id, clean_status,
                   membership_decision_id, membership_status
            FROM standard_price_observation
            ORDER BY id
            """
        )
    ).mappings()
    snapshot = [dict(row) for row in rows]
    connection.execute(sa.text("DELETE FROM standard_price_observation"))
    return snapshot


def _restore_price_observations(
    snapshot: list[dict[str, object]],
) -> None:
    if not snapshot:
        return
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO standard_price_observation (
                id, standard_price_version_id, standard_item_id,
                raw_item_id, clean_decision_id, clean_status,
                membership_decision_id, membership_status
            ) VALUES (
                :id, :standard_price_version_id, :standard_item_id,
                :raw_item_id, :clean_decision_id, :clean_status,
                :membership_decision_id, :membership_status
            )
            """
        ),
        snapshot,
    )


def upgrade() -> None:
    op.add_column(
        "standard_price_version",
        sa.Column(
            "draft_fingerprint",
            sa.String(length=64),
            server_default=sa.text(f"'{_LEGACY_FINGERPRINT}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "standard_price_version",
        sa.Column(
            "excluded_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "standard_price_version",
        sa.Column(
            "review_required_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "standard_price_version",
        sa.Column(
            "exclusion_context_json",
            sa.Text(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE standard_price_version
        SET draft_fingerprint = printf('%064x', id)
        WHERE draft_fingerprint = '"""
        + _LEGACY_FINGERPRINT
        + "'"
    )
    observations = _detach_price_observations()
    try:
        with op.batch_alter_table("standard_price_version") as batch_op:
            batch_op.create_check_constraint(
                "ck_standard_price_draft_fingerprint",
                "length(draft_fingerprint) = 64 "
                "AND draft_fingerprint NOT GLOB '*[^0-9a-f]*'",
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
    finally:
        _restore_price_observations(observations)


def downgrade() -> None:
    observations = _detach_price_observations()
    try:
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
                "ck_standard_price_draft_fingerprint",
                type_="check",
            )
            batch_op.drop_column("exclusion_context_json")
            batch_op.drop_column("review_required_count")
            batch_op.drop_column("excluded_count")
            batch_op.drop_column("draft_fingerprint")
    finally:
        _restore_price_observations(observations)
