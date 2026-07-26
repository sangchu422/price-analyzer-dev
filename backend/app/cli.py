"""Local-only command line entry points for corpus audit and ingestion."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import stat
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
from app.standard_database.models import StandardDatabaseBuildRun
from app.standard_database.service import (
    BUILD_ACTOR,
    RULE_VERSION,
    ConcurrentStandardBuild,
    DuplicateStandardKeyConflict,
    ManualMembershipConflict,
    StandardDatabaseBuildResult,
    assign_initial_historical_roles,
    build_standard_database,
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
    if args.command == "standard-db-build":
        return _run_standard_db_command(args)
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
            default=str(
                settings.project_root
                / "backend"
                / ".local"
                / "price-analyzer.sqlite3"
            ),
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
    standard_build_parser = subparsers.add_parser("standard-db-build")
    standard_build_parser.add_argument(
        "--database-file",
        default=str(settings.database_path),
        help="local SQLite database migrated before the standard build",
    )
    standard_build_parser.add_argument(
        "--report",
        help="UTF-8 JSON build report path under backend/.local",
    )
    standard_build_parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON",
    )
    standard_build_parser.add_argument(
        "--actor",
        default=BUILD_ACTOR,
        help="audit actor recorded on created standard evidence",
    )
    return parser


def _run_standard_db_command(args: argparse.Namespace) -> int:
    actor = args.actor.strip()
    if not actor or len(actor) > 100:
        return _emit_error(
            "INVALID_ACTOR",
            "actor must contain 1 to 100 characters",
            json_output=args.json,
        )
    try:
        requested_database = Path(args.database_file).expanduser()
        requested_report = (
            Path(args.report).expanduser()
            if args.report is not None
            else _default_catalog_report_path("standard-db-build")
        )
        database_path, report_path, _ = _validate_catalog_targets(
            database_path=requested_database,
            report_path=requested_report,
            index_path=None,
        )
        _upgrade_database(database_path)
    except UnsafeOutputPathError as exc:
        return _emit_error(
            "UNSAFE_OUTPUT_PATH",
            str(exc),
            json_output=args.json,
        )
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
            try:
                assign_initial_historical_roles(session, actor=actor)
                result = build_standard_database(session, actor=actor)
                run = session.get(StandardDatabaseBuildRun, result.run_id)
                assert run is not None
                payload = _standard_build_payload(
                    result,
                    run,
                    report_path,
                )
                publication = _stage_catalog_report(report_path, payload)
                publication.publish()
                try:
                    session.commit()
                except Exception:
                    session.rollback()
                    publication.restore()
                    raise
                publication.finalize()
            except (
                ConcurrentStandardBuild,
                DuplicateStandardKeyConflict,
                ManualMembershipConflict,
            ) as exc:
                session.rollback()
                return _emit_error(
                    "STANDARD_DB_BUILD_CONFLICT",
                    str(exc),
                    json_output=args.json,
                )
            except OSError:
                session.rollback()
                return _emit_error(
                    "REPORT_WRITE_ERROR",
                    "standard database report could not be written",
                    json_output=args.json,
                )
            except (DBAPIError, sqlite3.DatabaseError):
                session.rollback()
                raise
            except Exception:
                session.rollback()
                return _emit_error(
                    "STANDARD_DB_BUILD_ERROR",
                    "standard database build failed",
                    json_output=args.json,
                )
        _emit_catalog(payload, json_output=args.json)
        return EXIT_OK
    except (DBAPIError, sqlite3.DatabaseError) as exc:
        return _emit_database_error(exc, json_output=args.json)
    finally:
        engine.dispose()


def _standard_build_payload(
    result: StandardDatabaseBuildResult,
    run: StandardDatabaseBuildRun,
    report_path: Path,
) -> dict[str, object]:
    return {
        "status": run.status.value,
        "run_id": result.run_id,
        "reused_run_id": result.reused_run_id,
        "fingerprint": run.input_fingerprint,
        "rule_version": RULE_VERSION,
        "created_standard_items": result.created_standard_items,
        "created_memberships": result.created_memberships,
        "created_price_versions": result.created_price_versions,
        "groups": result.standard_item_count,
        "observations": result.observation_count,
        "single_observation_count": result.single_observation_count,
        "reused_standard_items": result.reused_count,
        "changed_standard_items": result.changed_count,
        "unit_conflict_count": result.unit_conflict_count,
        "exclusions": [asdict(issue) for issue in result.exclusions],
        "conflicts": [asdict(issue) for issue in result.conflicts],
        "report_file": _catalog_report_payload({}, report_path)[
            "report_file"
        ],
    }


def _run_catalog_command(args: argparse.Namespace) -> int:
    try:
        requested_database = Path(args.database_file).expanduser()
        requested_report = (
            Path(args.report).expanduser()
            if args.report is not None
            else _default_catalog_report_path(args.command)
        )
        requested_index = (
            Path(args.index_file).expanduser()
            if args.command == "embedding-index"
            else None
        )
        database_path, report_path, index_path = _validate_catalog_targets(
            database_path=requested_database,
            report_path=requested_report,
            index_path=requested_index,
        )
        _upgrade_database(database_path)
    except UnsafeOutputPathError as exc:
        return _emit_error(
            "UNSAFE_OUTPUT_PATH",
            str(exc),
            json_output=args.json,
        )
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
                payload = _catalog_report_payload(
                    report.to_dict(),
                    report_path,
                )
                try:
                    publication = _stage_catalog_report(
                        report_path,
                        payload,
                    )
                    publication.publish()
                except OSError:
                    session.rollback()
                    return _emit_error(
                        "REPORT_WRITE_ERROR",
                        "catalog report could not be written",
                        json_output=args.json,
                    )
                try:
                    session.commit()
                except Exception:
                    session.rollback()
                    publication.restore()
                    raise
                publication.finalize()
                _emit_catalog(payload, json_output=args.json)
                return EXIT_OK
            if args.command == "standard-price-drafts":
                report = report_standard_price_drafts(session)
                session.rollback()
                try:
                    _publish_catalog_report(
                        _catalog_report_payload(
                            report.to_dict(),
                            report_path,
                        ),
                        report_path=report_path,
                        json_output=args.json,
                    )
                except OSError:
                    return _emit_error(
                        "REPORT_WRITE_ERROR",
                        "standard-price draft report could not be written",
                        json_output=args.json,
                    )
                return EXIT_OK

            assert index_path is not None
            try:
                report = build_catalog_embedding_index(
                    session,
                    index_path=index_path,
                    mock=args.mock,
                    settings=settings,
                )
            except OSError:
                session.rollback()
                return _emit_error(
                    "INDEX_WRITE_ERROR",
                    "embedding index could not be written",
                    json_output=args.json,
                )
            session.rollback()
            try:
                _publish_catalog_report(
                    _catalog_report_payload(
                        report.to_dict(),
                        report_path,
                    ),
                    report_path=report_path,
                    json_output=args.json,
                )
            except OSError:
                return _emit_error(
                    "REPORT_WRITE_ERROR",
                    "embedding index report could not be written",
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


def _catalog_report_payload(
    payload: dict[str, object],
    report_path: Path,
) -> dict[str, object]:
    result = dict(payload)
    try:
        display = report_path.relative_to(settings.project_root).as_posix()
    except ValueError:
        display = report_path.name
    result["report_file"] = display
    return result


def _publish_catalog_report(
    payload: dict[str, object],
    *,
    report_path: Path | None,
    json_output: bool,
) -> None:
    if report_path is not None:
        publication = _stage_catalog_report(report_path, payload)
        publication.publish()
        publication.finalize()
    _emit_catalog(payload, json_output=json_output)


@dataclass
class _ReportPublication:
    final_path: Path
    staged_path: Path
    backup_path: Path | None = None
    published: bool = False

    def publish(self) -> None:
        if self.final_path.exists():
            self.backup_path = self.final_path.with_name(
                f".{self.final_path.name}.{secrets.token_hex(8)}.backup"
            )
            os.replace(self.final_path, self.backup_path)
        try:
            os.replace(self.staged_path, self.final_path)
        except OSError:
            if self.backup_path is not None:
                os.replace(self.backup_path, self.final_path)
                self.backup_path = None
            raise
        self.published = True

    def restore(self) -> None:
        try:
            if self.published and self.final_path.exists():
                self.final_path.unlink()
            if self.backup_path is not None and self.backup_path.exists():
                os.replace(self.backup_path, self.final_path)
        finally:
            if self.staged_path.exists():
                self.staged_path.unlink()
            self.backup_path = None
            self.published = False

    def finalize(self) -> None:
        try:
            if self.backup_path is not None and self.backup_path.exists():
                self.backup_path.unlink()
            if self.staged_path.exists():
                self.staged_path.unlink()
        except OSError:
            # The committed report remains authoritative; a hidden backup can
            # be cleaned by trusted local maintenance on the next run.
            pass
        self.backup_path = None


def _stage_catalog_report(
    path: Path,
    payload: dict[str, object],
) -> _ReportPublication:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    staged: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".staged",
            delete=False,
        ) as stream:
            staged = Path(stream.name)
            stream.write(serialized)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        if staged is not None and staged.exists():
            staged.unlink()
        raise
    assert staged is not None
    return _ReportPublication(final_path=path, staged_path=staged)


def _default_catalog_report_path(command_name: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_id = secrets.token_hex(4)
    return (
        settings.project_root
        / "backend"
        / ".local"
        / "reports"
        / f"{command_name}-{timestamp}-{run_id}.json"
    )


def _validate_catalog_targets(
    *,
    database_path: Path,
    report_path: Path,
    index_path: Path | None,
) -> tuple[Path, Path, Path | None]:
    allowed_root = (
        settings.project_root / "backend" / ".local"
    ).resolve(strict=False)
    requested = [
        ("database", database_path, {".db", ".sqlite3"}),
        ("report", report_path, {".json"}),
    ]
    if index_path is not None:
        requested.append(("index", index_path, {".npz"}))

    resolved: list[tuple[str, Path]] = []
    for role, path, suffixes in requested:
        if _path_has_reparse_component(path):
            raise UnsafeOutputPathError(
                f"{role} target must not use a symlink or reparse point"
            )
        target = path.resolve(strict=False)
        if target.suffix.casefold() not in suffixes:
            raise UnsafeOutputPathError(
                f"{role} target has an unsupported file extension"
            )
        if target.exists() and target.is_dir():
            raise UnsafeOutputPathError(
                f"{role} target must be a file path"
            )
        if not _is_within(target, allowed_root):
            raise UnsafeOutputPathError(
                "catalog database and outputs must stay under backend/.local"
            )
        resolved.append((role, target))

    for index, (role, target) in enumerate(resolved):
        for other_role, other in resolved[index + 1 :]:
            if target == other or (
                target.exists()
                and other.exists()
                and _same_file(target, other)
            ):
                raise UnsafeOutputPathError(
                    f"{role} and {other_role} targets must be distinct"
                )

    source_paths, source_hashes = _catalog_source_evidence(resolved[0][1])
    for role, target in resolved:
        aliases_source = any(
            target == source
            or (
                target.exists()
                and source.exists()
                and _same_file(target, source)
            )
            for source in source_paths
        )
        duplicates_source = (
            target.is_file()
            and _sha256_file(target) in source_hashes
        )
        if aliases_source or duplicates_source:
            raise UnsafeOutputPathError(
                f"{role} target must not alias quote source evidence"
            )
    by_role = dict(resolved)
    return by_role["database"], by_role["report"], by_role.get("index")


def _catalog_source_evidence(
    database_path: Path,
) -> tuple[set[Path], set[str]]:
    quote_root = settings.quote_path.resolve(strict=False)
    paths = {
        path.resolve(strict=False)
        for path in scan_supported_files(settings.quote_path)
    }
    hashes: set[str] = set()
    if not database_path.is_file():
        return paths, hashes
    try:
        uri = f"file:{database_path.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='source_variant'"
            ).fetchone()
            if exists:
                for value, digest in connection.execute(
                    "SELECT path, sha256 FROM source_variant"
                ):
                    source = Path(value)
                    if not source.is_absolute():
                        source = quote_root / source
                    paths.add(source.resolve(strict=False))
                    if isinstance(digest, str) and len(digest) == 64:
                        hashes.add(digest.casefold())
    except sqlite3.DatabaseError:
        pass
    return paths, hashes


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_has_reparse_component(path: Path) -> bool:
    candidate = path.expanduser()
    for component in (candidate, *candidate.parents):
        if component.exists() and _is_reparse_point(component):
            return True
    return False


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
