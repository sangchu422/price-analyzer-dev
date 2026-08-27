"""Typed, read-only quote-analysis API."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
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
    CPI_KOSIS_CLASSIFIER_CODE,
    CPI_KOSIS_ITEM_ID,
    CPI_KOSIS_ORG_ID,
    CPI_KOSIS_SERIES_URL,
    CPI_KOSIS_TABLE_ID,
    CPI_KOSIS_UNIT,
    KOSIS_CLASSIFIER_CODE,
    KOSIS_ITEM_ID,
    KOSIS_ORG_ID,
    KOSIS_SERIES_URL,
    KOSIS_TABLE_ID,
    AnalysisRunResult,
    CpiInflationEvidenceResult,
    InflationSeriesUnavailable,
    TargetEvidenceResult,
    cpi_evidence_for_quote_year,
    cpi_series_evidence,
    cpi_series_for_sync_run,
    create_analysis_run,
    latest_cpi_series,
    latest_ppi_series,
    sync_cpi_series,
    sync_ppi_series,
)
from app.analysis.models import (
    InflationSyncRun,
    QuoteAnalysisLineResult,
    QuoteAnalysisRun,
    QuoteAnalysisTargetEvidence,
)
from app.api.catalog import get_candidate_embedding_runtime
from app.api.xlsx_export import build_xlsx_response
from app.catalog.service import CandidateEmbeddingRuntime
from app.core.config import settings
from app.db.session import get_session
from app.documents.models import SourceDocument
from app.procurement.activation import (
    ActivationNotFound,
    activate_analysis_run,
    activation_payload,
    deliver_outlook_alerts,
)
from app.procurement.history import (
    CatalogStateConflict,
    catalog_state_payload,
    current_catalog_state,
    list_analysis_history,
    set_catalog_state,
)
from app.procurement.models import QuoteCatalogStateDecision
from app.analysis.family_analysis import family_analysis_payload
from app.procurement.equipment import (
    create_equipment_projection,
    equipment_group_payloads,
    equipment_group_payloads_from_result,
)
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


class AnnualRateResponse(BaseModel):
    year: str
    rate: Decimal


class CpiInflationEvidenceResponse(BaseModel):
    sync_run_id: int
    latest_confirmed_year: str
    annual_rates: list[AnnualRateResponse]
    factor: Decimal
    cumulative_percent: Decimal


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
    inflation: CpiInflationEvidenceResponse | None = None


class TargetLineResponse(BaseModel):
    raw_item_id: int
    status: Literal[
        "AVAILABLE",
        "DATE_UNAVAILABLE",
        "INDEX_UNAVAILABLE",
        "RATE_GAP",
        "COMPARABILITY_REVIEW_REQUIRED",
        "MARKET_REFERENCE_REQUIRED",
        "NOT_APPLICABLE",
    ]
    target_unit_price: Decimal | None
    target_amount: Decimal | None
    variance_amount: Decimal | None
    variance_percent: Decimal | None
    unit_variance_amount: Decimal | None = None
    used_observation_count: int
    excluded_observation_count: int
    reason: str
    evidence: list[TargetEvidenceResponse]
    calculation_basis: dict[str, Any] | None = None
    comparison_evidence: list[dict[str, Any]] = Field(default_factory=list)


class EquipmentLineResponse(BaseModel):
    raw_item_id: int
    quote_amount: Decimal
    target_amount: Decimal | None
    negotiation_amount: Decimal


class EquipmentGroupResponse(BaseModel):
    id: int
    key: str
    name: str
    source_kind: str
    quote_amount: Decimal
    target_amount: Decimal
    negotiation_amount: Decimal
    unallocated_amount: Decimal
    line_count: int
    target_available_count: int
    lines: list[EquipmentLineResponse]


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


class CpiInflationSeriesResponse(BaseModel):
    available: bool
    sync_run_id: int | None
    latest_period: str | None
    latest_annual_rate: Decimal | None
    source_last_changed: str | None
    point_count: int
    org_id: str
    table_id: str
    item_id: str
    classifier_code: str
    unit: str
    source_url: str
    annual_rates: list[AnnualRateResponse]
    factor: Decimal | None
    cumulative_percent: Decimal | None


class AnalysisRunResponse(DocumentAnalysisResponse):
    run_id: int
    inflation_sync_run_id: int | None
    inflation_series_kind: str | None
    target_period: str | None
    target_index_value: Decimal | None
    inflation_source_url: str
    inflation_source_last_changed: str | None
    quote_total_amount: Decimal | None
    target_total_amount: Decimal | None
    target_available_count: int
    target_unavailable_count: int
    target_lines: list[TargetLineResponse]
    equipment_groups: list[EquipmentGroupResponse]
    family_analysis: dict[str, Any]


class StoredAnalysisRunResponse(BaseModel):
    run_id: int
    document_id: int
    created_by: str
    created_at: str
    review_percent: Decimal
    high_percent: Decimal
    inflation_sync_run_id: int | None
    inflation_series_kind: str | None
    target_period: str | None
    target_index_value: Decimal | None
    quote_total_amount: Decimal | None
    target_total_amount: Decimal | None
    target_available_count: int
    target_unavailable_count: int
    target_lines: list[TargetLineResponse]
    equipment_groups: list[EquipmentGroupResponse]


class QuoteActivationRequest(BaseModel):
    activated_by: str = Field(min_length=1, max_length=100)
    reason_detail: str = Field(min_length=3, max_length=1000)
    send_outlook: bool = False
    outlook_recipient: str | None = Field(default=None, max_length=320)


class QuoteCatalogStateRequest(BaseModel):
    state: Literal["INCLUDED", "EXCLUDED"]
    decided_by: str = Field(min_length=1, max_length=100)
    reason_detail: str = Field(min_length=3, max_length=1000)
    expected_current_decision_id: int | None = None


@router.get("/history")
def get_analysis_history(
    session: Session = Depends(get_session),
    *,
    limit: int = Query(30, ge=1, le=100),
    after_id: int | None = Query(None, ge=1),
    state: Literal["NOT_INCLUDED", "INCLUDED", "EXCLUDED"] | None = Query(None),
) -> dict[str, object]:
    page = list_analysis_history(
        session,
        limit=limit,
        after_id=after_id,
        state=state,
    )
    return {
        "items": page.items,
        "total": page.total,
        "next_cursor": page.next_cursor,
        "limit": limit,
    }


@router.post("/runs/{run_id}/catalog-state")
def post_catalog_state(
    run_id: int,
    body: QuoteCatalogStateRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        decision = set_catalog_state(
            session,
            run_id,
            state=body.state,
            decided_by=body.decided_by,
            reason_detail=body.reason_detail,
            expected_current_decision_id=body.expected_current_decision_id,
        )
        session.commit()
        return catalog_state_payload(decision)
    except CatalogStateConflict as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "STALE_CATALOG_STATE",
                "message": str(exc),
                "current_decision_id": exc.current_decision_id,
            },
        ) from exc
    except ActivationNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


@router.get("/documents/{document_id}/export")
def export_document_analysis(
    document_id: int,
    session: Session = Depends(get_session),
    runtime: CandidateEmbeddingRuntime = Depends(get_candidate_embedding_runtime),
    *,
    review_percent: Decimal = Query(Decimal("10"), ge=0),
    high_percent: Decimal = Query(Decimal("20"), ge=0),
) -> Response:
    lines: list = []
    after_id: int | None = None
    while True:
        result = _analyze(
            session,
            document_id,
            runtime=runtime,
            after_id=after_id,
            limit=100,
            match_status=None,
            assessment=None,
            review_percent=review_percent,
            high_percent=high_percent,
        )
        lines.extend(result.lines)
        session.commit()
        if result.next_cursor is None:
            break
        after_id = result.next_cursor

    headers = [
        "품명", "규격", "단위", "수량", "개당 단가", "구매 금액",
        "참조 기준가", "참조 최저", "참조 최고", "편차 금액", "편차율(%)",
        "매칭 상태", "표준 품목 ID", "표준 가격 버전 ID", "가격 판정",
    ]
    rows = [
        [
            line.item_name or "",
            line.spec or "",
            line.unit or "",
            line.quantity,
            line.quote_unit_price,
            line.quote_amount,
            line.reference_price,
            line.minimum_price,
            line.maximum_price,
            line.variance_amount,
            line.variance_percent,
            line.match_status,
            line.standard_item_id,
            line.standard_price_version_id,
            line.assessment,
        ]
        for line in lines
    ]
    return build_xlsx_response(
        sheet_title="견적 분석 결과",
        headers=headers,
        rows=rows,
        filename=f"견적분석_문서{document_id}_{date.today():%Y%m%d}.xlsx",
    )


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
        create_equipment_projection(session, result)
        session.commit()
    except (AnalysisNotFound, ValueError) as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    payload = _analysis_run_payload(result, body.review_percent, body.high_percent)
    equipment_groups = equipment_group_payloads(session, result.run_id)
    payload["equipment_groups"] = (
        equipment_groups
        if equipment_groups
        else equipment_group_payloads_from_result(result)
    )
    payload["family_analysis"] = family_analysis_payload(
        session,
        result,
        review_percent=body.review_percent,
        high_percent=body.high_percent,
    )
    return payload


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
    inflation_run = (
        None
        if run.inflation_sync_run_id is None
        else session.get(InflationSyncRun, run.inflation_sync_run_id)
    )
    cpi_run, annual_rates = cpi_series_for_sync_run(
        session,
        run.inflation_sync_run_id,
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
        cpi_evidence = (
            None
            if cpi_run is None
            else cpi_evidence_for_quote_year(
                cpi_run.id,
                evidence.quote_date.year,
                annual_rates,
                cpi_run.latest_period,
            )
        )
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
                "inflation": _cpi_evidence_payload(cpi_evidence),
            }
        )
    return {
        "run_id": run.id,
        "document_id": run.document_id,
        "created_by": run.created_by,
        "created_at": run.created_at.isoformat(),
        "review_percent": run.review_percent,
        "high_percent": run.high_percent,
        "inflation_sync_run_id": run.inflation_sync_run_id,
        "inflation_series_kind": (
            None if inflation_run is None else inflation_run.series_kind
        ),
        "target_period": run.target_period,
        "target_index_value": run.target_index_value,
        "inflation_source_url": (
            None if inflation_run is None else inflation_run.source_url
        ),
        "inflation_source_last_changed": (
            None
            if inflation_run is None or inflation_run.source_last_changed is None
            else inflation_run.source_last_changed.isoformat()
        ),
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
                "unit_variance_amount": line.target_unit_variance_amount,
                "used_observation_count": line.target_used_observation_count,
                "excluded_observation_count": line.target_excluded_observation_count,
                "reason": line.target_reason,
                "evidence": evidence_by_line.get(line.id, []),
            }
            for line in lines
        ],
        "equipment_groups": equipment_group_payloads(session, run.id),
    }


@router.get("/runs/{run_id}/equipment-groups", response_model=list[EquipmentGroupResponse])
def get_analysis_equipment_groups(
    run_id: int,
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    if session.get(QuoteAnalysisRun, run_id) is None:
        raise HTTPException(status_code=404, detail="분석 실행 이력을 찾을 수 없습니다.")
    return equipment_group_payloads(session, run_id)


@router.post("/runs/{run_id}/activate")
def post_activate_analysis_run(
    run_id: int,
    body: QuoteActivationRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    outlook_recipient = (body.outlook_recipient or "").strip()
    if body.send_outlook and (
        not outlook_recipient
        or "@" not in outlook_recipient
        or len(outlook_recipient) > 320
    ):
        # Validate optional delivery before committing the append-only catalog
        # activation. A bad address must not make the API report failure after
        # the standard DB was already updated successfully.
        raise HTTPException(
            status_code=422,
            detail="Outlook 알림 수신 이메일을 확인해 주세요.",
        )
    try:
        activation = activate_analysis_run(
            session,
            run_id,
            activated_by=body.activated_by,
            reason_detail=body.reason_detail,
        )
        if current_catalog_state(session, activation.document_id) is None:
            session.add(
                QuoteCatalogStateDecision(
                    analysis_run_id=run_id,
                    document_id=activation.document_id,
                    state="INCLUDED",
                    supersedes_decision_id=None,
                    decided_by=body.activated_by.strip(),
                    reason_detail=body.reason_detail.strip(),
                )
            )
        session.commit()
    except ActivationNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    deliveries: list[dict[str, object]] = []
    if body.send_outlook:
        try:
            sent = deliver_outlook_alerts(
                session,
                activation,
                recipient=outlook_recipient,
            )
            session.commit()
            deliveries = [
                {
                    "alert_id": delivery.alert_id,
                    "channel": delivery.channel,
                    "recipient": delivery.recipient,
                    "status": delivery.status,
                    "detail": delivery.detail,
                }
                for delivery in sent
            ]
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    payload = activation_payload(session, activation)
    payload["deliveries"] = deliveries
    return payload


_TARGET_STATUS_LABELS = {
    "AVAILABLE": "산정 완료",
    "DATE_UNAVAILABLE": "원본 견적일 확인 필요",
    "INDEX_UNAVAILABLE": "물가지수 갱신 필요",
    "RATE_GAP": "연간 소비자물가 자료 누락",
    "COMPARABILITY_REVIEW_REQUIRED": "규격·가격 범위 확인 필요",
    "MARKET_REFERENCE_REQUIRED": "표준 DB 없음 · 시장가 별도 확인",
    "NOT_APPLICABLE": "산정 제외",
}


@router.get("/runs/{run_id}/target-price-export")
def export_target_price_run(
    run_id: int,
    session: Session = Depends(get_session),
    runtime: CandidateEmbeddingRuntime = Depends(get_candidate_embedding_runtime),
) -> Response:
    run = session.get(QuoteAnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="분석 실행 이력을 찾을 수 없습니다.")

    target_rows = list(
        session.scalars(
            select(QuoteAnalysisLineResult)
            .where(QuoteAnalysisLineResult.analysis_run_id == run.id)
            .order_by(QuoteAnalysisLineResult.raw_item_id)
        )
    )
    target_by_raw_item_id = {row.raw_item_id: row for row in target_rows}

    lines: list = []
    after_id: int | None = None
    while True:
        page = _analyze(
            session,
            run.document_id,
            runtime=runtime,
            after_id=after_id,
            limit=100,
            match_status=None,
            assessment=None,
            review_percent=run.review_percent,
            high_percent=run.high_percent,
        )
        lines.extend(page.lines)
        if page.next_cursor is None:
            break
        after_id = page.next_cursor

    headers = [
        "품명", "규격", "단위", "수량", "개당 단가", "구매 금액",
        "구매 목표 단가(개당)", "구매 목표금액", "목표 인하 금액", "산정 상태",
    ]
    rows = []
    total_negotiable = Decimal("0")
    for line in lines:
        target = target_by_raw_item_id.get(line.raw_item_id)
        negotiable = Decimal("0")
        if (
            target is not None
            and target.target_status == "AVAILABLE"
            and target.target_variance_amount is not None
            and target.target_variance_amount > 0
        ):
            negotiable = target.target_variance_amount
        total_negotiable += negotiable
        target_unit = target.target_unit_price if target else None
        if (
            target_unit is not None
            and line.quote_unit_price is not None
        ):
            target_unit = min(target_unit, line.quote_unit_price)
        target_amount = target.target_amount if target else None
        if target_amount is not None and line.quote_amount is not None:
            target_amount = min(target_amount, line.quote_amount)
        rows.append([
            line.item_name or "",
            line.spec or "",
            line.unit or "",
            line.quantity,
            line.quote_unit_price,
            line.quote_amount,
            target_unit,
            target_amount,
            negotiable,
            _TARGET_STATUS_LABELS.get(target.target_status, target.target_status)
            if target
            else "—",
        ])
    overall_target = (
        None
        if run.quote_total_amount is None
        else run.quote_total_amount - total_negotiable
    )
    rows.append([
        "합계",
        "",
        "",
        None,
        None,
        run.quote_total_amount,
        None,
        overall_target,
        total_negotiable,
        "전체 견적 - 목표 인하 금액",
    ])
    return build_xlsx_response(
        sheet_title="구매 목표가 분석 결과",
        headers=headers,
        rows=rows,
        filename=f"구매목표가_문서{run.document_id}_{date.today():%Y%m%d}.xlsx",
    )


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


@router.get(
    "/inflation/series/cpi-all",
    response_model=CpiInflationSeriesResponse,
)
def get_cpi_series(session: Session = Depends(get_session)) -> dict[str, object]:
    run, annual_rates = latest_cpi_series(session)
    return _cpi_inflation_payload(run, annual_rates)


@router.post(
    "/inflation/series/cpi-all/sync",
    response_model=CpiInflationSeriesResponse,
)
def post_cpi_sync(session: Session = Depends(get_session)) -> dict[str, object]:
    try:
        run = sync_cpi_series(session, settings)
        session.commit()
    except InflationSeriesUnavailable as exc:
        session.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    _, annual_rates = cpi_series_for_sync_run(session, run.id)
    return _cpi_inflation_payload(run, annual_rates)


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
    review_percent: Decimal | None = None,
    high_percent: Decimal | None = None,
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
            review_percent=review_percent or settings.price_variance_review_percent,
            high_percent=high_percent or settings.price_variance_high_percent,
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
            "inflation_sync_run_id": result.inflation_sync_run_id,
            "inflation_series_kind": result.inflation_series_kind,
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
                        _target_evidence_payload(evidence)
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


def _cpi_inflation_payload(
    run: InflationSyncRun | None,
    annual_rates: dict[str, Decimal],
) -> dict[str, object]:
    evidence = cpi_series_evidence(run, annual_rates)
    latest_period = None if run is None else run.latest_period
    return {
        "available": run is not None and latest_period in annual_rates,
        "sync_run_id": None if run is None else run.id,
        "latest_period": latest_period,
        "latest_annual_rate": (
            None if latest_period is None else annual_rates.get(latest_period)
        ),
        "source_last_changed": (
            None
            if run is None or run.source_last_changed is None
            else run.source_last_changed.isoformat()
        ),
        "point_count": len(annual_rates),
        "org_id": CPI_KOSIS_ORG_ID,
        "table_id": CPI_KOSIS_TABLE_ID,
        "item_id": CPI_KOSIS_ITEM_ID,
        "classifier_code": CPI_KOSIS_CLASSIFIER_CODE,
        "unit": CPI_KOSIS_UNIT,
        "source_url": CPI_KOSIS_SERIES_URL,
        "annual_rates": [
            {"year": year, "rate": rate}
            for year, rate in sorted(annual_rates.items())
        ],
        "factor": None if evidence is None else evidence.factor,
        "cumulative_percent": (
            None if evidence is None else evidence.cumulative_percent
        ),
    }


def _cpi_evidence_payload(
    evidence: CpiInflationEvidenceResult | None,
) -> dict[str, object] | None:
    if evidence is None:
        return None
    return {
        "sync_run_id": evidence.sync_run_id,
        "latest_confirmed_year": evidence.latest_confirmed_year,
        "annual_rates": [
            {"year": rate.year, "rate": rate.rate}
            for rate in evidence.annual_rates
        ],
        "factor": evidence.factor,
        "cumulative_percent": evidence.cumulative_percent,
    }


def _target_evidence_payload(
    evidence: TargetEvidenceResult,
) -> dict[str, object]:
    return {
        **{
            key: value
            for key, value in evidence.__dict__.items()
            if key not in {"quote_date", "inflation"}
        },
        "quote_date": evidence.quote_date.isoformat(),
        "inflation": _cpi_evidence_payload(evidence.inflation),
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
