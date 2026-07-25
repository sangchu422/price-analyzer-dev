import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import (
    CleanDecision,
    CleanStatus,
    RawQuoteItem,
    SourceDocument,
    SourceVariant,
)
from app.db.sqlite import configure_sqlite


def _run_alembic(
    backend_path: Path,
    environment: dict[str, str],
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
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


def _seed_variant_rows(
    database_path: Path,
    *,
    duplicate_sha: bool,
) -> None:
    migration_engine = create_engine(
        f"sqlite:///{database_path.as_posix()}"
    )
    with migration_engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO source_document (logical_name) VALUES (?)",
            ("migration-evidence",),
        )
        sha_values = ["e" * 64]
        if duplicate_sha:
            sha_values.append("e" * 64)
        for index, digest in enumerate(sha_values, start=1):
            connection.exec_driver_sql(
                """
                INSERT INTO source_variant (
                    document_id,
                    path,
                    sha256,
                    extension,
                    security_state,
                    preferred_for_parsing
                ) VALUES (1, ?, ?, '.xlsx', ?, ?)
                """,
                (
                    f"quotes/evidence-{index}.xlsx",
                    digest,
                    "UNLOCKED" if index == 2 else "UNKNOWN",
                    index == 2,
                ),
            )


def test_0002_downgrade_refuses_duplicate_sha_without_changing_evidence(
    tmp_path: Path,
) -> None:
    backend_path = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "duplicate-evidence.sqlite3"
    environment = os.environ.copy()
    environment["DATABASE_FILE"] = str(database_path)
    upgrade = _run_alembic(backend_path, environment, "upgrade", "0002")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    _seed_variant_rows(database_path, duplicate_sha=True)

    downgrade = _run_alembic(
        backend_path,
        environment,
        "downgrade",
        "0001",
    )

    output = downgrade.stdout + downgrade.stderr
    assert downgrade.returncode != 0
    assert "duplicate SHA-256 evidence paths" in output
    assert "remove or consolidate" not in output
    migration_engine = create_engine(
        f"sqlite:///{database_path.as_posix()}"
    )
    with migration_engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == "0002"
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM source_variant"
        ).scalar_one() == 2
    sha_indexes = [
        index
        for index in inspect(migration_engine).get_indexes("source_variant")
        if index["column_names"] == ["sha256"]
    ]
    assert len(sha_indexes) == 1
    assert sha_indexes[0]["name"] == "ix_source_variant_sha256"
    assert not sha_indexes[0]["unique"]


def test_0002_downgrade_restores_unique_sha_when_evidence_is_compatible(
    tmp_path: Path,
) -> None:
    backend_path = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "compatible-evidence.sqlite3"
    environment = os.environ.copy()
    environment["DATABASE_FILE"] = str(database_path)
    upgrade = _run_alembic(backend_path, environment, "upgrade", "0002")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    _seed_variant_rows(database_path, duplicate_sha=False)

    downgrade = _run_alembic(
        backend_path,
        environment,
        "downgrade",
        "0001",
    )

    assert downgrade.returncode == 0, downgrade.stdout + downgrade.stderr
    migration_engine = create_engine(
        f"sqlite:///{database_path.as_posix()}"
    )
    with migration_engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == "0001"
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM source_variant"
        ).scalar_one() == 1
    sha_indexes = [
        index
        for index in inspect(migration_engine).get_indexes("source_variant")
        if index["column_names"] == ["sha256"]
    ]
    assert len(sha_indexes) == 1
    assert sha_indexes[0]["name"] == "ux_source_variant_sha256"
    assert sha_indexes[0]["unique"]


