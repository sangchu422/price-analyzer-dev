import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.immutability import ImmutableEvidenceError
from app.db.models import (
    CleanDecision,
    CleanStatus,
    RawQuoteItem,
    SourceDocument,
    SourceVariant,
)
from app.db.sqlite import configure_sqlite


BACKEND_PATH = Path(__file__).resolve().parents[1]


def _graph() -> tuple[
    SourceDocument,
    SourceVariant,
    RawQuoteItem,
    CleanDecision,
]:
    document = SourceDocument(logical_name="immutable-source")
    variant = SourceVariant(
        path="quotes/source.xlsx",
        sha256="b" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
    )
    document.variants.append(variant)
    raw_item = RawQuoteItem(
        source_variant=variant,
        source_sheet="Sheet1",
        source_row=3,
        item_name_raw=" ORIGINAL ",
        parser_name="xlsx-reader",
        parser_version="1.0",
    )
    decision = CleanDecision(
        raw_item=raw_item,
        status=CleanStatus.INCLUDED,
        reason_code="VALID",
        rule_version="clean-v1",
    )
    return document, variant, raw_item, decision


def _configured_memory_engine() -> Engine:
    return configure_sqlite(create_engine("sqlite:///:memory:"))


@pytest.mark.parametrize(
    ("entrypoint", "construction"),
    [
        (
            "from app.db.models import SourceDocument",
            "SourceDocument(logical_name='isolated')",
        ),
        (
            "from app.main import app; "
            "from app.db.models import SourceDocument",
            "SourceDocument(logical_name='isolated')",
        ),
        (
            "from app.db.session import SessionLocal; "
            "from app.db.models import SourceDocument",
            "SourceDocument(logical_name='isolated')",
        ),
        (
            "from app.documents import SourceDocument",
            "SourceDocument(logical_name='isolated')",
        ),
        (
            "from app.documents import SourceDocument; "
            "from app.quotes import RawQuoteItem",
            (
                "SourceDocument(logical_name='isolated'); "
                "RawQuoteItem(parser_name='reader', parser_version='1.0')"
            ),
        ),
        (
            "from app.quotes import RawQuoteItem",
            "RawQuoteItem(parser_name='reader', parser_version='1.0')",
        ),
        (
            "from app.cleansing import CleanDecision",
            (
                "CleanDecision(status='INCLUDED', reason_code='VALID', "
                "rule_version='clean-v1')"
            ),
        ),
    ],
)
def test_normal_entrypoints_configure_all_mappers_in_isolated_process(
    entrypoint: str,
    construction: str,
    tmp_path: Path,
) -> None:
    environment = os.environ.copy()
    environment["DATABASE_FILE"] = str(tmp_path / "entrypoint.sqlite3")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                f"{entrypoint}; "
                "from sqlalchemy.orm import configure_mappers; "
                f"configure_mappers(); {construction}"
            ),
        ],
        cwd=BACKEND_PATH,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_domain_import_alone_registers_session_immutability() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from sqlalchemy import create_engine; "
                "from sqlalchemy.orm import Session; "
                "from app.db.base import Base; "
                "from app.documents import SourceDocument, SourceVariant; "
                "from app.quotes import RawQuoteItem; "
                "document=SourceDocument(logical_name='isolated'); "
                "variant=SourceVariant(document=document, path='q.xlsx', "
                "sha256='d'*64, extension='.xlsx', "
                "security_state='UNLOCKED'); "
                "raw=RawQuoteItem(source_variant=variant, "
                "parser_name='reader', parser_version='1.0'); "
                "engine=create_engine('sqlite:///:memory:'); "
                "Base.metadata.create_all(engine); "
                "session=Session(engine); session.add(document); "
                "session.commit(); raw.item_name_raw='rewrite'; "
                "raised=False; "
                "\ntry:\n session.flush()\n"
                "except RuntimeError:\n raised=True\n"
                "assert raised"
            ),
        ],
        cwd=BACKEND_PATH,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_raw_item_resolves_exact_source_variant() -> None:
    engine = _configured_memory_engine()
    Base.metadata.create_all(engine)
    document, variant, _, _ = _graph()
    raw_item = RawQuoteItem(
        source_variant=variant,
        parser_name="xlsx-reader",
        parser_version="1.0",
    )

    with Session(engine) as session:
        session.add(raw_item)
        session.commit()
        raw_item_id = raw_item.id

    with Session(engine) as session:
        saved = session.get(RawQuoteItem, raw_item_id)
        assert saved is not None
        assert saved.source_variant.path == "quotes/source.xlsx"
        assert saved.source_variant.sha256 == "b" * 64

    assert RawQuoteItem.__table__.c.source_variant_id.nullable is False
    assert RawQuoteItem.__table__.c.parser_name.nullable is False
    assert RawQuoteItem.__table__.c.parser_version.nullable is False


