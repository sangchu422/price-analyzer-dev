from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.catalog.models import (
    DocumentMetadataCandidate,
    DocumentMetadataScan,
    DocumentMetadataVersion,
)
from app.db.base import Base
from app.db.sqlite import configure_sqlite
from app.ingestion.service import ingest_path
from app.metadata_audit.service import audit_quote_metadata


def _write_quote(path: Path, supplier: str, quote_date: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "견적"
    sheet.append(["공급사", supplier])
    sheet.append(["견적일", quote_date])
    sheet.append(["공사명", "자동화 라인"])
    sheet.append([])
    sheet.append(["품명", "규격", "단위", "수량", "단가", "금액"])
    sheet.append(["SERVO MOTOR", "1KW", "EA", 1, 1000000, 1000000])
    workbook.save(path)


def test_audit_uses_labeled_source_values_not_collection_folder(tmp_path: Path) -> None:
    quote_root = tmp_path / "견적서"
    quote = quote_root / "3차 학습" / "AONE 추출 견적서" / "actual.xlsx"
    _write_quote(quote, "실제공급사", "2024-05-20")
    engine = configure_sqlite(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ingest_path(session, quote, root=quote_root)
        report_path = tmp_path / "audit.csv"

        first = audit_quote_metadata(
            session,
            quote_root=quote_root,
            report_path=report_path,
        )
        second = audit_quote_metadata(
            session,
            quote_root=quote_root,
            report_path=report_path,
        )
        session.commit()

        assert first.total_files == second.total_files == 1
        assert first.auto_confirmed_files == 1
        assert session.scalar(select(func.count(DocumentMetadataScan.id))) == 1
        assert session.scalar(select(func.count(DocumentMetadataCandidate.id))) == 3
        metadata = session.scalar(select(DocumentMetadataVersion))
        assert metadata is not None
        assert metadata.supplier_name == "실제공급사"
        assert metadata.quote_date.isoformat() == "2024-05-20"
        assert metadata.project_name == "자동화 라인"
        assert "AONE" not in metadata.supplier_name
        with report_path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert rows[0]["acquisition_channel"] == "AONE"
        assert rows[0]["supplier_name"] == "실제공급사"


def test_audit_rejects_collection_labels_and_placeholder_dates(tmp_path: Path) -> None:
    quote_root = tmp_path / "견적서"
    quote = quote_root / "3차 학습" / "바츠 추출 견적서" / "placeholder.xlsx"
    _write_quote(quote, "바츠추출", "2025-01-01")
    engine = configure_sqlite(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ingest_path(session, quote, root=quote_root)

        report = audit_quote_metadata(
            session,
            quote_root=quote_root,
            report_path=tmp_path / "audit.csv",
        )
        session.commit()

        assert report.review_required_files == 1
        assert session.scalar(select(func.count(DocumentMetadataVersion.id))) == 1
        metadata = session.scalar(select(DocumentMetadataVersion))
        assert metadata is not None
        assert metadata.supplier_name is None
        assert metadata.quote_date is None
        assert metadata.project_name == "자동화 라인"


def test_audit_records_unregistered_manual_review_files(tmp_path: Path) -> None:
    quote_root = tmp_path / "견적서"
    audit_root = quote_root / "3차 학습"
    quote = audit_root / "source.xlsx"
    image = audit_root / "scan.jpg"
    _write_quote(quote, "실제공급사", "2024-05-20")
    image.write_bytes(b"manual image review")
    engine = configure_sqlite(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ingest_path(session, quote, root=quote_root)

        report = audit_quote_metadata(
            session,
            quote_root=quote_root,
            report_path=tmp_path / "audit.csv",
        )
        session.commit()

        assert report.total_files == 2
        assert report.unsupported_files == 1
        scans = list(session.scalars(select(DocumentMetadataScan)))
        assert len(scans) == 2
        by_name = {Path(scan.source_path).name: scan for scan in scans}
        assert by_name["source.xlsx"].source_variant_id is not None
        assert by_name["scan.jpg"].source_variant_id is None
        assert by_name["scan.jpg"].review_status == "REVIEW_REQUIRED"


def test_audit_allows_corpus_without_third_training_folder(
    tmp_path: Path,
) -> None:
    quote_root = tmp_path / "견적서"
    quote_root.mkdir()
    engine = configure_sqlite(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(engine)
    report_path = tmp_path / "audit.csv"

    with Session(engine) as session:
        report = audit_quote_metadata(
            session,
            quote_root=quote_root,
            report_path=report_path,
        )

    assert report.total_files == 0
    assert report.review_required_files == 0
    with report_path.open(encoding="utf-8-sig", newline="") as stream:
        assert list(csv.DictReader(stream)) == []
