"""CLI for importing quote-date evidence from the team standard workbook."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.sqlite import configure_sqlite
from app.metadata_audit.team_standard_dates import backfill_team_standard_dates


def main() -> int:
    parser = argparse.ArgumentParser(
        description="팀 표준단가DB 원본행의 견적일을 현재 원본 문서와 대조합니다.",
    )
    parser.add_argument("--workbook", required=True)
    parser.add_argument("--source-json", required=True)
    parser.add_argument("--source-revision", default="team/main")
    parser.add_argument("--database-file", default=str(settings.database_path))
    parser.add_argument(
        "--report",
        default=str(
            settings.project_root
            / "backend"
            / ".local"
            / "reports"
            / "team-standard-date-backfill.json"
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="검증만 하지 않고 append-only 메타데이터 버전을 추가합니다.",
    )
    args = parser.parse_args()

    workbook_path = Path(args.workbook).expanduser().resolve(strict=True)
    source_json_path = Path(args.source_json).expanduser().resolve(strict=True)
    database_path = Path(args.database_file).expanduser().resolve(strict=False)
    report_path = Path(args.report).expanduser().resolve(strict=False)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _upgrade_database(database_path)
    engine = configure_sqlite(
        create_engine(
            f"sqlite:///{database_path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
    )
    try:
        with Session(engine) as session:
            report = backfill_team_standard_dates(
                session,
                workbook_path=workbook_path,
                source_json_path=source_json_path,
                source_revision=args.source_revision,
                apply=args.apply,
            )
            if args.apply:
                session.commit()
            else:
                session.rollback()
    finally:
        engine.dispose()

    payload = report.to_dict()
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def _upgrade_database(database_path: Path) -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes["database_path"] = database_path
    command.upgrade(config, "head")


if __name__ == "__main__":
    raise SystemExit(main())
