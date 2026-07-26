from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine, inspect
from sqlalchemy.exc import IntegrityError


def _alembic(
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


_LEGACY_TABLES = {
    "legacy_reconciliation_run",
    "legacy_standard_snapshot",
    "legacy_source_row_snapshot",
    "legacy_raw_item_link",
    "legacy_reconciliation_decision",
    "standard_item_external_code",
}


def _seed_rejected_legacy_schema(connection: Connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE TABLE legacy_reconciliation_run (
            id INTEGER NOT NULL PRIMARY KEY,
            workbook_sha256 VARCHAR(64) NOT NULL
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE legacy_standard_snapshot (
            id INTEGER NOT NULL PRIMARY KEY,
            run_id INTEGER NOT NULL REFERENCES legacy_reconciliation_run(id)
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE legacy_source_row_snapshot (
            id INTEGER NOT NULL PRIMARY KEY,
            run_id INTEGER NOT NULL REFERENCES legacy_reconciliation_run(id)
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE legacy_raw_item_link (
            id INTEGER NOT NULL PRIMARY KEY,
            run_id INTEGER NOT NULL REFERENCES legacy_reconciliation_run(id),
            legacy_source_row_id INTEGER NOT NULL
                REFERENCES legacy_source_row_snapshot(id)
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE legacy_reconciliation_decision (
            id INTEGER NOT NULL PRIMARY KEY,
            run_id INTEGER NOT NULL REFERENCES legacy_reconciliation_run(id)
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE standard_item_external_code (
            id INTEGER NOT NULL PRIMARY KEY,
            reconciliation_run_id INTEGER NOT NULL
                REFERENCES legacy_reconciliation_run(id),
            legacy_standard_snapshot_id INTEGER NOT NULL
                REFERENCES legacy_standard_snapshot(id)
        )
        """
    )
    connection.exec_driver_sql(
        "INSERT INTO legacy_reconciliation_run VALUES (1, ?)",
        ("a" * 64,),
    )
    connection.exec_driver_sql(
        "INSERT INTO legacy_standard_snapshot VALUES (1, 1)"
    )
    connection.exec_driver_sql(
        "INSERT INTO legacy_source_row_snapshot VALUES (1, 1)"
    )
    connection.exec_driver_sql(
        "INSERT INTO legacy_raw_item_link VALUES (1, 1, 1)"
    )


def test_0008_creates_only_standard_database_build_tables(
    tmp_path: Path,
) -> None:
    backend_path = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "standard-database.sqlite3"
    environment = os.environ.copy()
    environment["DATABASE_FILE"] = str(database_path)

    upgrade = _alembic(backend_path, environment, "upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"quote_document_role", "standard_database_build_run"} <= tables
    assert not {name for name in tables if name.startswith("legacy_")}

    role_columns = {
        column["name"]: column
        for column in inspector.get_columns("quote_document_role")
    }
    assert set(role_columns) == {
        "id",
        "document_id",
        "purpose",
        "supersedes_role_id",
        "decided_by",
        "reason_detail",
        "decided_at",
    }
    assert role_columns["decided_at"]["default"] == "CURRENT_TIMESTAMP"
    assert ["document_id"] in [
        index["column_names"]
        for index in inspector.get_indexes("quote_document_role")
    ]
    assert ("supersedes_role_id",) in {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("quote_document_role")
    }
    role_foreign_keys = {
        tuple(foreign_key["constrained_columns"]): (
            foreign_key["referred_table"],
            tuple(foreign_key["referred_columns"]),
            foreign_key["options"].get("ondelete"),
        )
        for foreign_key in inspector.get_foreign_keys("quote_document_role")
    }
    assert role_foreign_keys[("document_id",)] == (
        "source_document",
        ("id",),
        "RESTRICT",
    )
    assert role_foreign_keys[("supersedes_role_id", "document_id")] == (
        "quote_document_role",
        ("id", "document_id"),
        "RESTRICT",
    )
    assert ("id", "document_id") in {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("quote_document_role")
    }
    role_checks = inspector.get_check_constraints("quote_document_role")
    assert any(
        check["name"] == "quote_document_purpose"
        and "HISTORICAL_REFERENCE" in check["sqltext"]
        and "INCOMING_BID" in check["sqltext"]
        for check in role_checks
    )

    build_columns = {
        column["name"]: column
        for column in inspector.get_columns("standard_database_build_run")
    }
    assert set(build_columns) == {
        "id",
        "input_fingerprint",
        "rule_version",
        "status",
        "report_path",
        "counts_json",
        "error_detail",
        "started_at",
        "finished_at",
    }
    assert build_columns["counts_json"]["default"] == "'{}'"
    assert build_columns["started_at"]["default"] == "CURRENT_TIMESTAMP"
    build_checks = inspector.get_check_constraints(
        "standard_database_build_run"
    )
    assert any(
        check["name"] == "ck_standard_database_build_input_fingerprint"
        and "length(input_fingerprint) = 64" in check["sqltext"]
        for check in build_checks
    )
    assert any(
        check["name"] == "standard_build_status"
        and "SUCCEEDED" in check["sqltext"]
        for check in build_checks
    )
    success_indexes = [
        index
        for index in inspector.get_indexes("standard_database_build_run")
        if index["column_names"] == ["input_fingerprint", "rule_version"]
        and index["unique"]
    ]
    assert len(success_indexes) == 1
    sqlite_where = success_indexes[0]["dialect_options"]["sqlite_where"]
    assert "SUCCEEDED" in str(sqlite_where)
    with engine.begin() as connection:
        for status in ("RUNNING", "FAILED", "SUCCEEDED"):
            connection.exec_driver_sql(
                """
                INSERT INTO standard_database_build_run (
                    input_fingerprint, rule_version, status
                ) VALUES (?, 'rules-v1', ?)
                """,
                ("a" * 64, status),
            )
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(
                """
                INSERT INTO standard_database_build_run (
                    input_fingerprint, rule_version, status
                ) VALUES (?, 'rules-v1', 'SUCCEEDED')
                """,
                ("a" * 64,),
            )

    check = _alembic(backend_path, environment, "check")
    assert check.returncode == 0, check.stdout + check.stderr

    downgrade = _alembic(backend_path, environment, "downgrade", "0006")
    assert downgrade.returncode == 0, downgrade.stdout + downgrade.stderr
    tables_after_downgrade = set(inspect(engine).get_table_names())
    assert not {
        "quote_document_role",
        "standard_database_build_run",
    } & tables_after_downgrade

    reupgrade = _alembic(backend_path, environment, "upgrade", "head")
    assert reupgrade.returncode == 0, reupgrade.stdout + reupgrade.stderr
    recheck = _alembic(backend_path, environment, "check")
    assert recheck.returncode == 0, recheck.stdout + recheck.stderr


def test_0008_replaces_legacy_stamped_schema_with_standard_build_tables(
    tmp_path: Path,
) -> None:
    backend_path = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "legacy-stamped.sqlite3"
    environment = os.environ.copy()
    environment["DATABASE_FILE"] = str(database_path)

    upgrade = _alembic(backend_path, environment, "upgrade", "0006")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        _seed_rejected_legacy_schema(connection)
        connection.exec_driver_sql(
            "UPDATE alembic_version SET version_num = '0007'"
        )

    compatibility_upgrade = _alembic(
        backend_path,
        environment,
        "upgrade",
        "head",
    )
    assert compatibility_upgrade.returncode == 0, (
        compatibility_upgrade.stdout + compatibility_upgrade.stderr
    )
    tables = set(inspect(engine).get_table_names())
    assert {"quote_document_role", "standard_database_build_run"} <= tables
    assert not _LEGACY_TABLES & tables
    check = _alembic(backend_path, environment, "check")
    assert check.returncode == 0, check.stdout + check.stderr


@pytest.mark.parametrize(
    "protected_table",
    ["legacy_reconciliation_decision", "standard_item_external_code"],
)
def test_0008_preserves_populated_protected_legacy_evidence(
    tmp_path: Path,
    protected_table: str,
) -> None:
    backend_path = Path(__file__).resolve().parents[2]
    database_path = tmp_path / f"protected-{protected_table}.sqlite3"
    environment = os.environ.copy()
    environment["DATABASE_FILE"] = str(database_path)

    upgrade = _alembic(backend_path, environment, "upgrade", "0006")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        _seed_rejected_legacy_schema(connection)
        if protected_table == "legacy_reconciliation_decision":
            connection.exec_driver_sql(
                "INSERT INTO legacy_reconciliation_decision VALUES (1, 1)"
            )
        else:
            connection.exec_driver_sql(
                "INSERT INTO standard_item_external_code VALUES (1, 1, 1)"
            )
        connection.exec_driver_sql(
            "UPDATE alembic_version SET version_num = '0007'"
        )

    compatibility_upgrade = _alembic(
        backend_path,
        environment,
        "upgrade",
        "head",
    )
    assert compatibility_upgrade.returncode != 0
    assert "manual recovery is required" in (
        compatibility_upgrade.stdout + compatibility_upgrade.stderr
    )
    tables = set(inspect(engine).get_table_names())
    assert _LEGACY_TABLES <= tables
    assert "quote_document_role" not in tables
    assert "standard_database_build_run" not in tables
    with engine.connect() as connection:
        for table_name in (
            "legacy_reconciliation_run",
            "legacy_standard_snapshot",
            "legacy_source_row_snapshot",
            "legacy_raw_item_link",
        ):
            assert connection.exec_driver_sql(
                f"SELECT COUNT(*) FROM {table_name}"
            ).scalar_one() == 1
        assert connection.exec_driver_sql(
            f"SELECT COUNT(*) FROM {protected_table}"
        ).scalar_one() == 1
