"""Typed, read-only quote-analysis API."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.analysis.service import (
    AnalysisNotFound,
    Assessment,
    DocumentAnalysis,
    MatchStatus,
    analyze_document,
    list_analysis_documents,
)
from app.api.catalog import get_candidate_embedding_runtime
from app.catalog.service import CandidateEmbeddingRuntime
from app.core.config import settings
from app.db.session import get_session


router = APIRouter()


class AnalysisDocumentSummaryResponse(BaseModel):
    id: int
    logical_name: str
    raw_item_count: int
    included_count: int
    excluded_count: int
    review_required_count: int
    undecided_count: int
    analysis_ready: bool


class AnalysisDocumentListResponse(BaseModel):
    items: list[AnalysisDocumentSummaryResponse]
    total: int
    limit: int
    offset: int


class AnalysisDocumentIdentityResponse(BaseModel):
    id: int
    logical_name: str


class AnalysisSourceResponse(BaseModel):
    document_id: int
    logical_name: str
    variant_id: int
    path: str
    sha256: str
    sheet: str | None
    page: int | None
    row: int | None
    cells: str | None
    parser_name: str
    parser_version: str


class AnalysisCandidateResponse(BaseModel):
    standard_item_id: int
    standard_item_version_id: int
    canonical_name: str
    canonical_spec: str | None
    canonical_unit: str | None
    final_score: Decimal
    method: str
    matched_tokens: list[str]


class AnalysisLineResponse(BaseModel):
    raw_item_id: int
    item_name: str | None
    spec: str | None
    unit: str | None
    quote_unit_price: Decimal | None
    match_status: MatchStatus
    assessment: Assessment
    reference_price: Decimal | None
    minimum_price: Decimal | None
    average_price: Decimal | None
    maximum_price: Decimal | None
    variance_amount: Decimal | None
    variance_percent: Decimal | None
    clean_decision_id: int | None
    membership_decision_id: int | None
    standard_item_id: int | None
    standard_item_version_id: int | None
    standard_price_version_id: int | None
    standard_price_item_version_id: int | None
    market_price_lookup_required: bool
    market_price_lookup_status: Literal[
        "NOT_REQUIRED", "FUTURE_MARKET_LOOKUP"
    ]
    candidates: list[AnalysisCandidateResponse]
    source: AnalysisSourceResponse


class DocumentAnalysisResponse(BaseModel):
    document: AnalysisDocumentIdentityResponse
    lines: list[AnalysisLineResponse]
    next_cursor: int | None
    limit: int


class CandidateRefreshResponse(DocumentAnalysisResponse):
    refreshed_candidate_rows: int
    membership_rows_created: Literal[0]


@router.get("/documents", response_model=AnalysisDocumentListResponse)
def get_analysis_documents(
    session: Session = Depends(get_session),
    *,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, object]:
    page = list_analysis_documents(session, limit=limit, offset=offset)
    return {
        "items": page.items,
        "total": page.total,
        "limit": page.limit,
        "offset": page.offset,
    }


@router.get(
    "/documents/{document_id}",
    response_model=DocumentAnalysisResponse,
)
def get_document_analysis(
    document_id: int,
    session: Session = Depends(get_session),
    runtime: CandidateEmbeddingRuntime = Depends(
        get_candidate_embedding_runtime
    ),
    *,
    after_id: int | None = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=100),
    match_status: MatchStatus | None = Query(None),
    assessment: Assessment | None = Query(None),
) -> dict[str, object]:
    result = _analyze(
        session,
        document_id,
        runtime=runtime,
        after_id=after_id,
        limit=limit,
        match_status=match_status,
        assessment=assessment,
    )
    return _analysis_payload(result)


@router.post(
    "/documents/{document_id}/refresh-candidates",
    response_model=CandidateRefreshResponse,
)
def post_refresh_candidates(
    document_id: int,
    session: Session = Depends(get_session),
    runtime: CandidateEmbeddingRuntime = Depends(
        get_candidate_embedding_runtime
    ),
    *,
    after_id: int | None = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> dict[str, object]:
    result = _analyze(
        session,
        document_id,
        runtime=runtime,
        after_id=after_id,
        limit=limit,
        match_status={"CANDIDATE", "NO_MATCH"},
    )
    return {
        **_analysis_payload(result),
        "refreshed_candidate_rows": len(result.lines),
        "membership_rows_created": 0,
    }


def _analyze(
    session: Session,
    document_id: int,
    *,
    runtime: CandidateEmbeddingRuntime,
    after_id: int | None,
    limit: int,
    match_status: MatchStatus | set[MatchStatus] | None,
    assessment: Assessment | None = None,
) -> DocumentAnalysis:
    try:
        return analyze_document(
            session,
            document_id,
            after_id=after_id,
            limit=limit,
            match_status=match_status,
            assessment=assessment,
            review_percent=settings.price_variance_review_percent,
            high_percent=settings.price_variance_high_percent,
            embedding_runtime=runtime,
        )
    except AnalysisNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _analysis_payload(result: DocumentAnalysis) -> dict[str, object]:
    return {
        "document": {
            "id": result.document_id,
            "logical_name": result.logical_name,
        },
        "lines": result.lines,
        "next_cursor": result.next_cursor,
        "limit": result.limit,
    }
