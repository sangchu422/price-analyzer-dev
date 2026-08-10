"""Add source-backed document metadata audit evidence.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-10
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clean_decision",
        sa.Column(
            "reason_evidence_json",
            sa.Text(),
            server_default=sa.text("'{}'"),
            nullable=False,
        )
    )
    op.add_column(
        "document_metadata_version",
        sa.Column(
            "evidence_json",
            sa.Text(),
            server_default=sa.text("'{}'"),
            nullable=False,
        )
    )

    op.create_table(
        "document_metadata_scan",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_variant_id", sa.Integer(), nullable=True),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.String(length=100), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("open_status", sa.String(length=32), nullable=False),
        sa.Column("content_status", sa.String(length=32), nullable=False),
        sa.Column("review_status", sa.String(length=32), nullable=False),
        sa.Column("acquisition_channel", sa.String(length=100), nullable=True),
        sa.Column(
            "diagnostics_json",
            sa.Text(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "scanned_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "json_valid(diagnostics_json)",
            name="ck_document_metadata_scan_diagnostics_json",
        ),
        sa.ForeignKeyConstraint(
            ["source_variant_id"],
            ["source_variant.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_path",
            "input_fingerprint",
            "rule_version",
            name="uq_document_metadata_scan_file_rule",
        ),
    )
    op.create_index(
        "ix_document_metadata_scan_source_variant_id",
        "document_metadata_scan",
        ["source_variant_id"],
        unique=False,
    )

    op.create_table(
        "document_metadata_candidate",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scan_id", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.String(length=50), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_sheet", sa.String(length=255), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("source_cells", sa.Text(), nullable=True),
        sa.Column(
            "evidence_json",
            sa.Text(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("candidate_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 100",
            name="ck_document_metadata_candidate_confidence",
        ),
        sa.CheckConstraint(
            "json_valid(evidence_json)",
            name="ck_document_metadata_candidate_evidence_json",
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"],
            ["document_metadata_scan.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scan_id",
            "candidate_fingerprint",
            name="uq_document_metadata_candidate_scan_fingerprint",
        ),
    )
    op.create_index(
        "ix_document_metadata_candidate_scan_id",
        "document_metadata_candidate",
        ["scan_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_metadata_candidate_scan_id",
        table_name="document_metadata_candidate",
    )
    op.drop_table("document_metadata_candidate")
    op.drop_index(
        "ix_document_metadata_scan_source_variant_id",
        table_name="document_metadata_scan",
    )
    op.drop_table("document_metadata_scan")
    op.drop_column("document_metadata_version", "evidence_json")
    op.drop_column("clean_decision", "reason_evidence_json")
