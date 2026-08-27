from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


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


def test_0016_backfills_every_standard_item_and_round_trips(tmp_path: Path) -> None:
    backend_path = Path(__file__).resolve().parents[2]
    database_path = tmp_path / "command-center.sqlite3"
    environment = os.environ.copy()
    environment["DATABASE_FILE"] = str(database_path)

    before = _alembic(backend_path, environment, "upgrade", "0015")
    assert before.returncode == 0, before.stdout + before.stderr
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO standard_item DEFAULT VALUES"))
        connection.execute(text("INSERT INTO standard_item DEFAULT VALUES"))
        connection.execute(
            text(
                """
                INSERT INTO standard_item_version
                    (standard_item_id, version_number, canonical_name,
                     canonical_spec, canonical_unit, aliases_json,
                     created_by, change_reason)
                VALUES
                    (1, 1, 'SERVO MOTOR', '1KW', 'EA', '[]', 'test', 'seed'),
                    (2, 1, 'SPECIAL ASSEMBLY', 'ZX-991', 'SET', '[]', 'test', 'seed')
                """
            )
        )

    upgrade = _alembic(backend_path, environment, "upgrade", "0016")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM item_category")) == 14
        assert connection.scalar(
            text("SELECT count(*) FROM standard_item_category_assignment")
        ) == 2
        categories = dict(
            connection.execute(
                text(
                    """
                    SELECT a.standard_item_id, c.code
                    FROM standard_item_category_assignment a
                    JOIN item_category c ON c.id = a.category_id
                    ORDER BY a.standard_item_id
                    """
                )
            ).tuples().all()
        )
        assert categories == {1: "DRIVE_MOTION", 2: "GENERAL_COMPONENT"}
    upgrade_head = _alembic(backend_path, environment, "upgrade", "head")
    assert upgrade_head.returncode == 0, upgrade_head.stdout + upgrade_head.stderr
    with engine.connect() as connection:
        tables = set(inspect(engine).get_table_names())
        assert "quote_catalog_state_decision" in tables
        assert "procurement_indicator_sync_run" in tables
        assert "procurement_indicator_point" in tables
    check = _alembic(backend_path, environment, "check")
    assert check.returncode == 0, check.stdout + check.stderr

    downgrade = _alembic(backend_path, environment, "downgrade", "0015")
    assert downgrade.returncode == 0, downgrade.stdout + downgrade.stderr
    tables = set(inspect(engine).get_table_names())
    assert "item_category" not in tables
    assert "quote_catalog_activation_run" not in tables

    reupgrade = _alembic(backend_path, environment, "upgrade", "head")
    assert reupgrade.returncode == 0, reupgrade.stdout + reupgrade.stderr
    recheck = _alembic(backend_path, environment, "check")
    assert recheck.returncode == 0, recheck.stdout + recheck.stderr
    engine.dispose()
