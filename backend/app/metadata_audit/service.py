"""Extract document metadata only when the source file provides evidence."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import xlrd
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import (
    DocumentMetadataCandidate,
    DocumentMetadataScan,
    DocumentMetadataVersion,
)
from app.catalog.service import current_document_metadata
from app.documents.models import SourceVariant


AUDIT_RULE_VERSION = "document-metadata-v2"
AUTOMATED_METADATA_ACTORS = {
    "metadata-audit-v1",
    "metadata-audit-v2",
}
SUPPORTED_EXTENSIONS = {".xlsx", ".xls", ".pdf"}
PLACEHOLDER_VALUES = {
    "",
    "-",
    "미상",
    "미확인",
    "협력사미상",
    "aone협력사",
    "바츠추출",
}
PLACEHOLDER_DATES = {"2025-01-01"}
LABELS = {
    "supplier_name": (
        "공급사",
        "업체명",
        "회사명",
        "상호",
        "견적업체",
        "제출업체",
        "supplier",
        "vendor",
    ),
    "quote_date": (
        "견적일",
        "견적일자",
        "작성일",
        "quote date",
    ),
    "project_name": (
        "공사명",
        "프로젝트명",
        "project name",
        "project",
    ),
}
DATE_PATTERNS = (
    "%Y-%m-%d",
    "%Y.%m.%d",
    "%Y/%m/%d",
    "%Y년 %m월 %d일",
)


@dataclass(frozen=True)
class ExtractedCandidate:
    field_name: str
    value_text: str
    source_kind: str
    confidence: int
    sheet: str | None = None
    page: int | None = None
    cells: str | None = None


@dataclass(frozen=True)
class FileAuditResult:
    relative_path: str
    extension: str
    acquisition_channel: str | None
    open_status: str
    content_status: str
    review_status: str
    supplier_name: str | None
    quote_date: str | None
    project_name: str | None
    source_locations: str
    confidence: int | None
    diagnostic: str | None


@dataclass(frozen=True)
class MetadataAuditReport:
    total_files: int
    opened_files: int
    failed_files: int
    unsupported_files: int
    auto_confirmed_files: int
    review_required_files: int
    supplier_confirmed_files: int
    date_confirmed_files: int
    report_file: str
    rule_version: str = AUDIT_RULE_VERSION

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def audit_quote_metadata(
    session: Session,
    *,
    quote_root: Path,
    report_path: Path,
    third_training_relative: Path = Path("3차 학습"),
) -> MetadataAuditReport:
    """Audit every third-training file and append source-backed evidence."""

    quote_root = quote_root.resolve(strict=True)
    audit_root = (quote_root / third_training_relative).resolve(strict=False)
    audit_root.relative_to(quote_root)
    if not audit_root.is_dir():
        _write_report(report_path, [])
        return MetadataAuditReport(
            total_files=0,
            opened_files=0,
            failed_files=0,
            unsupported_files=0,
            auto_confirmed_files=0,
            review_required_files=0,
            supplier_confirmed_files=0,
            date_confirmed_files=0,
            report_file=str(report_path),
        )
    variants = {
        _path_key(row.path): row
        for row in session.scalars(select(SourceVariant).order_by(SourceVariant.id))
    }
    existing_scans = {
        (row.source_path, row.input_fingerprint, row.rule_version): row
        for row in session.scalars(select(DocumentMetadataScan))
    }
    results: list[FileAuditResult] = []
    for path in sorted(
        (row for row in audit_root.rglob("*") if row.is_file()),
        key=lambda row: row.as_posix().casefold(),
    ):
        relative_to_quote = path.relative_to(quote_root).as_posix()
        relative_to_audit = path.relative_to(audit_root).as_posix()
        fingerprint = _sha256(path)
        existing = existing_scans.get(
            (relative_to_quote, fingerprint, AUDIT_RULE_VERSION)
        )
        if existing is not None:
            restored = _stored_result(existing)
            if restored is not None:
                results.append(restored)
                continue
        result, candidates = _audit_file(path, relative_to_audit)
        variant = variants.get(_path_key(relative_to_quote))
        _store_scan(
            session,
            variant,
            source_path=relative_to_quote,
            input_fingerprint=fingerprint,
            result=result,
            candidates=candidates,
        )
        results.append(result)
    session.flush()
    _write_report(report_path, results)
    return MetadataAuditReport(
        total_files=len(results),
        opened_files=sum(row.open_status == "OPENED" for row in results),
        failed_files=sum(row.open_status == "FAILED" for row in results),
        unsupported_files=sum(row.open_status == "UNSUPPORTED" for row in results),
        auto_confirmed_files=sum(row.review_status == "AUTO_CONFIRMED" for row in results),
        review_required_files=sum(row.review_status == "REVIEW_REQUIRED" for row in results),
        supplier_confirmed_files=sum(row.supplier_name is not None for row in results),
        date_confirmed_files=sum(row.quote_date is not None for row in results),
        report_file=str(report_path),
    )


def _audit_file(
    path: Path,
    relative_path: str,
) -> tuple[FileAuditResult, list[ExtractedCandidate]]:
    extension = path.suffix.lower()
    channel = _acquisition_channel(relative_path)
    if extension not in SUPPORTED_EXTENSIONS:
        return (
            FileAuditResult(
                relative_path=relative_path,
                extension=extension,
                acquisition_channel=channel,
                open_status="UNSUPPORTED",
                content_status="MANUAL_REVIEW",
                review_status="REVIEW_REQUIRED",
                supplier_name=None,
                quote_date=None,
                project_name=None,
                source_locations="",
                confidence=None,
                diagnostic="unsupported evidence format",
            ),
            [],
        )
    try:
        if extension == ".xlsx":
            candidates, has_text = _xlsx_candidates(path)
        elif extension == ".xls":
            candidates, has_text = _xls_candidates(path)
        else:
            candidates, has_text = _pdf_candidates(path)
    except Exception as exc:  # file-level audit must continue
        return (
            FileAuditResult(
                relative_path=relative_path,
                extension=extension,
                acquisition_channel=channel,
                open_status="FAILED",
                content_status="UNREADABLE",
                review_status="REVIEW_REQUIRED",
                supplier_name=None,
                quote_date=None,
                project_name=None,
                source_locations="",
                confidence=None,
                diagnostic=type(exc).__name__,
            ),
            [],
        )
    selected, ambiguous = _select_candidates(candidates)
    locations = "; ".join(
        _candidate_location(row) for row in selected.values()
    )
    review_status = (
        "AUTO_CONFIRMED"
        if {"supplier_name", "quote_date"}.issubset(selected) and not ambiguous
        else "REVIEW_REQUIRED"
    )
    return (
        FileAuditResult(
            relative_path=relative_path,
            extension=extension,
            acquisition_channel=channel,
            open_status="OPENED",
            content_status="TEXT" if has_text else "OCR_REQUIRED",
            review_status=review_status,
            supplier_name=(
                selected.get("supplier_name").value_text
                if "supplier_name" in selected else None
            ),
            quote_date=(
                selected.get("quote_date").value_text
                if "quote_date" in selected else None
            ),
            project_name=(
                selected.get("project_name").value_text
                if "project_name" in selected else None
            ),
            source_locations=locations,
            confidence=(
                min(row.confidence for row in selected.values())
                if selected else None
            ),
            diagnostic=(
                "conflicting source values" if ambiguous else None
            ),
        ),
        candidates,
    )


def _xlsx_candidates(path: Path) -> tuple[list[ExtractedCandidate], bool]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        candidates: list[ExtractedCandidate] = []
        has_text = False
        for sheet in workbook.worksheets[:20]:
            rows = [
                list(row)
                for row in sheet.iter_rows(
                    min_row=1,
                    max_row=min(sheet.max_row, 50),
                    min_col=1,
                    max_col=min(sheet.max_column, 24),
                    values_only=True,
                )
            ]
            has_text = has_text or any(
                str(value).strip()
                for row in rows for value in row if value is not None
            )
            candidates.extend(_grid_candidates(rows, sheet.title))
        return candidates, has_text
    finally:
        workbook.close()


def _xls_candidates(path: Path) -> tuple[list[ExtractedCandidate], bool]:
    workbook = xlrd.open_workbook(path, on_demand=True)
    candidates: list[ExtractedCandidate] = []
    has_text = False
    try:
        for sheet in workbook.sheets()[:20]:
            rows = [
                [sheet.cell_value(row, col) for col in range(min(sheet.ncols, 24))]
                for row in range(min(sheet.nrows, 50))
            ]
            has_text = has_text or any(
                str(value).strip()
                for row in rows for value in row if value is not None
            )
            candidates.extend(_grid_candidates(rows, sheet.name))
        return candidates, has_text
    finally:
        workbook.release_resources()


def _pdf_candidates(path: Path) -> tuple[list[ExtractedCandidate], bool]:
    reader = PdfReader(str(path))
    if reader.is_encrypted and reader.decrypt("") == 0:
        raise ValueError("encrypted pdf")
    candidates: list[ExtractedCandidate] = []
    has_text = False
    for page_number, page in enumerate(reader.pages[:5], start=1):
        text = page.extract_text() or ""
        if text.strip():
            has_text = True
        for line in text.splitlines():
            candidates.extend(_line_candidates(line, page=page_number))
    return candidates, has_text


def _grid_candidates(
    rows: list[list[object]],
    sheet: str,
) -> list[ExtractedCandidate]:
    candidates: list[ExtractedCandidate] = []
    for row_index, row in enumerate(rows, start=1):
        for col_index, raw_value in enumerate(row, start=1):
            if raw_value is None:
                continue
            text = str(raw_value).strip()
            candidates.extend(
                _line_candidates(
                    text,
                    sheet=sheet,
                    cells=f"{get_column_letter(col_index)}{row_index}",
                )
            )
            field_name = _label_field(text)
            if field_name is None:
                continue
            adjacent = _next_value(row, col_index)
            value = _clean_candidate(field_name, adjacent)
            if value is None:
                continue
            end_col = min(len(row), col_index + 2)
            candidates.append(
                ExtractedCandidate(
                    field_name=field_name,
                    value_text=value,
                    source_kind="LABELED_CELL",
                    confidence=95,
                    sheet=sheet,
                    cells=(
                        f"{get_column_letter(col_index)}{row_index}:"
                        f"{get_column_letter(end_col)}{row_index}"
                    ),
                )
            )
    return candidates


def _line_candidates(
    line: str,
    *,
    sheet: str | None = None,
    page: int | None = None,
    cells: str | None = None,
) -> list[ExtractedCandidate]:
    compact = " ".join(line.split())
    result: list[ExtractedCandidate] = []
    for field_name, aliases in LABELS.items():
        for alias in aliases:
            match = re.search(
                rf"(?i)(?:^|\s){re.escape(alias)}\s*[:：]\s*(.+)$",
                compact,
            )
            if match is None:
                continue
            value = _clean_candidate(field_name, match.group(1))
            if value is not None:
                result.append(
                    ExtractedCandidate(
                        field_name=field_name,
                        value_text=value,
                        source_kind="LABELED_TEXT",
                        confidence=95,
                        sheet=sheet,
                        page=page,
                        cells=cells,
                    )
                )
            break
    return result


def _label_field(value: str) -> str | None:
    normalized = re.sub(r"[\s:：()\[\]]+", "", value).casefold()
    for field_name, aliases in LABELS.items():
        if normalized in {
            re.sub(r"[\s:：()\[\]]+", "", alias).casefold()
            for alias in aliases
        }:
            return field_name
    return None


def _next_value(row: list[object], one_based_col: int) -> str:
    for value in row[one_based_col: one_based_col + 2]:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _clean_candidate(field_name: str, value: str) -> str | None:
    cleaned = " ".join(str(value).split()).strip(" :：")
    if not cleaned or len(cleaned) > 300:
        return None
    if cleaned.casefold() in PLACEHOLDER_VALUES:
        return None
    if field_name == "supplier_name":
        if cleaned.casefold() in {"aone", "바츠"}:
            return None
        if not re.search(r"[A-Za-z가-힣]", cleaned):
            return None
    if field_name == "quote_date":
        parsed = _parse_date(cleaned)
        if parsed is None or parsed.isoformat() in PLACEHOLDER_DATES:
            return None
        return parsed.isoformat()
    return cleaned


def _parse_date(value: str) -> date | None:
    normalized = value.strip()
    for pattern in DATE_PATTERNS:
        try:
            return datetime.strptime(normalized, pattern).date()
        except ValueError:
            continue
    match = re.search(r"(?<!\d)(20\d{2})[./-]?(\d{1,2})[./-]?(\d{1,2})(?!\d)", normalized)
    if match is None:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def _select_candidates(
    candidates: Iterable[ExtractedCandidate],
) -> tuple[dict[str, ExtractedCandidate], bool]:
    by_field: dict[str, list[ExtractedCandidate]] = {}
    for candidate in candidates:
        by_field.setdefault(candidate.field_name, []).append(candidate)
    selected: dict[str, ExtractedCandidate] = {}
    ambiguous = False
    for field_name, rows in by_field.items():
        values = {row.value_text.casefold() for row in rows}
        if len(values) != 1:
            ambiguous = True
            continue
        selected[field_name] = sorted(
            rows,
            key=lambda row: (-row.confidence, row.page or 0, row.cells or ""),
        )[0]
    return selected, ambiguous


def _store_scan(
    session: Session,
    variant: SourceVariant | None,
    *,
    source_path: str,
    input_fingerprint: str,
    result: FileAuditResult,
    candidates: list[ExtractedCandidate],
) -> DocumentMetadataScan:
    existing = session.scalar(
        select(DocumentMetadataScan).where(
            DocumentMetadataScan.source_path == source_path,
            DocumentMetadataScan.input_fingerprint == input_fingerprint,
            DocumentMetadataScan.rule_version == AUDIT_RULE_VERSION,
        )
    )
    if existing is not None:
        return existing
    diagnostics = {
        "diagnostic": result.diagnostic,
        "relative_path": result.relative_path,
        "result": asdict(result),
    }
    scan = DocumentMetadataScan(
        source_variant_id=None if variant is None else variant.id,
        source_path=source_path,
        rule_version=AUDIT_RULE_VERSION,
        input_fingerprint=input_fingerprint,
        open_status=result.open_status,
        content_status=result.content_status,
        review_status=result.review_status,
        acquisition_channel=result.acquisition_channel,
        diagnostics_json=_json(diagnostics),
    )
    session.add(scan)
    session.flush()
    selected, ambiguous = _select_candidates(candidates)
    candidate_rows: list[DocumentMetadataCandidate] = []
    for candidate in candidates:
        accepted = (
            not ambiguous
            and selected.get(candidate.field_name) == candidate
            and candidate.confidence >= 90
        )
        evidence = {
            "sheet": candidate.sheet,
            "page": candidate.page,
            "cells": candidate.cells,
            "source_kind": candidate.source_kind,
        }
        fingerprint = hashlib.sha256(
            _json(
                {
                    "field": candidate.field_name,
                    "value": candidate.value_text,
                    **evidence,
                }
            ).encode("utf-8")
        ).hexdigest()
        row = DocumentMetadataCandidate(
            scan_id=scan.id,
            field_name=candidate.field_name,
            value_text=candidate.value_text,
            source_kind=candidate.source_kind,
            confidence=candidate.confidence,
            status="AUTO_ACCEPTED" if accepted else "REVIEW_REQUIRED",
            source_sheet=candidate.sheet,
            source_page=candidate.page,
            source_cells=candidate.cells,
            evidence_json=_json(evidence),
            candidate_fingerprint=fingerprint,
        )
        session.add(row)
        candidate_rows.append(row)
    session.flush()
    # A standalone ingest can own parsed rows even though it was not marked as
    # the preferred member of a protected/unlocked source group.  In either
    # case the metadata belongs to the exact variant that supplied the rows.
    if variant is not None and (
        variant.selected_for_parsing_at_ingest or bool(variant.raw_items)
    ) and not ambiguous:
        _append_auto_metadata(session, variant, candidate_rows)
    return scan


def _append_auto_metadata(
    session: Session,
    variant: SourceVariant,
    candidates: list[DocumentMetadataCandidate],
) -> None:
    accepted = {
        row.field_name: row
        for row in candidates
        if row.status == "AUTO_ACCEPTED"
    }
    if not accepted:
        return
    current = current_document_metadata(session, variant.document_id)
    if (
        current is not None
        and current.decided_by not in AUTOMATED_METADATA_ACTORS
    ):
        return
    supplier = accepted.get("supplier_name")
    quote_date = accepted.get("quote_date")
    project = accepted.get("project_name")
    values = {
        "supplier_name": (
            supplier.value_text
            if supplier
            else (current.supplier_name if current is not None else None)
        ),
        "quote_date": (
            date.fromisoformat(quote_date.value_text)
            if quote_date
            else (current.quote_date if current is not None else None)
        ),
        "project_name": (
            project.value_text
            if project
            else (current.project_name if current is not None else None)
        ),
    }
    if current is not None and all(
        getattr(current, field) == value for field, value in values.items()
    ):
        return
    try:
        prior_evidence = (
            json.loads(current.evidence_json)
            if current is not None and current.evidence_json
            else {}
        )
    except (json.JSONDecodeError, TypeError):
        prior_evidence = {}
    evidence = dict(prior_evidence) if isinstance(prior_evidence, dict) else {}
    evidence.update({
        field: {
            "candidate_id": row.id,
            "source_kind": row.source_kind,
            "confidence": row.confidence,
            "sheet": row.source_sheet,
            "page": row.source_page,
            "cells": row.source_cells,
        }
        for field, row in accepted.items()
    })
    session.add(
        DocumentMetadataVersion(
            source_document_id=variant.document_id,
            version_number=1 if current is None else current.version_number + 1,
            **values,
            decided_by="metadata-audit-v2",
            reason_detail="원본 견적서의 명시된 항목에서 자동 확인",
            evidence_json=_json(evidence),
        )
    )


def _write_report(path: Path, rows: list[FileAuditResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(asdict(rows[0]).keys()) if rows else ["relative_path"])
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    temporary.replace(path)


def _candidate_location(candidate: ExtractedCandidate) -> str:
    if candidate.sheet:
        return f"{candidate.field_name}:{candidate.sheet}!{candidate.cells or '?'}"
    if candidate.page:
        return f"{candidate.field_name}:PDF {candidate.page}쪽"
    return candidate.field_name


def _acquisition_channel(path: str) -> str | None:
    normalized = path.casefold()
    if "aone" in normalized:
        return "AONE"
    if "바츠" in normalized:
        return "BATS"
    return None


def _path_key(path: str) -> str:
    return path.replace("\\", "/").casefold()


def _json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _stored_result(scan: DocumentMetadataScan) -> FileAuditResult | None:
    try:
        payload = json.loads(scan.diagnostics_json)
        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        return FileAuditResult(**result)
    except (json.JSONDecodeError, TypeError, KeyError, ValueError):
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
