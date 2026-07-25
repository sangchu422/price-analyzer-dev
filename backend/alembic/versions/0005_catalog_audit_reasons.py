"""Persist audit reasons for catalog and document metadata versions.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-25
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("standard_item_version") as batch_op:
        batch_op.add_column(
            sa.Column(
                "change_reason",
                sa.Text(),
                server_default=sa.text("'INITIAL_CATALOG_VERSION'"),
                nullable=False,
            )
        )
    with op.batch_alter_table("document_metadata_version") as batch_op:
        batch_op.add_column(
            sa.Column(
                "reason_detail",
                sa.Text(),
                server_default=sa.text("'DOCUMENT_METADATA_REVIEW'"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("document_metadata_version") as batch_op:
        batch_op.drop_column("reason_detail")
    with op.batch_alter_table("standard_item_version") as batch_op:
        batch_op.drop_column("change_reason")
