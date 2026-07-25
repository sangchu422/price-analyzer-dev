"""Source-document inventory and configured-folder ingestion."""

from __future__ import annotations

import ntpath
from datetime import datetime
from pathlib import Path
from zipfile import BadZipFile

from fastapi import APIRouter, Depends, Query
from openpyxl.utils.exceptions import InvalidFileException
from pypdf.errors import PdfReadError
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from xlrd.biffh import XLRDError

from app.cleansing.models import CleanDecision, CleanStatus
from app.cleansing.service import apply_rules
from app.core.config import settings
from app.db.session import get_session
from app.documents.models import SourceDocument, SourceVariant
from app.ingestion.service import (
    SourceEvidenceConflictError,
    SourceFileChangedError,
    UnsupportedQuoteLayoutError,
    ingest_group,
    parsing_variant_for,
    preferred_variant_for,
)
from app.ingestion.source_selector import SourceGroup, build_source_groups
from app.quotes.models import RawQuoteItem


router = APIRouter()
_SUPPORTED_EXTENSIONS = {".xlsx", ".xls", ".pdf"}


class VariantResponse(BaseModel):
    id: int
    path: str
    sha256: str
    extension: str
    security_state: str
    selected_for_parsing_at_ingest: bool
    registered_at: datetime
    raw_item_count: int


class DocumentCountsResponse(BaseModel):
    raw_items: int
    INCLUDED: int
    EXCLUDED: int
    REVIEW_REQUIRED: int
    UNDECIDED: int


class DocumentResponse(BaseModel):
    id: int
    logical_name: str
    created_at: datetime
    variants: list[VariantResponse]
    preferred_variant: VariantResponse
    counts: DocumentCountsResponse


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    limit: int
    offset: int


class ScanFailureResponse(BaseModel):
    logical_name: str
    error_code: str
    detail: str


class ScanResponse(BaseModel):
    files_found: int
    documents_found: int
    documents_succeeded: int
    documents_failed: int
    variants_created: int
    raw_items_created: int
    decisions_created: int
    failures: list[ScanFailureResponse]


@router.get("", response_model=DocumentListResponse)
def list_documents(
    session: Session = Depends(get_session),
    *,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, object]:
    total = session.scalar(select(func.count(SourceDocument.id))) or 0
    documents = session.scalars(
        select(SourceDocument)
        .options(
            selectinload(SourceDocument.variants).selectinload(
                SourceVariant.raw_items
            )
        )
        .order_by(SourceDocument.logical_name, SourceDocument.id)
        .offset(offset)
        .limit(limit)
    ).all()
    document_ids = [document.id for document in documents]
    current_by_item = _current_decisions(session, document_ids)
    return {
        "items": [
            _document_item(document, current_by_item)
            for document in documents
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/scan", response_model=ScanResponse)
def scan_documents(
    session: Session = Depends(get_session),
) -> dict[str, object]:
    quote_root = settings.quote_path.resolve(strict=False)
    paths = _scan_supported_files(quote_root)
    groups, preflight_failures = _prepare_source_groups(paths, quote_root)
    result: dict[str, object] = {
        "files_found": len(paths),
        "documents_found": len(groups) + len(preflight_failures),
        "documents_succeeded": 0,
        "documents_failed": len(preflight_failures),
        "variants_created": 0,
        "raw_items_created": 0,
        "decisions_created": 0,
        "failures": list(preflight_failures),
    }

    for group in groups:
        before_variants = _count(session, SourceVariant.id)
        before_rows = _count(session, RawQuoteItem.id)
        before_decisions = _count(session, CleanDecision.id)
        try:
            selected = ingest_group(session, group, root=quote_root)
            parsing_variant = parsing_variant_for(session, selected)
            for raw_item in sorted(
                parsing_variant.raw_items,
                key=lambda item: item.id,
            ):
                apply_rules(session, raw_item)
            session.commit()
        except _EXPECTED_INGESTION_ERRORS as exc:
            session.rollback()
            result["documents_failed"] += 1
            result["failures"].append(
                _ingestion_failure(group.logical_name, exc)
            )
            continue
        except Exception:
            session.rollback()
            raise

        result["documents_succeeded"] += 1
        result["variants_created"] += (
            _count(session, SourceVariant.id) - before_variants
        )
        result["raw_items_created"] += (
            _count(session, RawQuoteItem.id) - before_rows
        )
        result["decisions_created"] += (
            _count(session, CleanDecision.id) - before_decisions
        )
    return result


def _scan_supported_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in _SUPPORTED_EXTENSIONS
            and not path.name.startswith("~$")
        ),
        key=lambda path: path.relative_to(root).as_posix().casefold(),
    )


_EXPECTED_INGESTION_ERRORS = (
    UnsupportedQuoteLayoutError,
    SourceFileChangedError,
    SourceEvidenceConflictError,
    BadZipFile,
    InvalidFileException,
    PdfReadError,
    XLRDError,
    OSError,
)


