from __future__ import annotations

import os
import sqlite3
import subprocess
import uuid
from pathlib import Path

import pytest
from openpyxl import Workbook


@pytest.mark.skipif(os.name != "nt", reason="Windows CMD launcher only")
def test_initialize_only_builds_standard_database_on_fresh_checkout(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[3]
    quote_root = tmp_path / "견적서"
    quote_root.mkdir()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["품명", "규격", "단위", "수량", "단가", "금액"])
    sheet.append(["SENSOR", "PX-1", "EA", 1, 11100, 11100])
    workbook.save(quote_root / "신규견적.xlsx")

    run_id = uuid.uuid4().hex
    database = (
        repository_root
        / "backend"
        / ".local"
        / f"launcher-test-{run_id}.sqlite3"
    )
    report = (
        repository_root
        / "backend"
        / ".local"
        / "reports"
        / f"launcher-test-{run_id}.json"
    )
    environment = os.environ.copy()
    environment.update(
        {
            "PRICE_ANALYZER_DATABASE_FILE": str(database),
            "PRICE_ANALYZER_QUOTE_ROOT": str(quote_root),
            "PRICE_ANALYZER_BUILD_REPORT": str(report),
        }
    )

    try:
        result = subprocess.run(
            [
                "cmd.exe",
                "/d",
                "/c",
                str(repository_root / "scripts" / "start-local.bat"),
                "--initialize-only",
            ],
            cwd=repository_root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        connection = sqlite3.connect(database)
        try:
            assert connection.execute(
                "SELECT COUNT(*) FROM standard_item"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM standard_price_version"
            ).fetchone()[0] == 1
        finally:
            connection.close()
    finally:
        database.unlink(missing_ok=True)
        report.unlink(missing_ok=True)
