from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

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


class StandardOperationalStatus(StrEnum):
    """Whether a standard item's captured price is usable right now."""

    ACTIVE = "ACTIVE"
    REBUILD_REQUIRED = "REBUILD_REQUIRED"
    NO_ELIGIBLE_EVIDENCE = "NO_ELIGIBLE_EVIDENCE"


def _legacy_build_fingerprint(label: str) -> str:
    """Stable migration fallback for builds created before provenance hashes."""

    return hashlib.sha256(label.encode("utf-8")).hexdigest()


LEGACY_BUILD_CALCULATION_FINGERPRINT = _legacy_build_fingerprint(
    "STANDARD_DATABASE_BUILD_LEGACY_CALCULATION"
)
LEGACY_BUILD_CODE_FINGERPRINT = _legacy_build_fingerprint(
    "STANDARD_DATABASE_BUILD_LEGACY_CODE"
)


class _ImmutableStandardDatabaseRow:
    __evidence_immutable__: ClassVar[bool] = True


class QuoteDocumentRole(_ImmutableStandardDatabaseRow, Base):
    __tablename__ = "quote_document_role"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "document_id",
            name="uq_quote_document_role_id_document",
        ),
        UniqueConstraint(
            "supersedes_role_id",
            name="uq_quote_document_role_supersedes",
        ),
        CheckConstraint(
            "supersedes_role_id IS NULL OR supersedes_role_id <> id",
            name="ck_quote_document_role_not_self_superseding",
        ),
        ForeignKeyConstraint(
            ["supersedes_role_id", "document_id"],
            ["quote_document_role.id", "quote_document_role.document_id"],
            name="fk_quote_document_role_supersedes_same_document",
            ondelete="RESTRICT",
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
    supersedes_role_id: Mapped[int | None] = mapped_column()
    decided_by: Mapped[str] = mapped_column(String(100))
    reason_detail: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

class StandardDatabaseBuildRun(_ImmutableStandardDatabaseRow, Base):
    __tablename__ = "standard_database_build_run"
    __table_args__ = (
        CheckConstraint(
            "length(input_fingerprint) = 64",
            name="ck_standard_database_build_input_fingerprint",
        ),
        CheckConstraint(
            "length(calculation_fingerprint) = 64",
            name="ck_standard_database_build_calculation_fingerprint",
        ),
        CheckConstraint(
            "length(code_fingerprint) = 64",
            name="ck_standard_database_build_code_fingerprint",
        ),
        Index(
            "uq_standard_database_build_success_provenance",
            "input_fingerprint",
            "calculation_fingerprint",
            "code_fingerprint",
            unique=True,
            sqlite_where=text("status = 'SUCCEEDED'"),
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    calculation_fingerprint: Mapped[str] = mapped_column(
        String(64),
        default=LEGACY_BUILD_CALCULATION_FINGERPRINT,
        server_default=text(
            f"'{LEGACY_BUILD_CALCULATION_FINGERPRINT}'"
        ),
    )
    code_fingerprint: Mapped[str] = mapped_column(
        String(64),
        default=LEGACY_BUILD_CODE_FINGERPRINT,
        server_default=text(f"'{LEGACY_BUILD_CODE_FINGERPRINT}'"),
    )
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

    def __allow_evidence_update__(self) -> bool:
        """Allow exactly one RUNNING-to-terminal lifecycle transition."""

        state = inspect(self)
        changed = {
            attribute.key
            for attribute in state.attrs
            if attribute.history.has_changes()
        }
        allowed = {
            "status",
            "counts_json",
            "report_path",
            "error_detail",
            "finished_at",
        }
        status_history = state.attrs.status.history
        finished_history = state.attrs.finished_at.history
        if not {"status", "finished_at"} <= changed or not changed <= allowed:
            return False
        if not status_history.deleted or not status_history.added:
            return False
        if status_history.deleted[0] is not StandardBuildStatus.RUNNING:
            return False
        if status_history.added[0] not in {
            StandardBuildStatus.SUCCEEDED,
            StandardBuildStatus.FAILED,
        }:
            return False
        return bool(finished_history.added and self.finished_at is not None)


class StandardDatabaseBuildProjection(_ImmutableStandardDatabaseRow, Base):
    """Immutable item-to-price snapshot emitted by a successful build."""

    __tablename__ = "standard_database_build_projection"
    __table_args__ = (
        UniqueConstraint(
            "build_run_id",
            "standard_item_id",
            name="uq_standard_database_build_projection_item",
        ),
        CheckConstraint(
            "(operational_status = 'NO_ELIGIBLE_EVIDENCE' "
            "AND standard_price_version_id IS NULL) OR "
            "(operational_status IN ('ACTIVE', 'REBUILD_REQUIRED') "
            "AND standard_price_version_id IS NOT NULL)",
            name="ck_standard_database_projection_price_status",
        ),
        ForeignKeyConstraint(
            ["standard_item_version_id", "standard_item_id"],
            [
                "standard_item_version.id",
                "standard_item_version.standard_item_id",
            ],
            name="fk_standard_database_projection_item_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["standard_price_version_id", "standard_item_id"],
            [
                "standard_price_version.id",
                "standard_price_version.standard_item_id",
            ],
            name="fk_standard_database_projection_price_version",
            ondelete="RESTRICT",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    build_run_id: Mapped[int] = mapped_column(
        ForeignKey("standard_database_build_run.id", ondelete="RESTRICT"),
        index=True,
    )
    standard_item_id: Mapped[int] = mapped_column(
        ForeignKey("standard_item.id", ondelete="RESTRICT"),
        index=True,
    )
    standard_item_version_id: Mapped[int | None] = mapped_column(index=True)
    standard_price_version_id: Mapped[int | None] = mapped_column(index=True)
    operational_status: Mapped[StandardOperationalStatus] = mapped_column(
        Enum(
            StandardOperationalStatus,
            name="standard_operational_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        )
    )
    projected_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )
