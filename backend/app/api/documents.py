"""Source-document inventory and configured-folder ingestion."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.cleansing.models import CleanDecision, CleanStatus
from app.cleansing.service import apply_rules
from app.core.config import settings
from app.db.session import get_session
from app.documents.models import SourceDocument, SourceVariant
from app.ingestion.service import (
    ingest_group,
    parsing_variant_for,
    preferred_variant_for,
)
from app.ingestion.source_selector import build_source_groups
from app.quotes.models import RawQuoteItem


router = APIRouter()
_SUPPORTED_EXTENSIONS = {".xlsx", ".xls", ".pdf"}


@router.get("")
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
    current_by_item = _current_decisions(session)
    return {
        "items": [
            _document_item(document, current_by_item)
            for document in documents
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/scan")
def scan_documents(
    session: Session = Depends(get_session),
) -> dict[str, object]:
    quote_root = settings.quote_path.resolve(strict=False)
    paths = _scan_supported_files(quote_root)
    groups = build_source_groups(paths, root=quote_root) if paths else []
    result: dict[str, object] = {
        "files_found": len(paths),
        "documents_found": len(groups),
        "documents_succeeded": 0,
        "documents_failed": 0,
        "variants_created": 0,
        "raw_items_created": 0,
        "decisions_created": 0,
        "failures": [],
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
        except Exception as exc:
            session.rollback()
            result["documents_failed"] += 1
            result["failures"].append(
                {
                    "logical_name": group.logical_name,
                    "error_type": type(exc).__name__,
                    "detail": _safe_error_detail(exc, quote_root),
                }
            )
            continue

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


def _safe_error_detail(exc: Exception, root: Path) -> str:
    detail = str(exc) or type(exc).__name__
    resolved_root = root.resolve(strict=False)
    for root_text in {
        str(root),
        root.as_posix(),
        str(resolved_root),
        resolved_root.as_posix(),
    }:
        if root_text:
            detail = detail.replace(root_text, "<quote-root>")
    return detail


def _count(session: Session, column: object) -> int:
    return session.scalar(select(func.count(column))) or 0


def _current_decisions(session: Session) -> dict[int, CleanDecision]:
    latest_ids = (
        select(
            CleanDecision.raw_item_id,
            func.max(CleanDecision.id).label("decision_id"),
        )
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
