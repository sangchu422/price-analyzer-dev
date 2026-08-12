from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.time import utc_now
from app.db.types import NaiveUTCDateTime


class ParseRunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAILED = "FAILED"


class SourceParseRun(Base):
    __tablename__ = "source_parse_run"
    __table_args__ = (
        UniqueConstraint("source_variant_id", "parser_version", "code_fingerprint", name="uq_source_parse_run_provenance"),
        CheckConstraint("length(code_fingerprint) = 64", name="ck_source_parse_run_code_fingerprint"),
        Index("ix_source_parse_run_current", "source_variant_id", "status", "id"),
        {"info": {"evidence_immutable": True}},
    )
    __evidence_immutable__: ClassVar[bool] = True

    id: Mapped[int] = mapped_column(primary_key=True)
    source_variant_id: Mapped[int] = mapped_column(ForeignKey("source_variant.id", ondelete="RESTRICT"), index=True)
    parser_name: Mapped[str] = mapped_column(String(100))
    parser_version: Mapped[str] = mapped_column(String(100))
    code_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[ParseRunStatus] = mapped_column(String(32))
    row_count: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    error_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(NaiveUTCDateTime(), default=utc_now, server_default=text("CURRENT_TIMESTAMP"))
    finished_at: Mapped[datetime | None] = mapped_column(NaiveUTCDateTime())


class SourceParseOutput(Base):
    __tablename__ = "source_parse_output"
    __table_args__ = (
        UniqueConstraint("parse_run_id", "raw_item_id", name="uq_source_parse_output_run_raw"),
        UniqueConstraint("raw_item_id", name="uq_source_parse_output_raw"),
        {"info": {"evidence_immutable": True}},
    )
    __evidence_immutable__: ClassVar[bool] = True

    id: Mapped[int] = mapped_column(primary_key=True)
    parse_run_id: Mapped[int] = mapped_column(ForeignKey("source_parse_run.id", ondelete="RESTRICT"), index=True)
    raw_item_id: Mapped[int] = mapped_column(ForeignKey("raw_quote_item.id", ondelete="RESTRICT"), index=True)


class CleansingReassessmentRun(Base):
    __tablename__ = "cleansing_reassessment_run"
    __table_args__ = (
        UniqueConstraint("input_fingerprint", "rule_version", name="uq_cleansing_reassessment_provenance"),
        CheckConstraint("length(input_fingerprint) = 64", name="ck_cleansing_reassessment_fingerprint"),
        {"info": {"evidence_immutable": True}},
    )
    __evidence_immutable__: ClassVar[bool] = True

    id: Mapped[int] = mapped_column(primary_key=True)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32))
    counts_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
    report_path: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(NaiveUTCDateTime(), default=utc_now, server_default=text("CURRENT_TIMESTAMP"))


class CleansingReassessmentEntry(Base):
    __tablename__ = "cleansing_reassessment_entry"
    __table_args__ = (
        UniqueConstraint("run_id", "raw_item_id", name="uq_cleansing_reassessment_entry"),
        {"info": {"evidence_immutable": True}},
    )
    __evidence_immutable__: ClassVar[bool] = True

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("cleansing_reassessment_run.id", ondelete="RESTRICT"), index=True)
    raw_item_id: Mapped[int] = mapped_column(ForeignKey("raw_quote_item.id", ondelete="RESTRICT"), index=True)
    previous_decision_id: Mapped[int | None] = mapped_column(ForeignKey("clean_decision.id", ondelete="RESTRICT"))
    new_decision_id: Mapped[int] = mapped_column(ForeignKey("clean_decision.id", ondelete="RESTRICT"))
    disposition: Mapped[str] = mapped_column(String(64))
    evidence_json: Mapped[str] = mapped_column(Text, default="{}", server_default=text("'{}'"))
