"""Source-document inventory and configured-folder ingestion."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.cleansing.models import CleanDecision, CleanStatus
from app.core.config import settings
from app.db.session import get_session
from app.documents.models import SourceDocument, SourceVariant
from app.ingestion.corpus import ingest_corpus
from app.ingestion.service import preferred_variant_for
from app.quotes.models import RawQuoteItem


router = APIRouter()


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
    report = ingest_corpus(session, settings.quote_path)
    return {
        "files_found": report.preflight.physical_files,
        "documents_found": report.preflight.logical_documents,
        "documents_succeeded": (
            report.documents_ingested + report.documents_unchanged
        ),
        "documents_failed": report.documents_failed,
        "variants_created": report.variants_created,
        "raw_items_created": report.raw_items_created,
        "decisions_created": (
            report.base_decisions_created
            + report.outlier_decisions_created
        ),
        "failures": [
            {
                "logical_name": failure.logical_name,
                "error_code": failure.error_code,
                "detail": failure.detail,
            }
            for failure in report.failures
        ],
    }


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