def test_raw_item_document_provenance_cannot_contradict_its_variant() -> None:
    assert "document_id" not in RawQuoteItem.__table__.c
    assert not hasattr(RawQuoteItem, "document")

    engine = _configured_memory_engine()
    Base.metadata.create_all(engine)
    first_document = SourceDocument(logical_name="first")
    second_document = SourceDocument(logical_name="second")
    variant = SourceVariant(
        document=second_document,
        path="quotes/second.xlsx",
        sha256="c" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
    )
    raw_item = RawQuoteItem(
        source_variant=variant,
        parser_name="reader",
        parser_version="1.0",
    )

    with Session(engine) as session:
        session.add_all([first_document, raw_item])
        session.commit()
        raw_item_id = raw_item.id

    with Session(engine) as session:
        saved = session.get(RawQuoteItem, raw_item_id)
        assert saved.source_variant.document.logical_name == "second"


@pytest.mark.parametrize(
    ("model", "attribute", "new_value"),
    [
        (SourceDocument, "logical_name", "changed"),
        (SourceVariant, "path", "changed.xlsx"),
        (RawQuoteItem, "item_name_raw", "changed"),
        (CleanDecision, "reason_detail", "changed"),
    ],
)
def test_persistent_evidence_rows_reject_updates(
    model: type,
    attribute: str,
    new_value: str,
) -> None:
    engine = _configured_memory_engine()
    Base.metadata.create_all(engine)
    document, _, _, _ = _graph()

    with Session(engine) as session:
        session.add(document)
        session.commit()
        instance = session.scalar(select(model))
        setattr(instance, attribute, new_value)

        with pytest.raises(ImmutableEvidenceError):
            session.flush()


@pytest.mark.parametrize(
    "model",
    [SourceDocument, SourceVariant, RawQuoteItem, CleanDecision],
)
def test_persistent_evidence_rows_reject_deletes(model: type) -> None:
    engine = _configured_memory_engine()
    Base.metadata.create_all(engine)
    document, _, _, _ = _graph()

    with Session(engine) as session:
        session.add(document)
        session.commit()
        instance = session.scalar(select(model))
        session.delete(instance)

        with pytest.raises(ImmutableEvidenceError):
            session.flush()


def test_bulk_orm_update_rejects_evidence_rewrite() -> None:
    engine = _configured_memory_engine()
    Base.metadata.create_all(engine)
    document, _, _, _ = _graph()

    with Session(engine) as session:
        session.add(document)
        session.commit()
        with pytest.raises(ImmutableEvidenceError):
            session.execute(
                update(RawQuoteItem).values(item_name_raw="rewritten")
            )


def test_bulk_orm_delete_rejects_evidence_removal() -> None:
    engine = _configured_memory_engine()
    Base.metadata.create_all(engine)
    document, _, _, _ = _graph()

    with Session(engine) as session:
        session.add(document)
        session.commit()
        with pytest.raises(ImmutableEvidenceError):
            session.execute(delete(CleanDecision))


@pytest.mark.parametrize(
    "statement",
    [
        update(RawQuoteItem.__table__).values(item_name_raw="rewritten"),
        delete(CleanDecision.__table__),
    ],
)
def test_session_rejects_table_level_bulk_evidence_dml(statement: object) -> None:
    engine = _configured_memory_engine()
    Base.metadata.create_all(engine)
    document, _, _, _ = _graph()

    with Session(engine) as session:
        session.add(document)
        session.commit()
        with pytest.raises(ImmutableEvidenceError):
            session.execute(statement)


def test_invalid_clean_status_is_rejected_by_orm() -> None:
    engine = _configured_memory_engine()
    Base.metadata.create_all(engine)
    document, _, raw_item, _ = _graph()
    raw_item.decisions.clear()
    raw_item.decisions.append(
        CleanDecision(
            status="NOT_A_STATUS",
            reason_code="INVALID",
            rule_version="clean-v1",
        )
    )

    with Session(engine) as session:
        session.add(document)
        with pytest.raises(StatementError):
            session.commit()


def test_invalid_clean_status_is_rejected_by_sqlite_check() -> None:
    engine = _configured_memory_engine()
    Base.metadata.create_all(engine)
    document, _, raw_item, _ = _graph()

    with Session(engine) as session:
        session.add(document)
        session.commit()
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    """
                    INSERT INTO clean_decision
                        (raw_item_id, status, reason_code, rule_version)
                    VALUES
                        (:raw_item_id, 'NOT_A_STATUS', 'INVALID', 'clean-v1')
                    """
                ),
                {"raw_item_id": raw_item.id},
            )