def test_0003_renames_historical_selection_field_without_data_loss(
    tmp_path: Path,
) -> None:
    backend_path = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "selection-rename.sqlite3"
    environment = os.environ.copy()
    environment["DATABASE_FILE"] = str(database_path)
    upgrade_0002 = _run_alembic(
        backend_path,
        environment,
        "upgrade",
        "0002",
    )
    assert upgrade_0002.returncode == 0, (
        upgrade_0002.stdout + upgrade_0002.stderr
    )
    _seed_variant_rows(database_path, duplicate_sha=False)

    upgrade_head = _run_alembic(
        backend_path,
        environment,
        "upgrade",
        "head",
    )

    assert upgrade_head.returncode == 0, (
        upgrade_head.stdout + upgrade_head.stderr
    )
    migration_engine = create_engine(
        f"sqlite:///{database_path.as_posix()}"
    )
    columns = {
        column["name"]
        for column in inspect(migration_engine).get_columns("source_variant")
    }
    assert "selected_for_parsing_at_ingest" in columns
    assert "preferred_for_parsing" not in columns
    with migration_engine.connect() as connection:
        assert connection.exec_driver_sql(
            """
            SELECT selected_for_parsing_at_ingest
            FROM source_variant
            """
        ).scalar_one() == 0

    downgrade = _run_alembic(
        backend_path,
        environment,
        "downgrade",
        "0002",
    )

    assert downgrade.returncode == 0, downgrade.stdout + downgrade.stderr
    columns = {
        column["name"]
        for column in inspect(migration_engine).get_columns("source_variant")
    }
    assert "preferred_for_parsing" in columns
    assert "selected_for_parsing_at_ingest" not in columns
    with migration_engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT preferred_for_parsing FROM source_variant"
        ).scalar_one() == 0


