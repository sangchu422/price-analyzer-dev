"""Local-only command line entry points for corpus audit and ingestion."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.catalog.cli import (
    build_catalog_embedding_index,
    report_standard_price_drafts,
    seed_exact_catalog,
)
from app.core.config import settings
from app.db.sqlite import configure_sqlite
from app.ingestion.corpus import (
    ingest_corpus,
    preflight_corpus,
    scan_supported_files,
)


EXIT_OK = 0
EXIT_PARTIAL_FAILURE = 1
EXIT_CONFIGURATION_ERROR = 2


class UnsafeOutputPathError(ValueError):
    """An output path could overwrite or mutate quote evidence."""


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command in {
        "catalog-seed",
        "embedding-index",
        "standard-price-drafts",
    }:
        return _run_catalog_command(args)
    try:
        quote_root = (
            Path(args.quote_root).expanduser().resolve(strict=False)
        )
    except (OSError, RuntimeError):
        return _emit_error(
            "ROOT_UNAVAILABLE",
            "quote root could not be safely resolved",
            json_output=args.json,
        )

    preflight = preflight_corpus(quote_root)
    if args.command == "preflight":
        payload = preflight.to_dict()
        _emit(payload, json_output=args.json, command="preflight")
        return EXIT_OK if preflight.root_available else EXIT_CONFIGURATION_ERROR

    if not preflight.root_available:
        payload = preflight.to_dict()
        _emit(payload, json_output=args.json, command="preflight")
        return EXIT_CONFIGURATION_ERROR

    try:
        database_path, report_path = _validate_output_targets(
            quote_root=quote_root,
            database_path=Path(args.database_file),
            report_path=(
                Path(args.report) if args.report is not None else None
            ),
        )
    except UnsafeOutputPathError as exc:
        return _emit_error(
            "UNSAFE_OUTPUT_PATH",
            str(exc),
            json_output=args.json,
        )
    except OSError:
        return _emit_error(
            "UNSAFE_OUTPUT_PATH",
            "output targets could not be safely resolved",
            json_output=args.json,
        )

    try:
        _upgrade_database(database_path)
    except CommandError:
        return _emit_error(
            "DATABASE_MIGRATION_ERROR",
            "database schema revision is incompatible",
            json_output=args.json,
        )
    except (DBAPIError, sqlite3.DatabaseError) as exc:
        return _emit_database_error(exc, json_output=args.json)
    except OSError:
        return _emit_error(
            "DATABASE_UNAVAILABLE",
            "database could not be created or accessed",
            json_output=args.json,
        )

    try:
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
    except (DBAPIError, sqlite3.DatabaseError) as exc:
        return _emit_database_error(exc, json_output=args.json)
    payload = report.to_dict()
    if report_path is not None:
        try:
            _write_report(report_path, payload)
        except OSError:
            return _emit_error(
                "REPORT_WRITE_ERROR",
                "run report could not be written",
                json_output=args.json,
            )

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
    for command_name in (
        "catalog-seed",
        "embedding-index",
        "standard-price-drafts",
    ):
        command_parser = subparsers.add_parser(command_name)
        command_parser.add_argument(
            "--database-file",
            default=str(settings.database_path),
            help="마이그레이션된 로컬 SQLite 파일",
        )
        command_parser.add_argument(
            "--json",
            action="store_true",
            help="기계 판독용 JSON만 출력",
        )
        command_parser.add_argument(
            "--report",
            help="UTF-8 JSON 실행 보고서 저장 경로",
        )
        if command_name == "embedding-index":
            command_parser.add_argument(
                "--index-file",
                default=str(settings.embedding_index_path),
                help="교체 가능한 임베딩 인덱스 파일",
            )
            command_parser.add_argument(
                "--mock",
                action="store_true",
                help="개발 전용 local-mock-v1 인덱스 생성",
            )
    return parser


def _run_catalog_command(args: argparse.Namespace) -> int:
    try:
        database_path = Path(args.database_file).expanduser().resolve(
            strict=False
        )
        report_path = (
            None
            if args.report is None
            else Path(args.report).expanduser().resolve(strict=False)
        )
        if report_path == database_path:
            return _emit_error(
                "UNSAFE_OUTPUT_PATH",
                "database and report targets must be distinct",
                json_output=args.json,
            )
        if database_path.exists() and database_path.is_dir():
            raise OSError("database target is a directory")
        _upgrade_database(database_path)
    except CommandError:
        return _emit_error(
            "DATABASE_MIGRATION_ERROR",
            "database schema revision is incompatible",
            json_output=args.json,
        )
    except (DBAPIError, sqlite3.DatabaseError) as exc:
        return _emit_database_error(exc, json_output=args.json)
    except OSError:
        return _emit_error(
            "DATABASE_UNAVAILABLE",
            "database could not be created or accessed",
            json_output=args.json,
        )

    engine = configure_sqlite(
        create_engine(
            f"sqlite:///{database_path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
    )
    try:
        with Session(engine, expire_on_commit=False) as session:
            if args.command == "catalog-seed":
                report = seed_exact_catalog(session)
                session.commit()
                _publish_catalog_report(
                    report.to_dict(),
                    report_path=report_path,
                    json_output=args.json,
                )
                return EXIT_OK
            if args.command == "standard-price-drafts":
                report = report_standard_price_drafts(session)
                session.rollback()
                _publish_catalog_report(
                    report.to_dict(),
                    report_path=report_path,
                    json_output=args.json,
                )
                return EXIT_OK

            index_path = Path(args.index_file).expanduser().resolve(
                strict=False
            )
            if report_path == index_path:
                return _emit_error(
                    "UNSAFE_OUTPUT_PATH",
                    "embedding index and report targets must be distinct",
                    json_output=args.json,
                )
            if (
                index_path == database_path
                or (
                    index_path.exists()
                    and database_path.exists()
                    and _same_file(index_path, database_path)
                )
            ):
                return _emit_error(
                    "UNSAFE_OUTPUT_PATH",
                    "embedding index and database targets must be distinct",
                    json_output=args.json,
                )
            report = build_catalog_embedding_index(
                session,
                index_path=index_path,
                mock=args.mock,
                settings=settings,
            )
            session.rollback()
            _publish_catalog_report(
                report.to_dict(),
                report_path=report_path,
                json_output=args.json,
            )
            return (
                EXIT_OK
                if report.status
                in {"MOCK_ONLY", "AVAILABLE", "EMPTY_CATALOG"}
                else EXIT_CONFIGURATION_ERROR
            )
    except (DBAPIError, sqlite3.DatabaseError) as exc:
        return _emit_database_error(exc, json_output=args.json)
    finally:
        engine.dispose()


def _emit_catalog(
    payload: dict[str, object],
    *,
    json_output: bool,
) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _publish_catalog_report(
    payload: dict[str, object],
    *,
    report_path: Path | None,
    json_output: bool,
) -> None:
    if report_path is not None:
        _write_report(report_path, payload)
    _emit_catalog(payload, json_output=json_output)


def _upgrade_database(database_path: Path) -> None:
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


def _emit_error(
    error_code: str,
    detail: str,
    *,
    json_output: bool,
) -> int:
    payload = {
        "error_code": error_code,
        "detail": detail,
    }
    _emit(payload, json_output=json_output, command="error")
    return EXIT_CONFIGURATION_ERROR


def _emit_database_error(exc: Exception, *, json_output: bool) -> int:
    normalized = str(exc).casefold()
    if "not a database" in normalized or "malformed" in normalized:
        return _emit_error(
            "DATABASE_INVALID",
            "database file is not a valid compatible SQLite database",
            json_output=json_output,
        )
    if "locked" in normalized:
        return _emit_error(
            "DATABASE_LOCKED",
            "database is locked by another process",
            json_output=json_output,
        )
    return _emit_error(
        "DATABASE_UNAVAILABLE",
        "database could not be initialized or accessed",
        json_output=json_output,
    )


def _validate_output_targets(
    *,
    quote_root: Path,
    database_path: Path,
    report_path: Path | None,
) -> tuple[Path, Path | None]:
    source_paths = scan_supported_files(quote_root)
    candidates = [database_path]
    if report_path is not None:
        candidates.append(report_path)

    canonical: list[Path] = []
    for candidate in candidates:
        expanded = candidate.expanduser()
        if _is_reparse_point(expanded):
            raise UnsafeOutputPathError(
                "output target must not be a symlink or reparse point"
            )
        if expanded.exists() and expanded.is_dir():
            raise UnsafeOutputPathError(
                "output target must be a file path"
            )
        if expanded.exists() and any(
            _same_file(expanded, source_path)
            for source_path in source_paths
        ):
            raise UnsafeOutputPathError(
                "output target must not alias quote source evidence"
            )
        resolved = expanded.resolve(strict=False)
        if _is_within(resolved, quote_root):
            raise UnsafeOutputPathError(
                "output targets must be outside quote root"
            )
        canonical.append(resolved)

    resolved_database = canonical[0]
    resolved_report = canonical[1] if report_path is not None else None
    if (
        resolved_report is not None
        and (
            resolved_database == resolved_report
            or (
                resolved_database.exists()
                and resolved_report.exists()
                and _same_file(resolved_database, resolved_report)
            )
        )
    ):
        raise UnsafeOutputPathError(
            "database and report targets must be distinct"
        )
    return resolved_database, resolved_report


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = os.lstat(path).st_file_attributes
    except (AttributeError, FileNotFoundError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _same_file(first: Path, second: Path) -> bool:
    try:
        return os.path.samefile(first, second)
    except FileNotFoundError:
        return False


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