def test_decimal_values_round_trip_exactly_without_float_storage() -> None:
    engine = _configured_memory_engine()
    Base.metadata.create_all(engine)
    document, _, _, decision = _graph()
    boundary = Decimal("9223372036854.775807")
    decision.quantity = "2.500"
    decision.unit_price = boundary
    decision.amount = 999

    with Session(engine) as session:
        session.add(document)
        session.commit()
        decision_id = decision.id

    with Session(engine) as session:
        saved = session.get(CleanDecision, decision_id)
        assert saved is not None
        assert saved.quantity == Decimal("2.500")
        assert saved.unit_price == boundary
        assert saved.amount == Decimal("999")
        storage_type = session.scalar(
            text(
                "SELECT typeof(unit_price) FROM clean_decision WHERE id = :id"
            ),
            {"id": decision_id},
        )
        assert storage_type == "integer"


def test_decimal_queries_use_numeric_ordering_and_comparison() -> None:
    engine = _configured_memory_engine()
    Base.metadata.create_all(engine)
    document, _, raw_item, decision = _graph()
    decision.amount = Decimal("10")
    raw_item.decisions.append(
        CleanDecision(
            status=CleanStatus.INCLUDED,
            reason_code="VALID",
            amount=Decimal("2.5"),
            rule_version="clean-v1",
        )
    )

    with Session(engine) as session:
        session.add(document)
        session.commit()
        ordered = session.scalars(
            select(CleanDecision.amount).order_by(CleanDecision.amount)
        ).all()
        greater_than_nine = session.scalars(
            select(CleanDecision.amount).where(CleanDecision.amount > 9)
        ).all()

    assert ordered == [Decimal("2.5"), Decimal("10")]
    assert greater_than_nine == [Decimal("10")]


@pytest.mark.parametrize(
    "invalid_value",
    [
        "not-a-decimal",
        1.5,
        True,
        Decimal("NaN"),
        Decimal("9223372036854.775808"),
        Decimal("-9223372036854.775808"),
        Decimal("0.0000001"),
        Decimal("1.0000000"),
    ],
)
def test_invalid_decimal_value_is_rejected(invalid_value: object) -> None:
    engine = _configured_memory_engine()
    Base.metadata.create_all(engine)
    document, _, _, decision = _graph()
    decision.amount = invalid_value

    with Session(engine) as session:
        session.add(document)
        with pytest.raises(StatementError):
            session.commit()


def test_sqlite_foreign_keys_reject_orphans_and_parent_deletion() -> None:
    engine = _configured_memory_engine()
    Base.metadata.create_all(engine)

    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    """
                    INSERT INTO raw_quote_item
                        (
                            source_variant_id, parser_name, parser_version
                        )
                    VALUES (999, 'reader', '1.0')
                    """
                )
            )

    document, _, _, _ = _graph()
    with Session(engine) as session:
        session.add(document)
        session.commit()
        document_id = document.id

    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                text("DELETE FROM source_document WHERE id = :id"),
                {"id": document_id},
            )


def test_timestamps_round_trip_as_naive_utc() -> None:
    engine = _configured_memory_engine()
    Base.metadata.create_all(engine)
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    document, variant, _, decision = _graph()

    with Session(engine) as session:
        session.add(document)
        session.commit()
        document_id = document.id

    after = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(engine) as session:
        saved = session.get(SourceDocument, document_id)
        timestamps = [
            saved.created_at,
            saved.variants[0].registered_at,
            saved.raw_items[0].decisions[0].decided_at,
        ]

    assert SourceDocument.__table__.c.created_at.type.timezone is False
    assert SourceVariant.__table__.c.registered_at.type.timezone is False
    assert CleanDecision.__table__.c.decided_at.type.timezone is False
    assert all(value.tzinfo is None for value in timestamps)
    assert all(before <= value <= after for value in timestamps)


def test_aware_timestamp_input_is_normalized_to_naive_utc() -> None:
    engine = _configured_memory_engine()
    Base.metadata.create_all(engine)
    local_time = datetime(
        2026,
        7,
        25,
        9,
        0,
        tzinfo=timezone(timedelta(hours=9)),
    )
    document, _, _, _ = _graph()
    document.created_at = local_time

    with Session(engine) as session:
        session.add(document)
        session.commit()
        document_id = document.id

    with Session(engine) as session:
        saved = session.get(SourceDocument, document_id)
        assert saved.created_at == datetime(2026, 7, 25, 0, 0)
        assert saved.created_at.tzinfo is None
