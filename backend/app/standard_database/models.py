from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.time import utc_now
from app.db.types import NaiveUTCDateTime


class QuoteDocumentPurpose(StrEnum):
    HISTORICAL_REFERENCE = "HISTORICAL_REFERENCE"
    INCOMING_BID = "INCOMING_BID"


class StandardBuildStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class _ImmutableStandardDatabaseRow:
    __evidence_immutable__: ClassVar[bool] = True


class QuoteDocumentRole(_ImmutableStandardDatabaseRow, Base):
    __tablename__ = "quote_document_role"
    __table_args__ = (
        CheckConstraint(
            "supersedes_role_id IS NULL OR supersedes_role_id <> id",
            name="ck_quote_document_role_not_self_superseding",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("source_document.id", ondelete="RESTRICT"),
        index=True,
    )
    purpose: Mapped[QuoteDocumentPurpose] = mapped_column(
        Enum(
            QuoteDocumentPurpose,
            name="quote_document_purpose",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        )
    )
    supersedes_role_id: Mapped[int | None] = mapped_column(
        ForeignKey("quote_document_role.id", ondelete="RESTRICT"),
        unique=True,
    )
    decided_by: Mapped[str] = mapped_column(String(100))
    reason_detail: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    supersedes: Mapped[QuoteDocumentRole | None] = relationship(
        "QuoteDocumentRole",
        remote_side="QuoteDocumentRole.id",
        foreign_keys=[supersedes_role_id],
        primaryjoin="QuoteDocumentRole.supersedes_role_id "
        "== QuoteDocumentRole.id",
    )


class StandardDatabaseBuildRun(_ImmutableStandardDatabaseRow, Base):
    __tablename__ = "standard_database_build_run"
    __table_args__ = (
        CheckConstraint(
            "length(input_fingerprint) = 64",
            name="ck_standard_database_build_input_fingerprint",
        ),
        Index(
            "uq_standard_database_build_success_input_rule",
            "input_fingerprint",
            "rule_version",
            unique=True,
            sqlite_where=text("status = 'SUCCEEDED'"),
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(String(100))
    status: Mapped[StandardBuildStatus] = mapped_column(
        Enum(
            StandardBuildStatus,
            name="standard_build_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=StandardBuildStatus.RUNNING,
        server_default=text("'RUNNING'"),
    )
    report_path: Mapped[str | None] = mapped_column(String(1024))
    counts_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
        server_default=text("'{}'"),
    )
    error_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        NaiveUTCDateTime(),
    )
