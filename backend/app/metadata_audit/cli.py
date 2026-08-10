"""Run the local source-document metadata audit."""

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
from app.metadata_audit.service import audit_quote_metadata


def main() -> int:
    parser = argparse.ArgumentParser(
        description="3차 학습 원본의 공급사·견적일·공사명 근거를 감사합니다.",
    )
    parser.add_argument("--quote-root", default=str(settings.quote_path))
    parser.add_argument("--database-file", default=str(settings.database_path))
    parser.add_argument(
        "--report",
        default=str(
            settings.project_root
            / "backend"
            / ".local"
            / "reports"
            / "third-training-metadata-audit.csv"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    quote_root = Path(args.quote_root).expanduser().resolve(strict=True)
    database_path = Path(args.database_file).expanduser().resolve(strict=False)
    report_path = Path(args.report).expanduser().resolve(strict=False)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    _upgrade_database(database_path)
    engine = configure_sqlite(
        create_engine(
            f"sqlite:///{database_path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
    )
    try:
        with Session(engine) as session:
            report = audit_quote_metadata(
                session,
                quote_root=quote_root,
                report_path=report_path,
            )
            session.commit()
    finally:
        engine.dispose()
    payload = report.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"감사 완료: {report.total_files}개 / "
            f"자동 확인 {report.auto_confirmed_files}개 / "
            f"검토 필요 {report.review_required_files}개"
        )
        print(f"보고서: {report.report_file}")
    return 0


def _upgrade_database(database_path: Path) -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes["database_path"] = database_path
    command.upgrade(config, "head")


if __name__ == "__main__":
    raise SystemExit(main())