def test_raw_quote_text_remains_unchanged_after_cleaning_decision() -> None:
    engine = configure_sqlite(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(engine)

    document = SourceDocument(
        logical_name="1차 학습/재검토/5. 견적서",
    )
    variant = SourceVariant(
        path="견적서/1차 학습/재검토/5. 견적서_보안해제.xlsx",
        sha256="a" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    document.variants.append(variant)
    raw_item = RawQuoteItem(
        source_variant=variant,
        source_sheet="단위장비1",
        source_row=12,
        source_cells='{"B12": "  네트워크 스위치  "}',
        item_name_raw="  네트워크 스위치  ",
        spec_raw="24 포트",
        unit_raw="대",
        quantity_raw="2",
        unit_price_raw="1,250,000",
        amount_raw="2,500,000",
        maker_raw="제조사 원문",
        parser_name="xlsx",
        parser_version="1.0",
    )
    raw_item.decisions.append(
        CleanDecision(
            status=CleanStatus.INCLUDED,
            reason_code="VALID",
            item_name_norm="네트워크 스위치",
            spec_norm="24 포트",
            unit_norm="대",
            maker_norm="제조사 원문",
            quantity=Decimal("2"),
            unit_price=Decimal("1250000"),
            amount=Decimal("2500000"),
            rule_version="clean-v1",
        )
    )
    with Session(engine) as session:
        session.add(document)
        session.commit()
        raw_item_id = raw_item.id

    with Session(engine) as session:
        saved = session.scalar(
            select(RawQuoteItem).where(RawQuoteItem.id == raw_item_id)
        )

        assert saved is not None
        assert saved.item_name_raw == "  네트워크 스위치  "
        assert saved.unit_price_raw == "1,250,000"
        assert (
            saved.source_variant.document.logical_name
            == "1차 학습/재검토/5. 견적서"
        )
        assert saved in saved.source_variant.document.raw_items
        assert saved.decisions[0].status is CleanStatus.INCLUDED
        assert saved.decisions[0].reason_code == "VALID"
        assert saved.decisions[0].rule_version == "clean-v1"


def test_initial_migration_creates_source_and_cleansing_tables(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "missing" / "nested" / "migration.sqlite3"
    backend_path = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["DATABASE_FILE"] = str(database_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(backend_path / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=backend_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    migration_engine = create_engine(
        f"sqlite:///{database_path.as_posix()}"
    )
    inspector = inspect(migration_engine)
    tables = set(inspector.get_table_names())
    assert {
        "source_document",
        "source_variant",
        "raw_quote_item",
        "clean_decision",
    } <= tables

    document_columns = {
        column["name"]: column
        for column in inspector.get_columns("source_document")
    }
    assert {
        name: column["nullable"]
        for name, column in document_columns.items()
    } == {
        "id": False,
        "logical_name": False,
        "created_at": False,
    }
    assert document_columns["created_at"]["default"] == "CURRENT_TIMESTAMP"

    variant_columns = {
        column["name"]: column
        for column in inspector.get_columns("source_variant")
    }
    assert {
        name: column["nullable"]
        for name, column in variant_columns.items()
    } == {
        "id": False,
        "document_id": False,
        "path": False,
        "sha256": False,
        "extension": False,
        "security_state": False,
        "selected_for_parsing_at_ingest": False,
        "registered_at": False,
    }
    assert (
        variant_columns["selected_for_parsing_at_ingest"]["default"]
        == "0"
    )
    assert variant_columns["registered_at"]["default"] == "CURRENT_TIMESTAMP"

    raw_columns = {
        column["name"]: column
        for column in inspector.get_columns("raw_quote_item")
    }
    assert {
        name: column["nullable"]
        for name, column in raw_columns.items()
    } == {
        "id": False,
        "source_variant_id": False,
        "source_sheet": True,
        "source_page": True,
        "source_row": True,
        "source_cells": True,
        "item_name_raw": True,
        "spec_raw": True,
        "unit_raw": True,
        "quantity_raw": True,
        "unit_price_raw": True,
        "amount_raw": True,
        "maker_raw": True,
        "parser_name": False,
        "parser_version": False,
        "parse_warnings_json": False,
    }
    assert raw_columns["parse_warnings_json"]["default"] == "'[]'"

    decision_columns = {
        column["name"]: column
        for column in inspector.get_columns("clean_decision")
    }
    assert str(decision_columns["quantity"]["type"]) == "BIGINT"
    assert str(decision_columns["unit_price"]["type"]) == "BIGINT"
    assert str(decision_columns["amount"]["type"]) == "BIGINT"
    assert {
        name: column["nullable"]
        for name, column in decision_columns.items()
    } == {
        "id": False,
        "raw_item_id": False,
        "status": False,
        "reason_code": False,
        "reason_detail": True,
        "item_name_norm": True,
        "spec_norm": True,
        "unit_norm": True,
        "maker_norm": True,
        "quantity": True,
        "unit_price": True,
        "amount": True,
        "rule_version": False,
        "decided_by": False,
        "decided_at": False,
    }
    assert decision_columns["decided_by"]["default"] == "'SYSTEM'"
    assert decision_columns["decided_at"]["default"] == "CURRENT_TIMESTAMP"

    foreign_keys = {
        table: {
            tuple(foreign_key["constrained_columns"]): (
                foreign_key["referred_table"],
                tuple(foreign_key["referred_columns"]),
            )
            for foreign_key in inspector.get_foreign_keys(table)
        }
        for table in ("source_variant", "raw_quote_item", "clean_decision")
    }
    assert foreign_keys["source_variant"][("document_id",)] == (
        "source_document",
        ("id",),
    )
    assert foreign_keys["raw_quote_item"][("source_variant_id",)] == (
        "source_variant",
        ("id",),
    )
    assert foreign_keys["clean_decision"][("raw_item_id",)] == (
        "raw_quote_item",
        ("id",),
    )

    unique_constraints = {
        table: {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(table)
        }
        for table in ("source_document", "source_variant")
    }
    assert ("logical_name",) in unique_constraints["source_document"]
    assert ("path",) in unique_constraints["source_variant"]

    variant_indexes = inspector.get_indexes("source_variant")
    assert not [
        index
        for index in variant_indexes
        if index["column_names"] == ["sha256"] and index["unique"]
    ]
    assert len(
        [
            index
            for index in variant_indexes
            if index["column_names"] == ["sha256"]
        ]
    ) == 1
    assert ["document_id"] in [
        index["column_names"]
        for index in inspector.get_indexes("source_variant")
    ]
    assert ["source_variant_id"] in [
        index["column_names"]
        for index in inspector.get_indexes("raw_quote_item")
    ]
    assert ["raw_item_id"] in [
        index["column_names"]
        for index in inspector.get_indexes("clean_decision")
    ]

    clean_checks = inspector.get_check_constraints("clean_decision")
    assert any(
        check["name"] == "clean_status"
        and "REVIEW_REQUIRED" in check["sqltext"]
        for check in clean_checks
    )

    check_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(backend_path / "alembic.ini"),
            "check",
        ],
        cwd=backend_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check_result.returncode == 0, (
        check_result.stdout + check_result.stderr
    )

    downgrade_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(backend_path / "alembic.ini"),
            "downgrade",
            "base",
        ],
        cwd=backend_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert downgrade_result.returncode == 0, (
        downgrade_result.stdout + downgrade_result.stderr
    )
    assert not {
        "source_document",
        "source_variant",
        "raw_quote_item",
        "clean_decision",
    } & set(inspect(migration_engine).get_table_names())
