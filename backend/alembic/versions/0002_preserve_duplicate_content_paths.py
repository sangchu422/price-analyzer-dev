"""Preserve distinct evidence paths that have identical content.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-25
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


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
    duplicate = op.get_bind().execute(
        sa.text(
            """
            SELECT sha256, COUNT(*) AS path_count
            FROM source_variant
            GROUP BY sha256
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).mappings().first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot downgrade 0002: duplicate SHA-256 evidence paths "
            f"exist ({duplicate['path_count']} paths for "
            f"{duplicate['sha256']}). Revision 0001 cannot represent "
            "those paths without evidence loss. Keep revision 0002, or "
            "migrate every evidence path to another lossless store before "
            "retrying."
        )

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
