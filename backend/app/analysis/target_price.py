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


PPI_SERIES_KIND = "PPI_ALL"
CPI_SERIES_KIND = "CPI_ALL"

PPI_KOSIS_ORG_ID = "301"
PPI_KOSIS_TABLE_ID = "DT_404Y014"
PPI_KOSIS_ITEM_ID = "13103134604999"
PPI_KOSIS_CLASSIFIER_CODE = "13102134604ACC_CD.*AA"
PPI_KOSIS_UNIT = "2020=100"
PPI_KOSIS_SERIES_URL = (
    "https://kosis.kr/statHtml/statHtml.do?orgId=301&tblId=DT_404Y014"
)

# Compatibility aliases for the read-only legacy PPI API and its stored runs.
KOSIS_ORG_ID = PPI_KOSIS_ORG_ID
KOSIS_TABLE_ID = PPI_KOSIS_TABLE_ID
KOSIS_ITEM_ID = PPI_KOSIS_ITEM_ID
KOSIS_CLASSIFIER_CODE = PPI_KOSIS_CLASSIFIER_CODE
KOSIS_UNIT = PPI_KOSIS_UNIT
KOSIS_SERIES_URL = PPI_KOSIS_SERIES_URL

CPI_KOSIS_ORG_ID = "101"
CPI_KOSIS_TABLE_ID = "DT_1J22041"
CPI_KOSIS_ITEM_ID = "T"
CPI_KOSIS_CLASSIFIER_CODE = "0"
CPI_KOSIS_UNIT = "%"
CPI_KOSIS_SERIES_URL = (
    "https://kosis.kr/statHtml/statHtml.do?orgId=101&tblId=DT_1J22041"
)

MONEY_QUANTUM = Decimal("0.000001")
KRW_QUANTUM = Decimal("1")


class InflationSeriesUnavailable(RuntimeError):
    """No valid locally cached inflation series can support a calculation."""


@dataclass(frozen=True)
class AnnualRateResult:
    year: str
    rate: Decimal


@dataclass(frozen=True)
class CpiInflationEvidenceResult:
    sync_run_id: int
    latest_confirmed_year: str
    annual_rates: tuple[AnnualRateResult, ...]
    factor: Decimal
    cumulative_percent: Decimal


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
    inflation: CpiInflationEvidenceResult | None = None


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
    unit_variance_amount: Decimal | None = None


@dataclass(frozen=True)
class AnalysisRunResult:
    run_id: int
    analysis: DocumentAnalysis
    inflation_sync_run_id: int | None
    inflation_series_kind: str | None
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
        series_kind=PPI_SERIES_KIND,
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
    return _latest_series(session, PPI_SERIES_KIND)


