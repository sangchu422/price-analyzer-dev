from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, inspect, select, text, update
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.catalog.models import (
    DocumentMetadataVersion,
    ItemMembershipDecision,
    MembershipStatus,
    StandardItem,
    StandardItemVersion,
    StandardPriceVersion,
)
from app.db.base import Base
from app.db.immutability import ImmutableEvidenceError
from app.db.sqlite import configure_sqlite
from app.db.types import EXACT_DECIMAL_MAX
from app.documents.models import SourceDocument, SourceVariant
from app.quotes.models import RawQuoteItem


def _engine():
    return configure_sqlite(create_engine("sqlite:///:memory:"))


def _source_graph() -> tuple[SourceDocument, RawQuoteItem]:
    document = SourceDocument(logical_name="quotes/sample.xlsx")
    variant = SourceVariant(
        document=document,
        path="quotes/sample.xlsx",
        sha256="a" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    raw_item = RawQuoteItem(
        source_variant=variant,
        source_sheet="Sheet1",
        source_row=2,
        item_name_raw="BEARING",
        spec_raw="6204 ZZ",
        unit_raw="EA",
        unit_price_raw="120",
        parser_name="xlsx",
        parser_version="1",
    )
    return document, raw_item


def _catalog_graph() -> tuple[
    SourceDocument,
    RawQuoteItem,
    StandardItem,
    StandardItemVersion,
    DocumentMetadataVersion,
    ItemMembershipDecision,
    StandardPriceVersion,
]:
    document, raw_item = _source_graph()
    item = StandardItem()
    item_version = StandardItemVersion(
        standard_item=item,
        version_number=1,
        canonical_name="BEARING",
        canonical_spec="6204 ZZ",
        canonical_unit="EA",
        aliases_json='["BALL BEARING"]',
        created_by="buyer-1",
    )
    metadata = DocumentMetadataVersion(
        source_document=document,
        version_number=1,
        supplier_name="SUPPLIER A",
        quote_date=date(2026, 7, 20),
        project_name="LINE A",
        decided_by="buyer-1",
    )
    membership = ItemMembershipDecision(
        raw_item=raw_item,
        standard_item=item,
        status=MembershipStatus.MATCHED,
        candidate_score=Decimal("0.920000"),
        method="MANUAL",
        evidence_json='{"matched_tokens":["6204"]}',
        decided_by="buyer-1",
    )
    price = StandardPriceVersion(
        standard_item=item,
        version_number=1,
        observation_count=1,
        supplier_count=1,
        latest_quote_date=date(2026, 7, 20),
        minimum_price=Decimal("120"),
        median_price=Decimal("120"),
        average_price=Decimal("120"),
        maximum_price=Decimal("120"),
        observation_decision_ids_json="[1]",
        calculation_version="STANDARD_PRICE_V1",
        approved_by="buyer-1",
    )
    return (
        document,
        raw_item,
        item,
        item_version,
        metadata,
        membership,
        price,
    )


def test_catalog_graph_persists_exact_values_and_utc_timestamps() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    graph = _catalog_graph()
    aware_kst = datetime(
        2026,
        7,
        25,
        12,
        30,
        tzinfo=timezone(timedelta(hours=9)),
    )
    graph[3].created_at = aware_kst
    graph[5].decided_at = aware_kst
    graph[6].approved_at = aware_kst

    with Session(engine) as session:
        session.add_all([graph[0], graph[2], graph[4]])
        session.commit()
        price_id = graph[6].id
        membership_id = graph[5].id
        version_id = graph[3].id

    with Session(engine) as session:
        price = session.get(StandardPriceVersion, price_id)
        membership = session.get(ItemMembershipDecision, membership_id)
        version = session.get(StandardItemVersion, version_id)
        assert price is not None
        assert membership is not None
        assert version is not None
        assert price.median_price == Decimal("120.000000")
        assert membership.candidate_score == Decimal("0.920000")
        assert price.approved_at == datetime(2026, 7, 25, 3, 30)
        assert membership.decided_at == datetime(2026, 7, 25, 3, 30)
        assert version.created_at == datetime(2026, 7, 25, 3, 30)


@pytest.mark.parametrize(
    ("model", "attribute", "replacement"),
    [
        (StandardItem, "created_at", datetime(2020, 1, 1)),
        (StandardItemVersion, "canonical_name", "MOTOR"),
        (DocumentMetadataVersion, "supplier_name", "OTHER"),
        (ItemMembershipDecision, "method", "REWRITE"),
        (StandardPriceVersion, "calculation_version", "REWRITE"),
    ],
)
def test_catalog_rows_reject_orm_updates(
    model: type,
    attribute: str,
    replacement: object,
) -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    graph = _catalog_graph()
    with Session(engine) as session:
        session.add_all([graph[0], graph[2], graph[4]])
        session.commit()
        instance = session.scalar(select(model))
        assert instance is not None
        setattr(instance, attribute, replacement)
        with pytest.raises(ImmutableEvidenceError):
            session.flush()


@pytest.mark.parametrize(
    "model",
    [
        StandardItem,
        StandardItemVersion,
        DocumentMetadataVersion,
        ItemMembershipDecision,
        StandardPriceVersion,
    ],
)
def test_catalog_rows_reject_orm_deletes(model: type) -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    graph = _catalog_graph()
    with Session(engine) as session:
        session.add_all([graph[0], graph[2], graph[4]])
        session.commit()
        instance = session.scalar(select(model))
        assert instance is not None
        session.delete(instance)
        with pytest.raises(ImmutableEvidenceError):
            session.flush()


@pytest.mark.parametrize(
    "statement",
    [
        update(StandardItemVersion).values(canonical_name="MOTOR"),
        delete(DocumentMetadataVersion),
        update(ItemMembershipDecision.__table__).values(method="REWRITE"),
        delete(StandardPriceVersion.__table__),
    ],
)
def test_catalog_rows_reject_bulk_dml(statement: object) -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    graph = _catalog_graph()
    with Session(engine) as session:
        session.add_all([graph[0], graph[2], graph[4]])
        session.commit()
        with pytest.raises(ImmutableEvidenceError):
            session.execute(statement)


def test_membership_supersedes_is_single_use_compare_and_swap() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    document, raw_item, item, _, _, first, _ = _catalog_graph()
    second = ItemMembershipDecision(
        raw_item=raw_item,
        standard_item=None,
        status=MembershipStatus.REJECTED,
        method="MANUAL",
        evidence_json="{}",
        supersedes=first,
        decided_by="buyer-2",
    )
    third = ItemMembershipDecision(
        raw_item=raw_item,
        standard_item=None,
        status=MembershipStatus.REJECTED,
        method="MANUAL",
        evidence_json="{}",
        supersedes=first,
        decided_by="buyer-3",
    )
    with Session(engine) as session:
        session.add_all([document, item, second])
        session.commit()
        session.add(third)
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize(
    "factory",
    [
        lambda document, raw_item, item: StandardItemVersion(
            standard_item=item,
            version_number=0,
            canonical_name="BEARING",
            aliases_json="[]",
            created_by="buyer",
        ),
        lambda document, raw_item, item: DocumentMetadataVersion(
            source_document=document,
            version_number=0,
            decided_by="buyer",
        ),
        lambda document, raw_item, item: StandardPriceVersion(
            standard_item=item,
            version_number=0,
            observation_count=1,
            supplier_count=0,
            minimum_price=Decimal("1"),
            median_price=Decimal("1"),
            average_price=Decimal("1"),
            maximum_price=Decimal("1"),
            observation_decision_ids_json="[1]",
            calculation_version="v1",
            approved_by="buyer",
        ),
    ],
)
def test_version_numbers_must_be_positive(factory) -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    document, raw_item = _source_graph()
    item = StandardItem()
    with Session(engine) as session:
        session.add_all([document, item, factory(document, raw_item, item)])
        with pytest.raises(IntegrityError):
            session.commit()


def test_history_version_numbers_are_unique_per_parent() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    item = StandardItem()
    with Session(engine) as session:
        session.add_all(
            [
                StandardItemVersion(
                    standard_item=item,
                    version_number=1,
                    canonical_name="BEARING",
                    aliases_json="[]",
                    created_by="buyer-1",
                ),
                StandardItemVersion(
                    standard_item=item,
                    version_number=1,
                    canonical_name="BEARING 2",
                    aliases_json="[]",
                    created_by="buyer-2",
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize(
    "invalid_json_row",
    [
        lambda item: StandardItemVersion(
            standard_item=item,
            version_number=1,
            canonical_name="BEARING",
            aliases_json="{not-an-array}",
            created_by="buyer",
        ),
        lambda item: ItemMembershipDecision(
            standard_item=item,
            raw_item_id=1,
            status=MembershipStatus.MATCHED,
            method="MANUAL",
            evidence_json="{broken",
            decided_by="buyer",
        ),
        lambda item: StandardPriceVersion(
            standard_item=item,
            version_number=1,
            observation_count=1,
            supplier_count=0,
            minimum_price=Decimal("1"),
            median_price=Decimal("1"),
            average_price=Decimal("1"),
            maximum_price=Decimal("1"),
            observation_decision_ids_json="{}",
            calculation_version="v1",
            approved_by="buyer",
        ),
    ],
)
def test_json_payloads_are_valid_and_array_fields_are_arrays(
    invalid_json_row,
) -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    document, _ = _source_graph()
    item = StandardItem()
    with Session(engine) as session:
        session.add_all([document, item])
        session.flush()
        session.add(invalid_json_row(item))
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize(
    "row",
    [
        lambda raw, item: ItemMembershipDecision(
            raw_item=raw,
            standard_item=None,
            status=MembershipStatus.MATCHED,
            method="MANUAL",
            evidence_json="{}",
            decided_by="buyer",
        ),
        lambda raw, item: ItemMembershipDecision(
            raw_item=raw,
            standard_item=item,
            status=MembershipStatus.REJECTED,
            method="MANUAL",
            evidence_json="{}",
            decided_by="buyer",
        ),
        lambda raw, item: ItemMembershipDecision(
            raw_item=raw,
            standard_item=item,
            status=MembershipStatus.MATCHED,
            candidate_score=Decimal("1.000001"),
            method="MANUAL",
            evidence_json="{}",
            decided_by="buyer",
        ),
    ],
)
def test_membership_status_target_and_score_constraints(row) -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    document, raw_item = _source_graph()
    item = StandardItem()
    with Session(engine) as session:
        session.add_all([document, item, row(raw_item, item)])
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize(
    "values",
    [
        {"observation_count": 0, "supplier_count": 0},
        {"observation_count": 1, "supplier_count": -1},
        {"observation_count": 1, "supplier_count": 2},
    ],
)
def test_price_counts_are_consistent(values: dict[str, int]) -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    item = StandardItem()
    price = StandardPriceVersion(
        standard_item=item,
        version_number=1,
        **values,
        minimum_price=Decimal("1"),
        median_price=Decimal("1"),
        average_price=Decimal("1"),
        maximum_price=Decimal("1"),
        observation_decision_ids_json="[1]",
        calculation_version="v1",
        approved_by="buyer",
    )
    with Session(engine) as session:
        session.add(price)
        with pytest.raises(IntegrityError):
            session.commit()


def test_price_observation_count_matches_evidence_ids() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    price = StandardPriceVersion(
        standard_item=StandardItem(),
        version_number=1,
        observation_count=2,
        supplier_count=1,
        minimum_price=Decimal("1"),
        median_price=Decimal("1"),
        average_price=Decimal("1"),
        maximum_price=Decimal("1"),
        observation_decision_ids_json="[1]",
        calculation_version="v1",
        approved_by="buyer",
    )
    with Session(engine) as session:
        session.add(price)
        with pytest.raises(IntegrityError):
            session.commit()


def test_exact_decimal_rejects_out_of_bounds_and_excess_precision() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    document, raw_item = _source_graph()
    item = StandardItem()
    with Session(engine) as session:
        session.add_all(
            [
                document,
                item,
                ItemMembershipDecision(
                    raw_item=raw_item,
                    standard_item=item,
                    status=MembershipStatus.MATCHED,
                    candidate_score=Decimal("0.1234567"),
                    method="MANUAL",
                    evidence_json="{}",
                    decided_by="buyer",
                ),
            ]
        )
        with pytest.raises(StatementError):
            session.commit()
    with Session(engine) as session:
        too_large = StandardPriceVersion(
            standard_item=StandardItem(),
            version_number=1,
            observation_count=1,
            supplier_count=0,
            minimum_price=EXACT_DECIMAL_MAX + Decimal("0.000001"),
            median_price=Decimal("1"),
            average_price=Decimal("1"),
            maximum_price=Decimal("1"),
            observation_decision_ids_json="[1]",
            calculation_version="v1",
            approved_by="buyer",
        )
        session.add(too_large)
        with pytest.raises(StatementError):
            session.commit()


def test_database_checks_membership_enum_and_foreign_key_restrict() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    graph = _catalog_graph()
    with Session(engine) as session:
        session.add_all([graph[0], graph[2], graph[4]])
        session.commit()
        raw_item_id = graph[1].id
        standard_item_id = graph[2].id
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    """
                    INSERT INTO item_membership_decision (
                        raw_item_id, status, method, evidence_json, decided_by
                    ) VALUES (:raw_item_id, 'AUTO', 'MANUAL', '{}', 'buyer')
                    """
                ),
                {"raw_item_id": raw_item_id},
            )
        with pytest.raises(IntegrityError):
            connection.execute(
                text("DELETE FROM standard_item WHERE id = :id"),
                {"id": standard_item_id},
            )


def _run_alembic(
    backend_path: Path,
    database_path: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_FILE"] = str(database_path)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(backend_path / "alembic.ini"),
            *arguments,
        ],
        cwd=backend_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _seed_0003_source(database_path: Path) -> None:
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO source_document (logical_name) VALUES ('seed.xlsx')"
        )
        connection.exec_driver_sql(
            """
            INSERT INTO source_variant (
                document_id, path, sha256, extension, security_state,
                selected_for_parsing_at_ingest
            ) VALUES (1, 'seed.xlsx', ?, '.xlsx', 'UNLOCKED', 1)
            """,
            ("f" * 64,),
        )
        connection.exec_driver_sql(
            """
            INSERT INTO raw_quote_item (
                source_variant_id, item_name_raw, parser_name, parser_version
            ) VALUES (1, 'BEARING', 'xlsx', '1')
            """
        )


def test_0004_migration_upgrade_check_and_populated_downgrade(
    tmp_path: Path,
) -> None:
    backend_path = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "catalog.sqlite3"
    result = _run_alembic(backend_path, database_path, "upgrade", "0003")
    assert result.returncode == 0, result.stdout + result.stderr
    _seed_0003_source(database_path)

    upgrade = _run_alembic(backend_path, database_path, "upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    assert {
        "standard_item",
        "standard_item_version",
        "document_metadata_version",
        "item_membership_decision",
        "standard_price_version",
    } <= set(inspector.get_table_names())
    expected_restrict_fks = {
        "standard_item_version": {"standard_item"},
        "document_metadata_version": {"source_document"},
        "item_membership_decision": {
            "raw_quote_item",
            "standard_item",
            "item_membership_decision",
        },
        "standard_price_version": {"standard_item"},
    }
    for table_name, referred_tables in expected_restrict_fks.items():
        foreign_keys = inspector.get_foreign_keys(table_name)
        assert {foreign_key["referred_table"] for foreign_key in foreign_keys} == (
            referred_tables
        )
        assert all(
            foreign_key["options"].get("ondelete") == "RESTRICT"
            for foreign_key in foreign_keys
        )
    membership_checks = inspector.get_check_constraints(
        "item_membership_decision"
    )
    assert any(
        check["name"] == "membership_status"
        and "MATCHED" in check["sqltext"]
        and "REJECTED" in check["sqltext"]
        for check in membership_checks
    )

    check = _run_alembic(backend_path, database_path, "check")
    assert check.returncode == 0, check.stdout + check.stderr

    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO standard_item DEFAULT VALUES")
        connection.exec_driver_sql(
            """
            INSERT INTO standard_item_version (
                standard_item_id, version_number, canonical_name,
                aliases_json, created_by
            ) VALUES (1, 1, 'BEARING', '[]', 'buyer')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO document_metadata_version (
                source_document_id, version_number, supplier_name, decided_by
            ) VALUES (1, 1, 'SUPPLIER', 'buyer')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO item_membership_decision (
                raw_item_id, standard_item_id, status, candidate_score,
                method, evidence_json, decided_by
            ) VALUES (1, 1, 'MATCHED', 900000, 'MANUAL', '{}', 'buyer')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO standard_price_version (
                standard_item_id, version_number, observation_count,
                supplier_count, minimum_price, median_price, average_price,
                maximum_price, observation_decision_ids_json,
                calculation_version, approved_by
            ) VALUES (
                1, 1, 1, 1, 1000000, 1000000, 1000000, 1000000,
                '[1]', 'v1', 'buyer'
            )
            """
        )

    downgrade = _run_alembic(
        backend_path,
        database_path,
        "downgrade",
        "0003",
    )
    assert downgrade.returncode == 0, downgrade.stdout + downgrade.stderr
    inspector = inspect(engine)
    assert not {
        "standard_item",
        "standard_item_version",
        "document_metadata_version",
        "item_membership_decision",
        "standard_price_version",
    } & set(inspector.get_table_names())
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM source_document"
        ).scalar_one() == 1
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM raw_quote_item"
        ).scalar_one() == 1


def test_0004_downgrade_refuses_unknown_dependent_table_before_ddl(
    tmp_path: Path,
) -> None:
    backend_path = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "dependent.sqlite3"
    upgrade = _run_alembic(
        backend_path,
        database_path,
        "upgrade",
        "head",
    )
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE later_dependency (
                id INTEGER PRIMARY KEY,
                standard_item_id INTEGER NOT NULL
                    REFERENCES standard_item(id)
            )
            """
        )

    downgrade = _run_alembic(
        backend_path,
        database_path,
        "downgrade",
        "0003",
    )

    output = downgrade.stdout + downgrade.stderr
    assert downgrade.returncode != 0
    assert "later_dependency" in output
    assert {
        "standard_item",
        "standard_item_version",
        "document_metadata_version",
        "item_membership_decision",
        "standard_price_version",
    } <= set(inspect(engine).get_table_names())
