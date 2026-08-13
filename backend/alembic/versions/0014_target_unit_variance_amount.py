"""Add target_unit_variance_amount for per-unit reference variance.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quote_analysis_line_result",
        sa.Column("target_unit_variance_amount", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("quote_analysis_line_result", "target_unit_variance_amount")
