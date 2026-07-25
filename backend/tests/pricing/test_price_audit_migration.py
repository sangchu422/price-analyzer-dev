from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from app.db.sqlite import configure_sqlite


def _alembic(
    backend: Path, database: Path, *arguments: str
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_FILE"] = str(database)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(backend / "alembic.ini"),
            *arguments,
        ],
        cwd=backend,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_0006_backfills_price_audit_and_round_trips(
    tmp_path: Path,
) -> None:
    backend = Path(__file__).resolve().parents[2]
    database = tmp_path / "price-audit.sqlite3"
    upgrade_0005 = _alembic(backend, database, "upgrade", "0005")
    assert upgrade_0005.returncode == 0, (
        upgrade_0005.stdout + upgrade_0005.stderr
    )
    engine = configure_sqlite(
        create_engine(f"sqlite:///{database.as_posix()}")
    )
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
            ("a" * 64,),
        )
        connection.exec_driver_sql(
            """
            INSERT INTO raw_quote_item (
                source_variant_id, item_name_raw, parser_name, parser_version
            ) VALUES (1, 'BEARING', 'xlsx', '1')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO clean_decision (
                raw_item_id, status, reason_code, unit_price, rule_version
            ) VALUES (1, 'INCLUDED', 'VALID', 1000000, 'clean-v1')
            """
        )
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
            INSERT INTO item_membership_decision (
                raw_item_id, standard_item_id, status, method,
                evidence_json, decided_by
            ) VALUES (1, 1, 'MATCHED', 'MANUAL', '{}', 'buyer')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO standard_price_version (
                standard_item_id, version_number, observation_count,
                supplier_count, minimum_price, median_price, average_price,
                maximum_price, calculation_version, approved_by
            ) VALUES (
                1, 1, 1, 0, 1000000, 1000000, 1000000, 1000000,
                'legacy-v1', 'buyer'
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO standard_price_observation (
                standard_price_version_id, standard_item_id, raw_item_id,
                clean_decision_id, membership_decision_id, membership_status
            ) VALUES (1, 1, 1, 1, 1, 'MATCHED')
            """
        )

    upgrade = _alembic(backend, database, "upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    columns = {
        row["name"]
        for row in inspect(engine).get_columns("standard_price_version")
    }
    assert {
        "standard_item_version_id",
        "audit_status",
        "draft_fingerprint",
        "excluded_count",
        "review_required_count",
        "exclusion_context_json",
    } <= columns
    with engine.connect() as connection:
        row = connection.exec_driver_sql(
            """
            SELECT standard_item_version_id, audit_status,
                   draft_fingerprint, excluded_count,
                   review_required_count, exclusion_context_json
            FROM standard_price_version WHERE id = 1
            """
        ).one()
        assert connection.exec_driver_sql(
            "PRAGMA foreign_key_check"
        ).all() == []
    assert row.standard_item_version_id is None
    assert row.audit_status == "LEGACY_BACKFILL"
    assert row.draft_fingerprint is None
    assert row.excluded_count == 0
    assert row.review_required_count == 0
    assert json.loads(row.exclusion_context_json) == []
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            INSERT INTO standard_price_version (
                standard_item_id, standard_item_version_id,
                version_number, observation_count, supplier_count,
                minimum_price, median_price, average_price, maximum_price,
                calculation_version, audit_status, draft_fingerprint,
                excluded_count, review_required_count,
                exclusion_context_json, approved_by
            ) VALUES (
                1, 1, 2, 1, 0, 1000000, 1000000, 1000000, 1000000,
                'v2', 'CAPTURED', ?, 0, 0, '[]', 'buyer'
            )
            """,
            ("a" * 64,),
        )
    observation_columns = {
        row["name"]
        for row in inspect(engine).get_columns(
            "standard_price_observation"
        )
    }
    assert "metadata_version_id" in observation_columns
    item_version_unique_keys = {
        tuple(row["column_names"])
        for row in inspect(engine).get_unique_constraints(
            "standard_item_version"
        )
    }
    assert ("id", "standard_item_id") in item_version_unique_keys
    price_version_foreign_keys = {
        tuple(row["constrained_columns"]): tuple(row["referred_columns"])
        for row in inspect(engine).get_foreign_keys(
            "standard_price_version"
        )
    }
    assert price_version_foreign_keys[
        ("standard_item_version_id", "standard_item_id")
    ] == ("id", "standard_item_id")
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            """
            SELECT metadata_version_id
            FROM standard_price_observation WHERE id = 1
            """
        ).scalar_one_or_none() is None
    with engine.begin() as connection:
        for column, value in (
            ("draft_fingerprint", "invalid"),
            ("excluded_count", -1),
            ("review_required_count", -1),
            ("exclusion_context_json", "{}"),
        ):
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        f"UPDATE standard_price_version "
                        f"SET {column} = :value WHERE id = 1"
                    ),
                    {"value": value},
                )
    check = _alembic(backend, database, "check")
    assert check.returncode == 0, check.stdout + check.stderr

    downgrade = _alembic(backend, database, "downgrade", "0005")
    assert downgrade.returncode == 0, downgrade.stdout + downgrade.stderr
    columns = {
        row["name"]
        for row in inspect(engine).get_columns("standard_price_version")
    }
    assert not {
        "standard_item_version_id",
        "audit_status",
        "draft_fingerprint",
        "excluded_count",
        "review_required_count",
        "exclusion_context_json",
    } & columns
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM standard_price_version"
        ).scalar_one() == 2
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM standard_price_observation"
        ).scalar_one() == 1
        assert connection.exec_driver_sql(
            "PRAGMA foreign_key_check"
        ).all() == []
