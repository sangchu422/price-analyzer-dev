"""Preserve distinct evidence paths that have identical content.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-25
"""

from typing import Sequence

from alembic import op


revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "ux_source_variant_sha256",
        table_name="source_variant",
    )
    op.create_index(
        "ix_source_variant_sha256",
        "source_variant",
        ["sha256"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_source_variant_sha256",
        table_name="source_variant",
    )
    op.create_index(
        "ux_source_variant_sha256",
        "source_variant",
        ["sha256"],
        unique=True,
    )