def sync_cpi_series(session: Session, settings: Settings) -> InflationSyncRun:
    """Fetch KOSIS annual all-items CPI change rates and append a snapshot."""

    now = datetime.now()
    params = {
        "method": "getList",
        "format": "json",
        "jsonVD": "Y",
        "orgId": CPI_KOSIS_ORG_ID,
        "tblId": CPI_KOSIS_TABLE_ID,
        "itmId": CPI_KOSIS_ITEM_ID,
        "prdSe": "Y",
        "startPrdDe": settings.kosis_cpi_start_period,
        "endPrdDe": f"{now.year:04d}",
        "objL1": CPI_KOSIS_CLASSIFIER_CODE,
    }
    endpoint = f"{settings.kosis_proxy_base_url.rstrip('/')}/v1/kosis/data"
    try:
        response = httpx.get(
            endpoint,
            params=params,
            timeout=settings.kosis_request_timeout_seconds,
            headers={"User-Agent": "price-analyzer/cpi-sync"},
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise InflationSeriesUnavailable(
            "KOSIS 소비자물가 연간 등락률을 갱신하지 못했습니다. "
            "기존 저장 자료는 변경되지 않았습니다."
        ) from exc
    rates = _validated_cpi_kosis_rates(payload)
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    existing = session.scalar(
        select(InflationSyncRun).where(InflationSyncRun.response_sha256 == fingerprint)
    )
    if existing is not None:
        return existing
    run = InflationSyncRun(
        series_kind=CPI_SERIES_KIND,
        provider="KOSIS",
        org_id=CPI_KOSIS_ORG_ID,
        table_id=CPI_KOSIS_TABLE_ID,
        item_id=CPI_KOSIS_ITEM_ID,
        classifier_code=CPI_KOSIS_CLASSIFIER_CODE,
        period_type="Y",
        unit=CPI_KOSIS_UNIT,
        source_url=CPI_KOSIS_SERIES_URL,
        source_last_changed=_source_last_changed(payload),
        response_sha256=fingerprint,
        row_count=len(rates),
        latest_period=max(rates),
    )
    session.add(run)
    session.flush()
    session.add_all(
        InflationIndexPoint(sync_run_id=run.id, period=year, index_value=rate)
        for year, rate in sorted(rates.items())
    )
    session.flush()
    return run


def latest_cpi_series(
    session: Session,
) -> tuple[InflationSyncRun | None, dict[str, Decimal]]:
    return _latest_series(session, CPI_SERIES_KIND)


def cpi_series_for_sync_run(
    session: Session,
    sync_run_id: int | None,
) -> tuple[InflationSyncRun | None, dict[str, Decimal]]:
    if sync_run_id is None:
        return None, {}
    run = session.get(InflationSyncRun, sync_run_id)
    if run is None or run.series_kind != CPI_SERIES_KIND:
        return None, {}
    return run, _series_points(session, run)


def _latest_series(
    session: Session,
    series_kind: str,
) -> tuple[InflationSyncRun | None, dict[str, Decimal]]:
    run = session.scalar(
        select(InflationSyncRun)
        .where(InflationSyncRun.series_kind == series_kind)
        .order_by(
            InflationSyncRun.fetched_at.desc(),
            InflationSyncRun.id.desc(),
        )
        .limit(1)
    )
    if run is None:
        return None, {}
    return run, _series_points(session, run)


def _series_points(
    session: Session,
    run: InflationSyncRun,
) -> dict[str, Decimal]:
    return {
        row.period: row.index_value
        for row in session.scalars(
            select(InflationIndexPoint)
            .where(InflationIndexPoint.sync_run_id == run.id)
            .order_by(InflationIndexPoint.period)
        )
    }


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
    # New analysis runs are CPI-only.  Legacy PPI snapshots remain readable
    # through their existing run records and API, but must never become a
    # fallback for a newly created purchase target.
    sync_run, annual_rates = latest_cpi_series(session)
    target_period = None if sync_run is None else sync_run.latest_period
    observations = _load_target_observations(
        session,
        {
            line.standard_price_version_id
            for line in analysis.lines
            if line.standard_price_version_id is not None
        },
    )
    target_lines = tuple(
        _target_line_from_cpi(
            line,
            observations.get(line.standard_price_version_id, ()),
            annual_rates,
            target_period,
            None if sync_run is None else sync_run.id,
        )
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
        target_index_value=None,
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
            target_unit_variance_amount=target.unit_variance_amount,
            target_used_observation_count=target.used_observation_count,
            target_excluded_observation_count=target.excluded_observation_count,
            target_reason=target.reason,
        )
        session.add(stored_line)
        session.flush()
        session.add_all(
            QuoteAnalysisTargetEvidence(
                line_result_id=stored_line.id,
                **_stored_target_evidence_fields(evidence),
            )
            for evidence in target.evidence
        )
    session.flush()
    return AnalysisRunResult(
        run_id=run.id,
        analysis=analysis,
        inflation_sync_run_id=None if sync_run is None else sync_run.id,
        inflation_series_kind=None if sync_run is None else sync_run.series_kind,
        target_period=target_period,
        target_index_value=None,
        inflation_source_url=CPI_KOSIS_SERIES_URL,
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


def _validated_cpi_kosis_rates(payload: object) -> dict[str, Decimal]:
    """Accept only KOSIS' annual all-items CPI change-rate series."""

    if not isinstance(payload, list):
        raise InflationSeriesUnavailable("KOSIS 소비자물가 응답 형식이 올바르지 않습니다.")
    rates: dict[str, Decimal] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        if (
            str(row.get("ORG_ID")) != CPI_KOSIS_ORG_ID
            or str(row.get("TBL_ID")) != CPI_KOSIS_TABLE_ID
            or str(row.get("ITM_ID")) != CPI_KOSIS_ITEM_ID
            or str(row.get("C1")) != CPI_KOSIS_CLASSIFIER_CODE
            # KOSIS accepts `Y` in the request but returns `A` for annual
            # observations.  Keep `Y` for fixture/backward compatibility.
            or str(row.get("PRD_SE")) not in {"A", "Y"}
        ):
            continue
        year = str(row.get("PRD_DE", ""))
        try:
            rate = Decimal(str(row.get("DT"))).quantize(MONEY_QUANTUM)
        except Exception:
            continue
        if (
            len(year) == 4
            and year.isdigit()
            and rate.is_finite()
            and rate > Decimal("-100")
        ):
            rates[year] = rate
    if not rates:
        raise InflationSeriesUnavailable(
            "KOSIS 응답에 유효한 연간 총지수 등락률이 없습니다."
        )
    return rates


def _source_last_changed(payload: object) -> date | None:
    if not isinstance(payload, list):
        return None
    changed_values: list[date] = []
    for row in payload:
        if not isinstance(row, dict) or not row.get("LST_CHN_DE"):
            continue
        try:
            changed_values.append(date.fromisoformat(str(row["LST_CHN_DE"])))
        except ValueError:
            continue
    return max(changed_values) if changed_values else None


def cpi_series_evidence(
    sync_run: InflationSyncRun | None,
    annual_rates: dict[str, Decimal],
) -> CpiInflationEvidenceResult | None:
    """Summarize the cached annual series from its first rate through latest."""

    if sync_run is None or not annual_rates:
        return None
    first_rate_year = min(annual_rates)
    return cpi_evidence_for_quote_year(
        sync_run.id,
        int(first_rate_year) - 1,
        annual_rates,
        sync_run.latest_period,
    )


def cpi_evidence_for_quote_year(
    sync_run_id: int,
    quote_year: int,
    annual_rates: dict[str, Decimal],
    latest_confirmed_year: str,
) -> CpiInflationEvidenceResult | None:
    """Return the exact CPI compounding inputs for one historic quote year."""

    inputs = _cpi_compounding_inputs(
        quote_year,
        annual_rates,
        latest_confirmed_year,
    )
    if inputs is None:
        return None
    applied_rates, factor = inputs
    return _cpi_evidence_from_compounding(
        sync_run_id,
        latest_confirmed_year,
        applied_rates,
        factor,
    )


def _cpi_compounding_inputs(
    quote_year: int,
    annual_rates: dict[str, Decimal],
    latest_confirmed_year: str,
) -> tuple[tuple[AnnualRateResult, ...], Decimal] | None:
    if (
        len(latest_confirmed_year) != 4
        or not latest_confirmed_year.isdigit()
        or quote_year > int(latest_confirmed_year)
    ):
        return None
    factor = Decimal("1")
    applied_rates: list[AnnualRateResult] = []
    for year_number in range(quote_year + 1, int(latest_confirmed_year) + 1):
        year = str(year_number)
        rate = annual_rates.get(year)
        if rate is None or not rate.is_finite() or rate <= Decimal("-100"):
            return None
        factor *= Decimal("1") + (rate / Decimal("100"))
        applied_rates.append(AnnualRateResult(year=year, rate=rate))
    return tuple(applied_rates), factor


def _cpi_evidence_from_compounding(
    sync_run_id: int,
    latest_confirmed_year: str,
    applied_rates: tuple[AnnualRateResult, ...],
    factor: Decimal,
) -> CpiInflationEvidenceResult:
    return CpiInflationEvidenceResult(
        sync_run_id=sync_run_id,
        latest_confirmed_year=latest_confirmed_year,
        annual_rates=applied_rates,
        factor=factor.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP),
        cumulative_percent=((factor - Decimal("1")) * Decimal("100")).quantize(
            MONEY_QUANTUM,
            rounding=ROUND_HALF_UP,
        ),
    )


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


def _unavailable_target_reason(line: AnalysisLine) -> tuple[str, str]:
    """Explain why a non-matched line cannot become a negotiation target."""

    if line.match_status == "REVIEW_REQUIRED":
        return (
            "NOT_APPLICABLE",
            "정제 검토가 끝나지 않아 원본 확인 후에 목표가를 산정할 수 있습니다.",
        )
    if line.match_status == "EXCLUDED":
        return (
            "NOT_APPLICABLE",
            "합계·소계 등 가격 비교 대상이 아닌 행이라 목표가 산정에서 제외했습니다.",
        )
    if line.market_price_lookup_required:
        return (
            "MARKET_REFERENCE_REQUIRED",
            "표준 DB에 기준 가격이 없어 외부 시장가를 별도로 확인합니다. 외부 가격은 구매 목표가로 자동 대입하지 않습니다.",
        )
    return (
        "NOT_APPLICABLE",
        "표준 DB 가격과 연결되지 않아 현재 근거만으로 목표가를 산정할 수 없습니다.",
    )


def _target_line(
    line: AnalysisLine,
    rows: tuple[tuple, ...],
    points: dict[str, Decimal],
    target_period: str | None,
    target_index: Decimal | None,
) -> TargetLineResult:
    if line.match_status != "MATCHED" or line.standard_price_version_id is None:
        status, reason = _unavailable_target_reason(line)
        return TargetLineResult(
            line.raw_item_id,
            status,
            None,
            None,
            None,
            None,
            0,
            0,
            reason,
            (),
        )
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
    unit_variance_amount = None
    variance_amount = None
    variance_percent = None
    if line.quote_unit_price is not None:
        unit_variance_amount = (line.quote_unit_price - target_unit).quantize(MONEY_QUANTUM)
    if line.quote_amount is not None and target_amount is not None:
        variance_amount = (line.quote_amount - target_amount).quantize(MONEY_QUANTUM)
        if target_amount != 0:
            variance_percent = (variance_amount / target_amount * Decimal("100")).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    reason = f"원본 날짜가 확인된 과거 단가 {len(evidence)}건을 {target_period[:4]}년 {int(target_period[4:])}월 물가 수준으로 보정했습니다."
    if len(evidence) == 1:
        reason += " 근거가 1건이므로 신뢰도가 낮습니다."
    return TargetLineResult(
        line.raw_item_id, "AVAILABLE", target_unit, target_amount, variance_amount, variance_percent,
        len(evidence), excluded, reason, tuple(evidence),
        unit_variance_amount=unit_variance_amount,
    )


def _target_line_from_cpi(
    line: AnalysisLine,
    rows: tuple[tuple, ...],
    annual_rates: dict[str, Decimal],
    latest_confirmed_year: str | None,
    sync_run_id: int | None,
) -> TargetLineResult:
    """Create a CPI-backed target without consulting the legacy PPI series."""

    if line.match_status != "MATCHED" or line.standard_price_version_id is None:
        status, reason = _unavailable_target_reason(line)
        return TargetLineResult(
            line.raw_item_id,
            status,
            None,
            None,
            None,
            None,
            0,
            0,
            reason,
            (),
        )
    if line.evidence_quality == "NON_COMPARABLE":
        return TargetLineResult(
            line.raw_item_id,
            "COMPARABILITY_REVIEW_REQUIRED",
            None,
            None,
            None,
            None,
            0,
            len(rows),
            "규격이 없고 과거 가격 범위가 넓어 같은 품목인지 먼저 확인해야 합니다.",
            (),
        )
    if latest_confirmed_year is None or sync_run_id is None:
        return TargetLineResult(
            line.raw_item_id,
            "INDEX_UNAVAILABLE",
            None,
            None,
            None,
            None,
            0,
            len(rows),
            "저장된 확정 소비자물가 연간 자료가 없어 목표가를 계산할 수 없습니다.",
            (),
        )

    evidence: list[TargetEvidenceResult] = []
    rate_gap_count = 0
    target_year = int(latest_confirmed_year)
    for row in rows:
        (
            raw_id,
            metadata_id,
            unit_price,
            quote_date,
            evidence_json,
            document_id,
            logical_name,
            variant_id,
            sheet,
            page,
            source_row,
            cells,
        ) = row
        if (
            metadata_id is None
            or unit_price is None
            or quote_date is None
            or not _is_source_confirmed(evidence_json)
            or quote_date.year > target_year
        ):
            continue
        inputs = _cpi_compounding_inputs(
            quote_date.year,
            annual_rates,
            latest_confirmed_year,
        )
        if inputs is None:
            rate_gap_count += 1
            continue
        applied_rates, exact_factor = inputs
        inflation = _cpi_evidence_from_compounding(
            sync_run_id,
            latest_confirmed_year,
            applied_rates,
            exact_factor,
        )
        adjusted = (unit_price * exact_factor).quantize(
            KRW_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        evidence.append(
            TargetEvidenceResult(
                raw_item_id=raw_id,
                metadata_version_id=metadata_id,
                source_document_id=document_id,
                source_variant_id=variant_id,
                source_logical_name=logical_name,
                source_sheet=sheet,
                source_page=page,
                source_row=source_row,
                source_cells=cells,
                quote_date=quote_date,
                source_period=str(quote_date.year),
                original_unit_price=unit_price,
                # These retained columns encode the baseline and final factor
                # for persistence compatibility with legacy PPI evidence.
                source_index_value=Decimal("1"),
                target_index_value=inflation.factor,
                adjusted_unit_price=adjusted,
                inflation=inflation,
            )
        )

    evidence = _independent_target_evidence(evidence)
    excluded = len(rows) - len(evidence)
    if not evidence:
        if rate_gap_count:
            return TargetLineResult(
                line.raw_item_id,
                "RATE_GAP",
                None,
                None,
                None,
                None,
                0,
                excluded,
                "필요한 연도의 소비자물가 등락률이 일부 누락되어 목표가를 계산할 수 없습니다.",
                (),
            )
        return TargetLineResult(
            line.raw_item_id,
            "DATE_UNAVAILABLE",
            None,
            None,
            None,
            None,
            0,
            excluded,
            "원본 본문·머리말에서 확인된 과거 견적일이 없어 목표가를 계산할 수 없습니다.",
            (),
        )

    # Price assessment uses the standard-price median as a neutral benchmark.
    # A purchase target instead represents the most aggressive price the
    # company has actually achieved, after putting all observations on the
    # same CPI basis.  Keep every observation for audit, but select the lowest
    # adjusted unit price as the negotiation target.
    evidence.sort(
        key=lambda item: (
            item.adjusted_unit_price,
            -item.quote_date.toordinal(),
            item.raw_item_id,
        )
    )
    target_unit = evidence[0].adjusted_unit_price.quantize(
        KRW_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    target_amount = (
        None
        if line.quantity is None
        else (target_unit * line.quantity).quantize(
            KRW_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
    )
    unit_variance_amount = None
    variance_amount = None
    variance_percent = None
    if line.quote_unit_price is not None:
        unit_variance_amount = (line.quote_unit_price - target_unit).quantize(
            KRW_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
    if line.quote_amount is not None and target_amount is not None:
        variance_amount = (line.quote_amount - target_amount).quantize(
            KRW_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        if target_amount != 0:
            variance_percent = (
                variance_amount / target_amount * Decimal("100")
            ).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    reason = (
        f"서로 다른 원본 견적의 과거 단가 {len(evidence)}건을 "
        f"{latest_confirmed_year}년 확정 소비자물가로 보정한 뒤 "
        "가장 낮은 금액을 구매 목표로 채택했습니다."
    )
    if rate_gap_count:
        reason += " 필요한 연간 등락률이 누락된 과거 근거는 계산에서 제외했습니다."
    if len(evidence) == 1:
        reason += " 근거가 1건뿐이므로 협상 시 신뢰도가 낮습니다."
    return TargetLineResult(
        line.raw_item_id,
        "AVAILABLE",
        target_unit,
        target_amount,
        variance_amount,
        variance_percent,
        len(evidence),
        excluded,
        reason,
        tuple(evidence),
        unit_variance_amount=unit_variance_amount,
    )


def _independent_target_evidence(
    evidence: list[TargetEvidenceResult],
) -> list[TargetEvidenceResult]:
    """Count a logical quote document once for negotiation evidence.

    Repeated pages or re-parsed rows from one submitted quote are not
    independent market observations.  For the aggressive negotiation target,
    retain the lowest CPI-adjusted line from each logical source document and
    keep the choice deterministic for audit/replay.
    """

    by_document: dict[int, TargetEvidenceResult] = {}
    for item in evidence:
        current = by_document.get(item.source_document_id)
        if current is None or (
            item.adjusted_unit_price,
            -item.quote_date.toordinal(),
            item.raw_item_id,
        ) < (
            current.adjusted_unit_price,
            -current.quote_date.toordinal(),
            current.raw_item_id,
        ):
            by_document[item.source_document_id] = item
    return list(by_document.values())


def _stored_target_evidence_fields(
    evidence: TargetEvidenceResult,
) -> dict[str, object]:
    """Exclude response-only CPI detail from the legacy immutable table shape."""

    return {
        key: value
        for key, value in evidence.__dict__.items()
        if key != "inflation"
    }


def _is_source_confirmed(evidence_json: str | None) -> bool:
    try:
        payload = json.loads(evidence_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return False
    quote_date = payload.get("quote_date") if isinstance(payload, dict) else None
    return isinstance(quote_date, dict) and quote_date.get("quality") == "SOURCE_CONFIRMED" and quote_date.get("use_for_index") == "EXACT_DATE"


def _sum_or_none(values: list[Decimal]) -> Decimal | None:
    return None if not values else sum(values, Decimal("0")).quantize(MONEY_QUANTUM)
