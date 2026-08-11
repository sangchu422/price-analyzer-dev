"""Typed, read-only quote-analysis API."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.service import (
    AnalysisNotFound,
    Assessment,
    DocumentAnalysis,
    MatchStatus,
    analyze_document,
    list_analysis_documents,
)
from app.analysis.target_price import (
    KOSIS_CLASSIFIER_CODE,
    KOSIS_ITEM_ID,
    KOSIS_ORG_ID,
    KOSIS_SERIES_URL,
    KOSIS_TABLE_ID,
    AnalysisRunResult,
    InflationSeriesUnavailable,
    create_analysis_run,
    latest_ppi_series,
    sync_ppi_series,
)
from app.analysis.models import (
    QuoteAnalysisLineResult,
    QuoteAnalysisRun,
    QuoteAnalysisTargetEvidence,
)
from app.api.catalog import get_candidate_embedding_runtime
from app.catalog.service import CandidateEmbeddingRuntime
from app.core.config import settings
from app.db.session import get_session
from app.documents.models import SourceDocument
from app.standard_database.models import (
    QuoteDocumentPurpose,
    QuoteDocumentRole,
)


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
    next_cursor: int | None


class AnalysisDocumentIdentityResponse(BaseModel):
    id: int
    logical_name: str
    display_name: str
    purpose: QuoteDocumentPurpose


class PricePolicyResponse(BaseModel):
    within_percent: Decimal
    high_low_percent: Decimal
    description: str


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
    embedding_status: Literal[
        "DISABLED",
        "UNAVAILABLE",
        "AVAILABLE",
        "MOCK_ONLY",
    ]
    embedding_model: str | None


class AnalysisLineResponse(BaseModel):
    raw_item_id: int
    item_name: str | None
    spec: str | None
    spec_source_status: str
    unit: str | None
    quantity: Decimal | None
    quote_unit_price: Decimal | None
    quote_amount: Decimal | None
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
    canonical_name: str | None
    canonical_spec: str | None
    canonical_unit: str | None
    standard_price_version_id: int | None
    standard_price_item_version_id: int | None
    standard_observation_count: int | None
    evidence_quality: str | None
    market_price_lookup_required: bool
    market_price_lookup_status: Literal[
        "NOT_REQUIRED", "FUTURE_MARKET_LOOKUP"
    ]
    candidates: list[AnalysisCandidateResponse]
    source: AnalysisSourceResponse


class DocumentAnalysisResponse(BaseModel):
    document: AnalysisDocumentIdentityResponse
    price_policy: PricePolicyResponse
    lines: list[AnalysisLineResponse]
    next_cursor: int | None
    limit: int


class CandidateRefreshResponse(DocumentAnalysisResponse):
    refreshed_candidate_rows: int
    membership_rows_created: Literal[0]


class AnalysisRunCreateRequest(BaseModel):
    created_by: str = Field(min_length=1, max_length=100)
    review_percent: Decimal = Field(default=Decimal("10"), ge=0)
    high_percent: Decimal = Field(default=Decimal("20"), ge=0)


class TargetEvidenceResponse(BaseModel):
    raw_item_id: int
    metadata_version_id: int
    source_document_id: int
    source_variant_id: int
    source_logical_name: str
    source_sheet: str | None
    source_page: int | None
    source_row: int | None
    source_cells: str | None
    quote_date: str
    source_period: str
    original_unit_price: Decimal
    source_index_value: Decimal
    target_index_value: Decimal
    adjusted_unit_price: Decimal


class TargetLineResponse(BaseModel):
    raw_item_id: int
    status: Literal[
        "AVAILABLE",
        "DATE_UNAVAILABLE",
        "INDEX_UNAVAILABLE",
        "MARKET_REFERENCE_REQUIRED",
        "NOT_APPLICABLE",
    ]
    target_unit_price: Decimal | None
    target_amount: Decimal | None
    variance_amount: Decimal | None
    variance_percent: Decimal | None
    used_observation_count: int
    excluded_observation_count: int
    reason: str
    evidence: list[TargetEvidenceResponse]


class InflationSeriesResponse(BaseModel):
    available: bool
    sync_run_id: int | None
    latest_period: str | None
    latest_value: Decimal | None
    source_last_changed: str | None
    point_count: int
    org_id: str
    table_id: str
    item_id: str
    classifier_code: str
    unit: str
    source_url: str


class AnalysisRunResponse(DocumentAnalysisResponse):
    run_id: int
    target_period: str | None
    target_index_value: Decimal | None
    inflation_source_url: str
    inflation_source_last_changed: str | None
    quote_total_amount: Decimal | None
    target_total_amount: Decimal | None
    target_available_count: int
    target_unavailable_count: int
    target_lines: list[TargetLineResponse]


class StoredAnalysisRunResponse(BaseModel):
    run_id: int
    document_id: int
    created_by: str
    created_at: str
    review_percent: Decimal
    high_percent: Decimal
    target_period: str | None
    target_index_value: Decimal | None
    quote_total_amount: Decimal | None
    target_total_amount: Decimal | None
    target_available_count: int
    target_unavailable_count: int
    target_lines: list[TargetLineResponse]


@router.get("/documents", response_model=AnalysisDocumentListResponse)
def get_analysis_documents(
    session: Session = Depends(get_session),
    *,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    after_id: int | None = Query(None, ge=0),
) -> dict[str, object]:
    page = list_analysis_documents(
        session,
        limit=limit,
        offset=offset,
        after_id=after_id,
    )
    return {
        "items": page.items,
        "total": page.total,
        "limit": page.limit,
        "offset": page.offset,
        "next_cursor": page.next_cursor,
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
    "/documents/{document_id}/runs",
    response_model=AnalysisRunResponse,
)
def post_analysis_run(
    document_id: int,
    body: AnalysisRunCreateRequest,
    session: Session = Depends(get_session),
    runtime: CandidateEmbeddingRuntime = Depends(get_candidate_embedding_runtime),
) -> dict[str, object]:
    _require_incoming_role(session, document_id)
    if body.high_percent < body.review_percent:
        raise HTTPException(
            status_code=422,
            detail="고가·저가 기준은 적정 범위보다 크거나 같아야 합니다.",
        )
    try:
        result = create_analysis_run(
            session,
            document_id,
            created_by=body.created_by,
            review_percent=body.review_percent,
            high_percent=body.high_percent,
            embedding_runtime=runtime,
        )
        session.commit()
    except (AnalysisNotFound, ValueError) as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _analysis_run_payload(result, body.review_percent, body.high_percent)


@router.get("/runs/{run_id}", response_model=StoredAnalysisRunResponse)
def get_analysis_run(
    run_id: int,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    run = session.get(QuoteAnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="분석 실행 이력을 찾을 수 없습니다.")
    lines = list(
        session.scalars(
            select(QuoteAnalysisLineResult)
            .where(QuoteAnalysisLineResult.analysis_run_id == run.id)
            .order_by(QuoteAnalysisLineResult.raw_item_id)
        )
    )
    evidence_rows = list(
        session.scalars(
            select(QuoteAnalysisTargetEvidence)
            .where(
                QuoteAnalysisTargetEvidence.line_result_id.in_(
                    [line.id for line in lines]
                )
            )
            .order_by(
                QuoteAnalysisTargetEvidence.line_result_id,
                QuoteAnalysisTargetEvidence.raw_item_id,
            )
        )
    ) if lines else []
    evidence_by_line: dict[int, list[dict[str, object]]] = {}
    for evidence in evidence_rows:
        evidence_by_line.setdefault(evidence.line_result_id, []).append(
            {
                "raw_item_id": evidence.raw_item_id,
                "metadata_version_id": evidence.metadata_version_id,
                "source_document_id": evidence.source_document_id,
                "source_variant_id": evidence.source_variant_id,
                "source_logical_name": evidence.source_logical_name,
                "source_sheet": evidence.source_sheet,
                "source_page": evidence.source_page,
                "source_row": evidence.source_row,
                "source_cells": evidence.source_cells,
                "quote_date": evidence.quote_date.isoformat(),
                "source_period": evidence.source_period,
                "original_unit_price": evidence.original_unit_price,
                "source_index_value": evidence.source_index_value,
                "target_index_value": evidence.target_index_value,
                "adjusted_unit_price": evidence.adjusted_unit_price,
            }
        )
    return {
        "run_id": run.id,
        "document_id": run.document_id,
        "created_by": run.created_by,
        "created_at": run.created_at.isoformat(),
        "review_percent": run.review_percent,
        "high_percent": run.high_percent,
        "target_period": run.target_period,
        "target_index_value": run.target_index_value,
        "quote_total_amount": run.quote_total_amount,
        "target_total_amount": run.target_total_amount,
        "target_available_count": run.target_available_count,
        "target_unavailable_count": run.target_unavailable_count,
        "target_lines": [
            {
                "raw_item_id": line.raw_item_id,
                "status": line.target_status,
                "target_unit_price": line.target_unit_price,
                "target_amount": line.target_amount,
                "variance_amount": line.target_variance_amount,
                "variance_percent": line.target_variance_percent,
                "used_observation_count": line.target_used_observation_count,
                "excluded_observation_count": line.target_excluded_observation_count,
                "reason": line.target_reason,
                "evidence": evidence_by_line.get(line.id, []),
            }
            for line in lines
        ],
    }


@router.get("/inflation/series/ppi-all", response_model=InflationSeriesResponse)
def get_ppi_series(session: Session = Depends(get_session)) -> dict[str, object]:
    run, points = latest_ppi_series(session)
    return _inflation_payload(run, points)


@router.post("/inflation/series/ppi-all/sync", response_model=InflationSeriesResponse)
def post_ppi_sync(session: Session = Depends(get_session)) -> dict[str, object]:
    try:
        run = sync_ppi_series(session, settings)
        session.commit()
    except InflationSeriesUnavailable as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    _, points = latest_ppi_series(session)
    return _inflation_payload(run, points)


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
    _require_incoming_role(session, document_id)
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
            deterministic_exact_match=True,
        )
    except AnalysisNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _analysis_payload(result: DocumentAnalysis) -> dict[str, object]:
    return {
        "document": {
            "id": result.document_id,
            "logical_name": result.logical_name,
            "display_name": result.logical_name.replace("\\", "/").split("/")[-1],
            "purpose": QuoteDocumentPurpose.INCOMING_BID,
        },
        "price_policy": {
            "within_percent": settings.price_variance_review_percent,
            "high_low_percent": settings.price_variance_high_percent,
            "description": "표준 중앙값 대비 ±10% 이내 적정, ±10~20% 주의, ±20% 초과 고가·저가",
        },
        "lines": result.lines,
        "next_cursor": result.next_cursor,
        "limit": result.limit,
    }


def _analysis_run_payload(
    result: AnalysisRunResult,
    review_percent: Decimal,
    high_percent: Decimal,
) -> dict[str, object]:
    payload = _analysis_payload(result.analysis)
    payload["price_policy"] = {
        "within_percent": review_percent,
        "high_low_percent": high_percent,
        "description": (
            f"표준 중앙값 대비 ±{review_percent}% 이내 적정, "
            f"±{review_percent}~{high_percent}% 주의, "
            f"±{high_percent}% 초과 고가·저가"
        ),
    }
    payload.update(
        {
            "run_id": result.run_id,
            "target_period": result.target_period,
            "target_index_value": result.target_index_value,
            "inflation_source_url": result.inflation_source_url,
            "inflation_source_last_changed": (
                None
                if result.inflation_source_last_changed is None
                else result.inflation_source_last_changed.isoformat()
            ),
            "quote_total_amount": result.quote_total_amount,
            "target_total_amount": result.target_total_amount,
            "target_available_count": result.target_available_count,
            "target_unavailable_count": result.target_unavailable_count,
            "target_lines": [
                {
                    **{
                        key: value
                        for key, value in line.__dict__.items()
                        if key != "evidence"
                    },
                    "evidence": [
                        {
                            **evidence.__dict__,
                            "quote_date": evidence.quote_date.isoformat(),
                        }
                        for evidence in line.evidence
                    ],
                }
                for line in result.target_lines
            ],
        }
    )
    return payload


def _inflation_payload(run: object, points: dict[str, Decimal]) -> dict[str, object]:
    latest_period = None if run is None else run.latest_period
    return {
        "available": run is not None and latest_period in points,
        "sync_run_id": None if run is None else run.id,
        "latest_period": latest_period,
        "latest_value": None if latest_period is None else points.get(latest_period),
        "source_last_changed": (
            None
            if run is None or run.source_last_changed is None
            else run.source_last_changed.isoformat()
        ),
        "point_count": len(points),
        "org_id": KOSIS_ORG_ID,
        "table_id": KOSIS_TABLE_ID,
        "item_id": KOSIS_ITEM_ID,
        "classifier_code": KOSIS_CLASSIFIER_CODE,
        "unit": "2020=100",
        "source_url": KOSIS_SERIES_URL,
    }


def _require_incoming_role(session: Session, document_id: int) -> None:
    if session.get(SourceDocument, document_id) is None:
        return
    role = session.scalar(
        select(QuoteDocumentRole)
        .where(QuoteDocumentRole.document_id == document_id)
        .order_by(QuoteDocumentRole.id.desc())
        .limit(1)
    )
    if (
        role is None
        or role.purpose is not QuoteDocumentPurpose.INCOMING_BID
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "DOCUMENT_ROLE_MISMATCH",
                "message": (
                    "analysis accepts only current incoming bid documents"
                ),
            },
        )
