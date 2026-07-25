from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import ArgumentError, InvalidRequestError

from app.cli import _is_reparse_point, main


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
    failure = payload["failures"][0]
    assert failure["logical_name"] == "보안문서"
    assert failure["error_code"] == "UNSUPPORTED_LAYOUT"
    assert failure["detail"] == "source layout is not currently supported"
    assert failure["preferred_path"] == "보안문서.xlsx"
    assert len(failure["preferred_sha256"]) == 64
    assert failure["variants"] == [
        {
            "path": "보안문서.xlsx",
            "sha256": failure["preferred_sha256"],
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


@pytest.mark.parametrize(
    "error_type",
    [InvalidRequestError, ArgumentError],
)
def test_ingest_does_not_disguise_sqlalchemy_programming_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    root = tmp_path / "quotes"
    _write_quote(root / "quote.xlsx")

    def fail_unexpectedly(*args, **kwargs):
        raise error_type("programming defect")

    monkeypatch.setattr("app.cli.ingest_corpus", fail_unexpectedly)

    with pytest.raises(error_type, match="programming defect"):
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


def test_quote_root_resolution_failure_is_sanitized_before_any_write(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "quotes"
    database = tmp_path / "local.sqlite3"
    report = tmp_path / "run.json"
    _write_quote(root / "quote.xlsx")
    original_resolve = Path.resolve

    def fail_root_resolution(self: Path, *args, **kwargs):
        if self == root:
            raise RuntimeError(f"symlink loop at sensitive path: {self}")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_root_resolution)

    exit_code = main(
        [
            "ingest",
            "--quote-root",
            str(root),
            "--database-file",
            str(database),
            "--report",
            str(report),
            "--json",
        ]
    )

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 2
    assert payload == {
        "error_code": "ROOT_UNAVAILABLE",
        "detail": "quote root could not be safely resolved",
    }
    assert str(tmp_path) not in output
    assert not database.exists()
    assert not report.exists()


def test_quote_root_symlink_loop_is_sanitized_when_supported(
    tmp_path: Path,
    capsys,
) -> None:
    loop = tmp_path / "loop"
    try:
        loop.symlink_to(loop, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    exit_code = main(
        [
            "preflight",
            "--quote-root",
            str(loop),
            "--json",
        ]
    )

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 2
    assert payload["error_code"] == "ROOT_UNAVAILABLE"
    assert str(tmp_path) not in output


def test_rejects_report_that_is_the_database_before_writing(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "quotes"
    output = tmp_path / "runtime" / "shared.sqlite3"
    _write_quote(root / "quote.xlsx")

    exit_code = main(
        [
            "ingest",
            "--quote-root",
            str(root),
            "--database-file",
            str(output),
            "--report",
            str(output),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload == {
        "error_code": "UNSAFE_OUTPUT_PATH",
        "detail": "database and report targets must be distinct",
    }
    assert not output.exists()


def test_rejects_output_targets_inside_quote_root_without_touching_source(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "quotes"
    source = root / "quote.xlsx"
    _write_quote(source)
    original_bytes = source.read_bytes()
    database = root / "runtime.sqlite3"

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

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["error_code"] == "UNSAFE_OUTPUT_PATH"
    assert payload["detail"] == "output targets must be outside quote root"
    assert not database.exists()
    assert source.read_bytes() == original_bytes


def test_rejects_report_symlink_to_source_without_touching_source(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "quotes"
    source = root / "quote.xlsx"
    report_link = tmp_path / "report.json"
    _write_quote(source)
    original_bytes = source.read_bytes()
    try:
        report_link.symlink_to(source)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")

    exit_code = main(
        [
            "ingest",
            "--quote-root",
            str(root),
            "--database-file",
            str(tmp_path / "local.sqlite3"),
            "--report",
            str(report_link),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload == {
        "error_code": "UNSAFE_OUTPUT_PATH",
        "detail": "output target must not be a symlink or reparse point",
    }
    assert source.read_bytes() == original_bytes


def test_rejects_database_hardlink_to_source_without_touching_source(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "quotes"
    source = root / "quote.xlsx"
    database_alias = tmp_path / "database.sqlite3"
    _write_quote(source)
    original_bytes = source.read_bytes()
    try:
        os.link(source, database_alias)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    exit_code = main(
        [
            "ingest",
            "--quote-root",
            str(root),
            "--database-file",
            str(database_alias),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload == {
        "error_code": "UNSAFE_OUTPUT_PATH",
        "detail": "output target must not alias quote source evidence",
    }
    assert source.read_bytes() == original_bytes


def test_rejects_report_symlink_to_database(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "quotes"
    database = tmp_path / "local.sqlite3"
    report_link = tmp_path / "report.json"
    _write_quote(root / "quote.xlsx")
    try:
        report_link.symlink_to(database)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")

    exit_code = main(
        [
            "ingest",
            "--quote-root",
            str(root),
            "--database-file",
            str(database),
            "--report",
            str(report_link),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["error_code"] == "UNSAFE_OUTPUT_PATH"
    assert not database.exists()


def test_rejects_report_hardlink_to_database(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "quotes"
    database = tmp_path / "local.sqlite3"
    report_alias = tmp_path / "report.json"
    _write_quote(root / "quote.xlsx")
    database.write_bytes(b"sentinel")
    try:
        os.link(database, report_alias)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    exit_code = main(
        [
            "ingest",
            "--quote-root",
            str(root),
            "--database-file",
            str(database),
            "--report",
            str(report_alias),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload == {
        "error_code": "UNSAFE_OUTPUT_PATH",
        "detail": "database and report targets must be distinct",
    }
    assert database.read_bytes() == b"sentinel"


def test_rejects_nonexistent_output_under_parent_alias_into_quote_root(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "quotes"
    nested = root / "nested"
    nested.mkdir(parents=True)
    _write_quote(root / "quote.xlsx")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(nested, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    report = alias / "new-report.json"

    exit_code = main(
        [
            "ingest",
            "--quote-root",
            str(root),
            "--database-file",
            str(tmp_path / "local.sqlite3"),
            "--report",
            str(report),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["error_code"] == "UNSAFE_OUTPUT_PATH"
    assert not report.exists()
    assert not (nested / report.name).exists()


def test_corrupt_database_returns_sanitized_json_error(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "quotes"
    database = tmp_path / "corrupt.sqlite3"
    _write_quote(root / "quote.xlsx")
    corrupt_bytes = b"not a sqlite database"
    database.write_bytes(corrupt_bytes)

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
    assert exit_code == 2
    assert payload == {
        "error_code": "DATABASE_INVALID",
        "detail": "database file is not a valid compatible SQLite database",
    }
    assert str(tmp_path) not in output
    assert database.read_bytes() == corrupt_bytes


def test_unknown_database_revision_returns_sanitized_json_error(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "quotes"
    database = tmp_path / "future.sqlite3"
    _write_quote(root / "quote.xlsx")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE alembic_version "
            "(version_num VARCHAR(32) NOT NULL)"
        )
        connection.execute(
            "INSERT INTO alembic_version VALUES ('future_revision')"
        )

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
    assert exit_code == 2
    assert payload == {
        "error_code": "DATABASE_MIGRATION_ERROR",
        "detail": "database schema revision is incompatible",
    }
    assert str(tmp_path) not in output


def test_existing_output_directory_is_rejected_before_migration(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "quotes"
    database_directory = tmp_path / "database-directory"
    database_directory.mkdir()
    _write_quote(root / "quote.xlsx")

    exit_code = main(
        [
            "ingest",
            "--quote-root",
            str(root),
            "--database-file",
            str(database_directory),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["error_code"] == "UNSAFE_OUTPUT_PATH"
    assert payload["detail"] == "output target must be a file path"


def test_existing_report_directory_is_rejected_before_migration(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "quotes"
    report_directory = tmp_path / "report-directory"
    report_directory.mkdir()
    database = tmp_path / "local.sqlite3"
    _write_quote(root / "quote.xlsx")

    exit_code = main(
        [
            "ingest",
            "--quote-root",
            str(root),
            "--database-file",
            str(database),
            "--report",
            str(report_directory),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["error_code"] == "UNSAFE_OUTPUT_PATH"
    assert not database.exists()


def test_windows_reparse_attribute_is_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "junction"
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(
        "app.cli.os.lstat",
        lambda path: SimpleNamespace(
            st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT
        ),
    )

    assert _is_reparse_point(candidate)
