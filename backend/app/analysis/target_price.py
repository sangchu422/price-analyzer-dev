"""Inflation cache and immutable purchase-target analysis snapshots."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from statistics import median

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.models import (
    InflationIndexPoint,
    InflationSyncRun,
    QuoteAnalysisLineResult,
    QuoteAnalysisRun,
    QuoteAnalysisTargetEvidence,
)
from app.analysis.service import AnalysisLine, DocumentAnalysis, analyze_document
from app.catalog.models import DocumentMetadataVersion, StandardPriceObservation
from app.catalog.service import CandidateEmbeddingRuntime
from app.cleansing.models import CleanDecision
from app.core.config import Settings
from app.documents.models import SourceDocument, SourceVariant
from app.quotes.models import RawQuoteItem


KOSIS_ORG_ID = "301"
KOSIS_TABLE_ID = "DT_404Y014"
KOSIS_ITEM_ID = "13103134604999"
KOSIS_CLASSIFIER_CODE = "13102134604ACC_CD.*AA"
KOSIS_UNIT = "2020=100"
KOSIS_SERIES_URL = (
    "https://kosis.kr/statHtml/statHtml.do?orgId=301&tblId=DT_404Y014"
)
MONEY_QUANTUM = Decimal("0.000001")


class InflationSeriesUnavailable(RuntimeError):
    """No valid locally cached PPI series can support target calculation."""


@dataclass(frozen=True)
class TargetEvidenceResult:
    raw_item_id: int
    metadata_version_id: int
    source_document_id: int
    source_variant_id: int
    source_logical_name: str
    source_sheet: str | None
    source_page: int | None
    source_row: int | None
    source_cells: str | None
    quote_date: date
    source_period: str
    original_unit_price: Decimal
    source_index_value: Decimal
    target_index_value: Decimal
    adjusted_unit_price: Decimal


@dataclass(frozen=True)
class TargetLineResult:
    raw_item_id: int
    status: str
    target_unit_price: Decimal | None
    target_amount: Decimal | None
    variance_amount: Decimal | None
    variance_percent: Decimal | None
    used_observation_count: int
    excluded_observation_count: int
    reason: str
    evidence: tuple[TargetEvidenceResult, ...]


@dataclass(frozen=True)
class AnalysisRunResult:
    run_id: int
    analysis: DocumentAnalysis
    target_period: str | None
    target_index_value: Decimal | None
    inflation_source_url: str
    inflation_source_last_changed: date | None
    quote_total_amount: Decimal | None
    target_total_amount: Decimal | None
    target_available_count: int
    target_unavailable_count: int
    target_lines: tuple[TargetLineResult, ...]


def sync_ppi_series(session: Session, settings: Settings) -> InflationSyncRun:
    """Fetch the official KOSIS all-items PPI and append an offline snapshot."""

    now = datetime.now()
    params = {
        "method": "getList",
        "format": "json",
        "jsonVD": "Y",
        "orgId": KOSIS_ORG_ID,
        "tblId": KOSIS_TABLE_ID,
        "itmId": KOSIS_ITEM_ID,
        "prdSe": "M",
        "startPrdDe": settings.kosis_ppi_start_period,
        "endPrdDe": f"{now.year:04d}{now.month:02d}",
        "objL1": KOSIS_CLASSIFIER_CODE,
    }
    endpoint = f"{settings.kosis_proxy_base_url.rstrip('/')}/v1/kosis/data"
    try:
        response = httpx.get(
            endpoint,
            params=params,
            timeout=settings.kosis_request_timeout_seconds,
            headers={"User-Agent": "price-analyzer/ppi-sync"},
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise InflationSeriesUnavailable(
            "KOSIS 생산자물가지수를 갱신하지 못했습니다. 기존 캐시는 변경되지 않았습니다."
        ) from exc
    points = _validated_kosis_points(payload)
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    existing = session.scalar(
        select(InflationSyncRun).where(InflationSyncRun.response_sha256 == fingerprint)
    )
    if existing is not None:
        return existing
    latest_period = max(points)
    changed_values = {
        str(row.get("LST_CHN_DE")) for row in payload if row.get("LST_CHN_DE")
    }
    source_last_changed = None
    if len(changed_values) == 1:
        try:
            source_last_changed = date.fromisoformat(changed_values.pop())
        except ValueError:
            pass
    run = InflationSyncRun(
        provider="KOSIS",
        org_id=KOSIS_ORG_ID,
        table_id=KOSIS_TABLE_ID,
        item_id=KOSIS_ITEM_ID,
        classifier_code=KOSIS_CLASSIFIER_CODE,
        period_type="M",
        unit=KOSIS_UNIT,
        source_url=KOSIS_SERIES_URL,
        source_last_changed=source_last_changed,
        response_sha256=fingerprint,
        row_count=len(points),
        latest_period=latest_period,
    )
    session.add(run)
    session.flush()
    session.add_all(
        InflationIndexPoint(sync_run_id=run.id, period=period, index_value=value)
        for period, value in sorted(points.items())
    )
    session.flush()
    return run


def latest_ppi_series(
    session: Session,
) -> tuple[InflationSyncRun | None, dict[str, Decimal]]:
    run = session.scalar(select(InflationSyncRun).order_by(InflationSyncRun.id.desc()).limit(1))
    if run is None:
        return None, {}
    points = {
        row.period: row.index_value
        for row in session.scalars(
            select(InflationIndexPoint)
            .where(InflationIndexPoint.sync_run_id == run.id)
            .order_by(InflationIndexPoint.period)
        )
    }
    return run, points


def create_analysis_run(
    session: Session,
    document_id: int,
    *,
    created_by: str,
    review_percent: Decimal,
    high_percent: Decimal,
    embedding_runtime: CandidateEmbeddingRuntime,
) -> AnalysisRunResult:
    actor = created_by.strip()
    if not actor:
        raise ValueError("분석 실행자를 입력해 주세요.")
    analysis = _complete_analysis(
        session,
        document_id,
        review_percent=review_percent,
        high_percent=high_percent,
        embedding_runtime=embedding_runtime,
    )
    sync_run, points = latest_ppi_series(session)
    target_period = None if sync_run is None else sync_run.latest_period
    target_index = None if target_period is None else points.get(target_period)
    observations = _load_target_observations(
        session,
        {
            line.standard_price_version_id
            for line in analysis.lines
            if line.standard_price_version_id is not None
        },
    )
    target_lines = tuple(
        _target_line(line, observations.get(line.standard_price_version_id, ()), points, target_period, target_index)
        for line in analysis.lines
    )
    quote_amounts = [line.quote_amount for line in analysis.lines if line.quote_amount is not None]
    target_amounts = [line.target_amount for line in target_lines if line.target_amount is not None]
    quote_total = _sum_or_none(quote_amounts)
    target_total = _sum_or_none(target_amounts)
    available = sum(line.status == "AVAILABLE" for line in target_lines)
    run = QuoteAnalysisRun(
        document_id=document_id,
        created_by=actor,
        review_percent=review_percent,
        high_percent=high_percent,
        inflation_sync_run_id=None if sync_run is None else sync_run.id,
        target_period=target_period,
        target_index_value=target_index,
        total_line_count=len(analysis.lines),
        target_available_count=available,
        target_unavailable_count=len(target_lines) - available,
        quote_total_amount=quote_total,
        target_total_amount=target_total,
    )
    session.add(run)
    session.flush()
    for source_line, target in zip(analysis.lines, target_lines, strict=True):
        stored_line = QuoteAnalysisLineResult(
            analysis_run_id=run.id,
            raw_item_id=source_line.raw_item_id,
            standard_price_version_id=source_line.standard_price_version_id,
            match_status=source_line.match_status,
            assessment=source_line.assessment,
            reference_price=source_line.reference_price,
            variance_amount=source_line.variance_amount,
            variance_percent=source_line.variance_percent,
            target_status=target.status,
            target_unit_price=target.target_unit_price,
            target_amount=target.target_amount,
            target_variance_amount=target.variance_amount,
            target_variance_percent=target.variance_percent,
            target_used_observation_count=target.used_observation_count,
            target_excluded_observation_count=target.excluded_observation_count,
            target_reason=target.reason,
        )
        session.add(stored_line)
        session.flush()
        session.add_all(
            QuoteAnalysisTargetEvidence(line_result_id=stored_line.id, **evidence.__dict__)
            for evidence in target.evidence
        )
    session.flush()
    return AnalysisRunResult(
        run_id=run.id,
        analysis=analysis,
        target_period=target_period,
        target_index_value=target_index,
        inflation_source_url=KOSIS_SERIES_URL,
        inflation_source_last_changed=None if sync_run is None else sync_run.source_last_changed,
        quote_total_amount=quote_total,
        target_total_amount=target_total,
        target_available_count=available,
        target_unavailable_count=len(target_lines) - available,
        target_lines=target_lines,
    )


def _complete_analysis(
    session: Session,
    document_id: int,
    *,
    review_percent: Decimal,
    high_percent: Decimal,
    embedding_runtime: CandidateEmbeddingRuntime,
) -> DocumentAnalysis:
    lines: list[AnalysisLine] = []
    after_id: int | None = None
    first: DocumentAnalysis | None = None
    while True:
        page = analyze_document(
            session,
            document_id,
            after_id=after_id,
            limit=100,
            review_percent=review_percent,
            high_percent=high_percent,
            embedding_runtime=embedding_runtime,
            deterministic_exact_match=True,
        )
        first = first or page
        lines.extend(page.lines)
        if page.next_cursor is None:
            break
        after_id = page.next_cursor
    assert first is not None
    return DocumentAnalysis(first.document_id, first.logical_name, tuple(lines), None, 100)


def _validated_kosis_points(payload: object) -> dict[str, Decimal]:
    if not isinstance(payload, list):
        raise InflationSeriesUnavailable("KOSIS 응답 형식이 올바르지 않습니다.")
    points: dict[str, Decimal] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        if (
            str(row.get("ORG_ID")) != KOSIS_ORG_ID
            or str(row.get("TBL_ID")) != KOSIS_TABLE_ID
            or str(row.get("ITM_ID")) != KOSIS_ITEM_ID
            or str(row.get("C1")) != KOSIS_CLASSIFIER_CODE
            or str(row.get("PRD_SE")) != "M"
        ):
            continue
        period = str(row.get("PRD_DE", ""))
        try:
            value = Decimal(str(row.get("DT")))
        except Exception:
            continue
        if len(period) == 6 and period.isdigit() and value.is_finite() and value > 0:
            points[period] = value.quantize(MONEY_QUANTUM)
    if not points:
        raise InflationSeriesUnavailable("KOSIS 응답에 유효한 생산자물가지수가 없습니다.")
    return points


def _load_target_observations(session: Session, price_ids: set[int]) -> dict[int, tuple[tuple, ...]]:
    if not price_ids:
        return {}
    rows = session.execute(
        select(
            StandardPriceObservation.standard_price_version_id,
            StandardPriceObservation.raw_item_id,
            StandardPriceObservation.metadata_version_id,
            CleanDecision.unit_price,
            DocumentMetadataVersion.quote_date,
            DocumentMetadataVersion.evidence_json,
            SourceDocument.id,
            SourceDocument.logical_name,
            SourceVariant.id,
            RawQuoteItem.source_sheet,
            RawQuoteItem.source_page,
            RawQuoteItem.source_row,
            RawQuoteItem.source_cells,
        )
        .join(CleanDecision, CleanDecision.id == StandardPriceObservation.clean_decision_id)
        .join(RawQuoteItem, RawQuoteItem.id == StandardPriceObservation.raw_item_id)
        .join(SourceVariant, SourceVariant.id == RawQuoteItem.source_variant_id)
        .join(SourceDocument, SourceDocument.id == SourceVariant.document_id)
        .outerjoin(DocumentMetadataVersion, DocumentMetadataVersion.id == StandardPriceObservation.metadata_version_id)
        .where(StandardPriceObservation.standard_price_version_id.in_(price_ids))
        .order_by(StandardPriceObservation.standard_price_version_id, StandardPriceObservation.raw_item_id)
    ).tuples()
    grouped: dict[int, list[tuple]] = defaultdict(list)
    for row in rows:
        grouped[row[0]].append(tuple(row[1:]))
    return {key: tuple(value) for key, value in grouped.items()}


def _target_line(
    line: AnalysisLine,
    rows: tuple[tuple, ...],
    points: dict[str, Decimal],
    target_period: str | None,
    target_index: Decimal | None,
) -> TargetLineResult:
    if line.match_status != "MATCHED" or line.standard_price_version_id is None:
        status = "MARKET_REFERENCE_REQUIRED" if line.market_price_lookup_required else "NOT_APPLICABLE"
        return TargetLineResult(line.raw_item_id, status, None, None, None, None, 0, 0, "표준 DB 매칭이 없어 시장가를 별도로 확인해야 합니다." if status == "MARKET_REFERENCE_REQUIRED" else "구매 목표가 산정 대상이 아닙니다.", ())
    if target_period is None or target_index is None:
        return TargetLineResult(line.raw_item_id, "INDEX_UNAVAILABLE", None, None, None, None, 0, len(rows), "저장된 생산자물가지수가 없어 목표가를 계산할 수 없습니다.", ())
    evidence: list[TargetEvidenceResult] = []
    for row in rows:
        raw_id, metadata_id, unit_price, quote_date, evidence_json, document_id, logical_name, variant_id, sheet, page, source_row, cells = row
        if metadata_id is None or unit_price is None or quote_date is None or not _is_source_confirmed(evidence_json):
            continue
        source_period = quote_date.strftime("%Y%m")
        source_index = points.get(source_period)
        if source_index is None or source_period > target_period:
            continue
        adjusted = (unit_price * target_index / source_index).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        evidence.append(TargetEvidenceResult(raw_id, metadata_id, document_id, variant_id, logical_name, sheet, page, source_row, cells, quote_date, source_period, unit_price, source_index, target_index, adjusted))
    excluded = len(rows) - len(evidence)
    if not evidence:
        return TargetLineResult(line.raw_item_id, "DATE_UNAVAILABLE", None, None, None, None, 0, excluded, "원본 본문·머리말에서 확인된 견적일이 없어 목표가를 계산할 수 없습니다.", ())
    target_unit = Decimal(str(median([item.adjusted_unit_price for item in evidence]))).quantize(MONEY_QUANTUM)
    target_amount = None if line.quantity is None else (target_unit * line.quantity).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    variance_amount = None
    variance_percent = None
    if line.quote_unit_price is not None:
        variance_amount = (line.quote_unit_price - target_unit).quantize(MONEY_QUANTUM)
        variance_percent = (variance_amount / target_unit * Decimal("100")).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    reason = f"원본 날짜가 확인된 과거 단가 {len(evidence)}건을 {target_period[:4]}년 {int(target_period[4:])}월 물가 수준으로 보정했습니다."
    if len(evidence) == 1:
        reason += " 근거가 1건이므로 신뢰도가 낮습니다."
    return TargetLineResult(line.raw_item_id, "AVAILABLE", target_unit, target_amount, variance_amount, variance_percent, len(evidence), excluded, reason, tuple(evidence))


def _is_source_confirmed(evidence_json: str | None) -> bool:
    try:
        payload = json.loads(evidence_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return False
    quote_date = payload.get("quote_date") if isinstance(payload, dict) else None
    return isinstance(quote_date, dict) and quote_date.get("quality") == "SOURCE_CONFIRMED" and quote_date.get("use_for_index") == "EXACT_DATE"


def _sum_or_none(values: list[Decimal]) -> Decimal | None:
    return None if not values else sum(values, Decimal("0")).quantize(MONEY_QUANTUM)
