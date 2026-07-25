"""Local-only command line entry points for corpus audit and ingestion."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.sqlite import configure_sqlite
from app.ingestion.corpus import ingest_corpus, preflight_corpus


EXIT_OK = 0
EXIT_PARTIAL_FAILURE = 1
EXIT_CONFIGURATION_ERROR = 2


class LocalSetupError(ValueError):
    """A user-correctable local path or database setup problem."""


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    quote_root = Path(args.quote_root).expanduser().resolve(strict=False)

    preflight = preflight_corpus(quote_root)
    if args.command == "preflight":
        payload = preflight.to_dict()
        _emit(payload, json_output=args.json, command="preflight")
        return EXIT_OK if preflight.root_available else EXIT_CONFIGURATION_ERROR

    if not preflight.root_available:
        payload = preflight.to_dict()
        _emit(payload, json_output=args.json, command="preflight")
        return EXIT_CONFIGURATION_ERROR

    database_path = (
        Path(args.database_file).expanduser().resolve(strict=False)
    )
    try:
        _upgrade_database(database_path)
    except (OSError, LocalSetupError) as exc:
        return _emit_setup_error(exc, json_output=args.json)

    engine = configure_sqlite(
        create_engine(
            f"sqlite:///{database_path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
    )
    try:
        with Session(engine, expire_on_commit=False) as session:
            report = ingest_corpus(session, quote_root)
    finally:
        engine.dispose()
    payload = report.to_dict()
    if args.report is not None:
        try:
            _write_report(Path(args.report), payload)
        except OSError as exc:
            return _emit_setup_error(exc, json_output=args.json)

    _emit(payload, json_output=args.json, command="ingest")
    return (
        EXIT_PARTIAL_FAILURE
        if report.documents_failed
        else EXIT_OK
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="로컬 견적서 코퍼스를 읽기 전 점검하거나 SQLite에 적재합니다.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command_name in ("preflight", "ingest"):
        command_parser = subparsers.add_parser(command_name)
        command_parser.add_argument(
            "--quote-root",
            default=str(settings.quote_path),
            help="견적서 루트 폴더(기본값: 환경 설정)",
        )
        command_parser.add_argument(
            "--json",
            action="store_true",
            help="기계 판독용 JSON만 출력",
        )
        if command_name == "ingest":
            command_parser.add_argument(
                "--database-file",
                default=str(settings.database_path),
                help="로컬 SQLite 파일(기본값: 환경 설정)",
            )
            command_parser.add_argument(
                "--report",
                help="절대경로를 포함하지 않는 JSON 실행 보고서 저장 경로",
            )
    return parser


def _upgrade_database(database_path: Path) -> None:
    if database_path.exists() and database_path.is_dir():
        raise LocalSetupError("database target is a directory")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.attributes["database_path"] = database_path
    command.upgrade(config, "head")


def _write_report(path: Path, payload: dict[str, object]) -> None:
    report_path = path.expanduser().resolve(strict=False)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=report_path.parent,
        prefix=f".{report_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        stream.write(serialized)
        stream.write("\n")
    try:
        os.replace(temporary, report_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _emit(
    payload: dict[str, object],
    *,
    json_output: bool,
    command: str,
) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    if command == "preflight":
        print("견적서 사전 점검")
        print(f"- 물리 파일: {payload['physical_files']}")
        print(f"- 논리 문서: {payload['logical_documents']}")
        print(f"- 보안해제 우선 문서: {payload['unlocked_preferred']}")
        print(f"- 경로/증빙 이슈: {len(payload['issues'])}")
    elif command == "ingest":
        print("로컬 견적서 적재 결과")
        print(f"- 신규/변경 적재: {payload['documents_ingested']}")
        print(f"- 변경 없음: {payload['documents_unchanged']}")
        print(f"- 실패: {payload['documents_failed']}")
        print(f"- 원시 품목 신규: {payload['raw_items_created']}")
        print(
            "- 정제 결정 신규: "
            f"{payload['base_decisions_created']} + "
            f"이상치 {payload['outlier_decisions_created']}"
        )
    else:
        print(
            f"로컬 설정 오류 [{payload['error_code']}]: "
            f"{payload['detail']}"
        )


def _safe_setup_detail(exc: Exception) -> str:
    if isinstance(exc, PermissionError):
        return "local database or report path is not writable"
    if isinstance(exc, LocalSetupError):
        return str(exc)
    return "local database or report setup failed"


def _emit_setup_error(exc: Exception, *, json_output: bool) -> int:
    payload = {
        "error_code": "LOCAL_SETUP_ERROR",
        "detail": _safe_setup_detail(exc),
    }
    _emit(payload, json_output=json_output, command="error")
    return EXIT_CONFIGURATION_ERROR


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
