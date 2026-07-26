from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.immutability import ImmutableEvidenceError
from app.db.sqlite import configure_sqlite
from app.documents.models import SourceDocument
from app.standard_database.models import (
    QuoteDocumentPurpose,
    QuoteDocumentRole,
    StandardBuildStatus,
    StandardDatabaseBuildRun,
)


def _engine():
    return configure_sqlite(create_engine("sqlite:///:memory:"))


def _role(document_id: int, *, supersedes_role_id: int | None = None):
    return QuoteDocumentRole(
        document_id=document_id,
        purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
        supersedes_role_id=supersedes_role_id,
        decided_by="buyer",
        reason_detail="classification review",
    )


def _build_run() -> StandardDatabaseBuildRun:
    return StandardDatabaseBuildRun(
        input_fingerprint="a" * 64,
        rule_version="rules-v1",
    )


def test_quote_document_role_supersession_requires_same_document() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        first_document = SourceDocument(logical_name="quotes/first.xlsx")
        second_document = SourceDocument(logical_name="quotes/second.xlsx")
        session.add_all([first_document, second_document])
        session.flush()
        first_role = _role(first_document.id)
        second_role = _role(second_document.id)
        session.add_all([first_role, second_role])
        session.commit()
        first_document_id = first_document.id
        first_role_id = first_role.id
        second_role_id = second_role.id

    with Session(engine) as session:
        session.add(_role(first_document_id, supersedes_role_id=first_role_id))
        session.commit()

    with Session(engine) as session:
        session.add(_role(first_document_id, supersedes_role_id=second_role_id))
        with pytest.raises(IntegrityError):
            session.commit()


def test_quote_document_role_rejects_invalid_enum_updates_and_deletes() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        document = SourceDocument(logical_name="quotes/sample.xlsx")
        session.add(document)
        session.flush()
        role = _role(document.id)
        session.add(role)
        session.commit()
        role_id = role.id

    with Session(engine) as session:
        invalid = _role(1)
        invalid.purpose = "INVALID"  # type: ignore[assignment]
        session.add(invalid)
        with pytest.raises(StatementError):
            session.flush()

    with Session(engine) as session:
        role = session.get(QuoteDocumentRole, role_id)
        assert role is not None
        role.reason_detail = "rewritten"
        with pytest.raises(ImmutableEvidenceError):
            session.flush()

    with Session(engine) as session:
        role = session.get(QuoteDocumentRole, role_id)
        assert role is not None
        session.delete(role)
        with pytest.raises(ImmutableEvidenceError):
            session.flush()


@pytest.mark.parametrize(
    "terminal_status",
    [StandardBuildStatus.SUCCEEDED, StandardBuildStatus.FAILED],
)
def test_build_run_can_transition_once_from_running(
    terminal_status: StandardBuildStatus,
) -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        run = _build_run()
        session.add(run)
        session.commit()
        run_id = run.id

    with Session(engine) as session:
        run = session.get(StandardDatabaseBuildRun, run_id)
        assert run is not None
        run.status = terminal_status
        run.counts_json = '{"processed": 1}'
        run.report_path = "reports/build.json"
        run.finished_at = datetime(2026, 7, 26, 12, 0)
        session.commit()

    with Session(engine) as session:
        run = session.get(StandardDatabaseBuildRun, run_id)
        assert run is not None
        run.error_detail = "terminal rewrite"
        with pytest.raises(ImmutableEvidenceError):
            session.flush()
        session.rollback()
        session.delete(run)
        with pytest.raises(ImmutableEvidenceError):
            session.flush()


@pytest.mark.parametrize(
    ("attribute", "replacement"),
    [
        ("rule_version", "rewritten-rules"),
        ("input_fingerprint", "b" * 64),
    ],
)
def test_build_run_rejects_unrelated_mutation_during_transition(
    attribute: str,
    replacement: str,
) -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        run = _build_run()
        session.add(run)
        session.commit()
        run_id = run.id

    with Session(engine) as session:
        run = session.get(StandardDatabaseBuildRun, run_id)
        assert run is not None
        run.status = StandardBuildStatus.SUCCEEDED
        setattr(run, attribute, replacement)
        run.finished_at = datetime(2026, 7, 26, 12, 0)
        with pytest.raises(ImmutableEvidenceError):
            session.flush()