def _prepare_source_groups(
    paths: list[Path],
    root: Path,
) -> tuple[list[SourceGroup], list[dict[str, str]]]:
    resolved_root = root.resolve(strict=False)
    grouped_paths: dict[str, list[Path]] = {}
    failures: list[dict[str, str]] = []
    for path in paths:
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            failures.append(
                _preflight_failure(
                    path,
                    "INVALID_SOURCE_PATH",
                    "source path could not be resolved",
                )
            )
            continue
        try:
            resolved.relative_to(resolved_root)
        except ValueError:
            failures.append(
                _preflight_failure(
                    path,
                    "PATH_OUTSIDE_ROOT",
                    "source path resolves outside configured quote root",
                )
            )
            continue
        try:
            candidate_group = build_source_groups([path], root=root)[0]
        except (OSError, ValueError):
            failures.append(
                _preflight_failure(
                    path,
                    "INVALID_SOURCE_PATH",
                    "source path could not be grouped",
                )
            )
            continue
        key = ntpath.normcase(candidate_group.logical_name)
        grouped_paths.setdefault(key, []).append(path)

    groups = [
        build_source_groups(group_paths, root=root)[0]
        for _, group_paths in sorted(grouped_paths.items())
    ]
    return groups, failures


def _preflight_failure(
    path: Path,
    error_code: str,
    detail: str,
) -> dict[str, str]:
    return {
        "logical_name": path.stem,
        "error_code": error_code,
        "detail": detail,
    }


def _ingestion_failure(
    logical_name: str,
    exc: Exception,
) -> dict[str, str]:
    if isinstance(exc, UnsupportedQuoteLayoutError):
        code = "UNSUPPORTED_LAYOUT"
        detail = "source layout is not currently supported"
    elif isinstance(exc, SourceFileChangedError):
        code = "SOURCE_CHANGED"
        detail = "source file changed during ingestion"
    elif isinstance(
        exc,
        (BadZipFile, InvalidFileException, PdfReadError, XLRDError),
    ):
        code = "UNREADABLE_SOURCE"
        detail = "source file could not be read"
    elif isinstance(exc, OSError):
        code = "SOURCE_IO_ERROR"
        detail = "source file could not be accessed"
    else:
        code = "INVALID_SOURCE_EVIDENCE"
        detail = "source evidence is invalid or changed"
    return {
        "logical_name": logical_name,
        "error_code": code,
        "detail": detail,
    }


def _count(session: Session, column: object) -> int:
    return session.scalar(select(func.count(column))) or 0


def _current_decisions(
    session: Session,
    document_ids: list[int],
) -> dict[int, CleanDecision]:
    if not document_ids:
        return {}
    latest_ids = (
        select(
            CleanDecision.raw_item_id,
            func.max(CleanDecision.id).label("decision_id"),
        )
        .join(
            RawQuoteItem,
            RawQuoteItem.id == CleanDecision.raw_item_id,
        )
        .join(
            SourceVariant,
            SourceVariant.id == RawQuoteItem.source_variant_id,
        )
        .where(SourceVariant.document_id.in_(document_ids))
        .group_by(CleanDecision.raw_item_id)
        .subquery()
    )
    return {
        decision.raw_item_id: decision
        for decision in session.scalars(
            select(CleanDecision).join(
                latest_ids,
                CleanDecision.id == latest_ids.c.decision_id,
            )
        )
    }


def _document_item(
    document: SourceDocument,
    current_by_item: dict[int, CleanDecision],
) -> dict[str, object]:
    variants = sorted(document.variants, key=lambda variant: variant.path)
    preferred = preferred_variant_for(document)
    raw_items = [
        raw_item
        for variant in variants
        for raw_item in variant.raw_items
    ]
    counts = {status.value: 0 for status in CleanStatus}
    undecided = 0
    for raw_item in raw_items:
        decision = current_by_item.get(raw_item.id)
        if decision is None:
            undecided += 1
        else:
            counts[decision.status.value] += 1
    return {
        "id": document.id,
        "logical_name": document.logical_name,
        "created_at": document.created_at.isoformat(),
        "variants": [_variant_item(variant) for variant in variants],
        "preferred_variant": _variant_item(preferred),
        "counts": {
            "raw_items": len(raw_items),
            **counts,
            "UNDECIDED": undecided,
        },
    }


def _variant_item(variant: SourceVariant) -> dict[str, object]:
    return {
        "id": variant.id,
        "path": variant.path,
        "sha256": variant.sha256,
        "extension": variant.extension,
        "security_state": variant.security_state,
        "selected_for_parsing_at_ingest": (
            variant.selected_for_parsing_at_ingest
        ),
        "registered_at": variant.registered_at.isoformat(),
        "raw_item_count": len(variant.raw_items),
    }
