"""Name the immutable selection flag as an ingest-time snapshot.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-25
"""

from typing import Sequence

from alembic import op


revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "source_variant",
        "preferred_for_parsing",
        new_column_name="selected_for_parsing_at_ingest",
    )


def downgrade() -> None:
    op.alter_column(
        "source_variant",
        "selected_for_parsing_at_ingest",
        new_column_name="preferred_for_parsing",
    )
