from __future__ import annotations

import json
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, inspect, select, text

from app.cli import main


def _write_quote(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["품명", "규격", "단위", "수량", "단가", "금액"])
    sheet.append(["SENSOR", "PX-1", "EA", 1, 11100, 11100])
    workbook.save(path)


def test_preflight_json_has_clear_exit_code_and_korean_safe_output(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "견적서"
    _write_quote(root / "센서_보안해제.xlsx")

    exit_code = main(
        ["preflight", "--quote-root", str(root), "--json"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["physical_files"] == 1
    assert payload["unlocked_preferred"] == 1
    assert payload["root_available"] is True


def test_ingest_initializes_migrated_database_and_writes_report(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "quotes"
    database = tmp_path / "runtime" / "local.sqlite3"
    report_path = tmp_path / "reports" / "run.json"
    _write_quote(root / "quote.xlsx")

    exit_code = main(
        [
            "ingest",
            "--quote-root",
            str(root),
            "--database-file",
            str(database),
            "--report",
            str(report_path),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["documents_ingested"] == 1
    assert payload["raw_items_created"] == 1
    assert report_path.is_file()
    assert json.loads(report_path.read_text(encoding="utf-8")) == payload
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    assert "source_document" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.scalar(select(text("version_num")).select_from(
            text("alembic_version")
        )) == "0003"
    engine.dispose()


def test_ingest_returns_partial_failure_exit_code_without_absolute_paths(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "quotes"
    database = tmp_path / "local.sqlite3"
    workbook = Workbook()
    workbook.active.append(["unknown layout"])
    root.mkdir()
    workbook.save(root / "보안문서.xlsx")

    exit_code = main(
        [
            "ingest",
            "--quote-root",
            str(root),
            "--database-file",
            str(database),
            "--json",
        ]
    )

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 1
    assert payload["documents_failed"] == 1
    assert payload["failures"] == [
        {
            "logical_name": "보안문서",
            "error_code": "UNSUPPORTED_LAYOUT",
            "detail": "source layout is not currently supported",
        }
    ]
    assert str(tmp_path) not in output


def test_missing_quote_root_returns_configuration_exit_code(
    tmp_path: Path,
    capsys,
) -> None:
    exit_code = main(
        [
            "preflight",
            "--quote-root",
            str(tmp_path / "missing"),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["issues"][0]["error_code"] == "QUOTE_ROOT_NOT_FOUND"


def test_ingest_does_not_disguise_unexpected_programmer_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "quotes"
    _write_quote(root / "quote.xlsx")

    def fail_unexpectedly(*args, **kwargs):
        raise ValueError("programming defect")

    monkeypatch.setattr("app.cli.ingest_corpus", fail_unexpectedly)

    with pytest.raises(ValueError, match="programming defect"):
        main(
            [
                "ingest",
                "--quote-root",
                str(root),
                "--database-file",
                str(tmp_path / "local.sqlite3"),
                "--json",
            ]
        )
