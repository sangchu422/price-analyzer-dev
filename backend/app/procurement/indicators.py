"""Fetch and cache real procurement reference indicators."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from statistics import mean
from urllib.parse import quote

import httpx
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.procurement.models import ProcurementIndicatorPoint, ProcurementIndicatorSyncRun


INDICATOR_CACHE_TTL = timedelta(hours=24)


@dataclass(frozen=True)
class IndicatorDefinition:
    code: str
    name: str
    group: str
    unit: str
    source_label: str
    source_url: str
    fred_series: str | None = None
    yahoo_symbol: str | None = None
    value_multiplier: Decimal = Decimal("1")
    frequency: str = "MONTHLY"
    point_limit: int = 12


@dataclass(frozen=True)
class IndicatorSnapshot:
    definition: IndicatorDefinition
    points: tuple[tuple[str, Decimal], ...]


DEFINITIONS = (
    IndicatorDefinition(
        "USD_KRW", "원/달러", "환율", "원/$",
        "미 연준 H.10 · FRED", "https://fred.stlouisfed.org/series/DEXKOUS", "DEXKOUS",
        frequency="DAILY", point_limit=30,
    ),
    IndicatorDefinition(
        "COPPER", "전기동", "원자재", "USD/톤",
        "COMEX 전기동 선물 · Yahoo Finance", "https://finance.yahoo.com/quote/HG%3DF/",
        yahoo_symbol="HG=F", value_multiplier=Decimal("2204.62262185"),
        frequency="DAILY", point_limit=30,
    ),
    IndicatorDefinition(
        "STEEL", "열연코일 선물", "원자재", "USD/short ton",
        "미 중서부 열연코일 선물 · Yahoo Finance", "https://finance.yahoo.com/quote/HRC%3DF/",
        yahoo_symbol="HRC=F", frequency="DAILY", point_limit=30,
    ),
    IndicatorDefinition(
        "WAGE", "제조 임율", "임율", "원/시간",
        "고용노동부 사업체노동력조사 · KOSIS",
        "https://kosis.kr/statHtml/statHtml.do?orgId=118&tblId=DT_118N_MON054",
    ),
    IndicatorDefinition(
        "SEMICON", "반도체 시황지수", "시황", "지수",
        "PHLX 반도체지수 · Yahoo Finance", "https://finance.yahoo.com/quote/%5ESOX/",
        yahoo_symbol="^SOX", frequency="DAILY", point_limit=30,
    ),
    IndicatorDefinition(
        "CPI_ALL", "소비자물가 총지수", "시황", "2020=100",
        "국가통계포털 소비자물가지수 · KOSIS",
        "https://kosis.kr/statHtml/statHtml.do?orgId=101&tblId=DT_1J22003",
    ),
)


def sync_procurement_indicators(
    session: Session,
    settings: Settings,
) -> list[ProcurementIndicatorSyncRun]:
    snapshots: dict[str, IndicatorSnapshot] = {}
    errors: dict[str, str] = {}

    def fetch(definition: IndicatorDefinition) -> IndicatorSnapshot:
        if definition.code == "WAGE":
            return _fetch_wage(definition, settings)
        if definition.code == "CPI_ALL":
            return _fetch_monthly_cpi(definition, settings)
        if definition.yahoo_symbol is not None:
            return _fetch_yahoo_market(definition, settings)
        return _fetch_fred(definition, settings)

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(fetch, definition): definition for definition in DEFINITIONS}
        for future in as_completed(futures):
            definition = futures[future]
            try:
                snapshots[definition.code] = future.result()
            except Exception as exc:  # preserve the last good cache on every provider failure
                errors[definition.code] = f"{type(exc).__name__}: {exc}"

    runs: list[ProcurementIndicatorSyncRun] = []
    for definition in DEFINITIONS:
        snapshot = snapshots.get(definition.code)
        run = ProcurementIndicatorSyncRun(
            indicator_code=definition.code,
            status="SUCCEEDED" if snapshot is not None else "FAILED",
            source_label=definition.source_label,
            source_url=definition.source_url,
            unit=definition.unit,
            error_detail=errors.get(definition.code),
        )
        session.add(run)
        session.flush()
        if snapshot is not None:
            session.add_all(
                ProcurementIndicatorPoint(sync_run_id=run.id, period=period, value=value)
                for period, value in snapshot.points[-definition.point_limit:]
            )
        runs.append(run)
    session.flush()
    return runs


def indicator_cache_payloads(session: Session, *, now: datetime | None = None) -> list[dict[str, object]]:
    now = now or datetime.now()
    schema = inspect(session.get_bind())
    if not (
        schema.has_table(ProcurementIndicatorSyncRun.__tablename__)
        and schema.has_table(ProcurementIndicatorPoint.__tablename__)
    ):
        return [_unavailable_payload(definition) for definition in DEFINITIONS]
    payloads: list[dict[str, object]] = []
    for definition in DEFINITIONS:
        latest_attempt = session.scalar(
            select(ProcurementIndicatorSyncRun)
            .where(ProcurementIndicatorSyncRun.indicator_code == definition.code)
            .order_by(ProcurementIndicatorSyncRun.id.desc())
            .limit(1)
        )
        latest_success = session.scalar(
            select(ProcurementIndicatorSyncRun)
            .where(
                ProcurementIndicatorSyncRun.indicator_code == definition.code,
                ProcurementIndicatorSyncRun.status == "SUCCEEDED",
            )
            .order_by(ProcurementIndicatorSyncRun.id.desc())
            .limit(1)
        )
        points = [] if latest_success is None else list(
            session.scalars(
                select(ProcurementIndicatorPoint)
                .where(ProcurementIndicatorPoint.sync_run_id == latest_success.id)
                .order_by(ProcurementIndicatorPoint.period)
            )
        )
        stale = (
            latest_success is not None
            and (
                now - latest_success.synced_at > INDICATOR_CACHE_TTL
                or (latest_attempt is not None and latest_attempt.status == "FAILED")
            )
        )
        payloads.append(
            {
                "code": definition.code,
                "name": definition.name,
                "group": definition.group,
                "unit": definition.unit,
                "source_status": (
                    "UNAVAILABLE" if latest_success is None else "STALE" if stale else "LIVE_CACHE"
                ),
                "source_label": definition.source_label,
                "source_url": definition.source_url,
                "source_frequency": definition.frequency,
                "latest_period": None if not points else points[-1].period,
                "synced_at": None if latest_success is None else latest_success.synced_at.isoformat(),
                "error_detail": None if latest_attempt is None else latest_attempt.error_detail,
                "points": [{"period": point.period, "value": point.value} for point in points],
            }
        )
    return payloads


def _unavailable_payload(definition: IndicatorDefinition) -> dict[str, object]:
    return {
        "code": definition.code,
        "name": definition.name,
        "group": definition.group,
        "unit": definition.unit,
        "source_status": "UNAVAILABLE",
        "source_label": definition.source_label,
        "source_url": definition.source_url,
        "source_frequency": definition.frequency,
        "latest_period": None,
        "synced_at": None,
        "error_detail": "지표 저장소 마이그레이션이 필요합니다.",
        "points": [],
    }


def indicator_sync_required(session: Session, *, now: datetime | None = None) -> bool:
    return any(
        payload["source_status"] in {"UNAVAILABLE", "STALE"}
        for payload in indicator_cache_payloads(session, now=now)
    )


def _fetch_fred(definition: IndicatorDefinition, settings: Settings) -> IndicatorSnapshot:
    assert definition.fred_series is not None
    response = httpx.get(
        "https://fred.stlouisfed.org/graph/fredgraph.csv",
        params={"id": definition.fred_series},
        timeout=min(settings.kosis_request_timeout_seconds, 15.0),
        headers={"User-Agent": "price-analyzer/procurement-indicators"},
    )
    response.raise_for_status()
    rows = csv.DictReader(io.StringIO(response.text))
    daily: dict[str, Decimal] = {}
    monthly: dict[str, list[Decimal]] = defaultdict(list)
    for row in rows:
        date_text = str(row.get("DATE") or row.get("observation_date") or "")
        raw = row.get(definition.fred_series)
        if len(date_text) < 7 or raw in {None, "", "."}:
            continue
        try:
            value = Decimal(str(raw))
        except InvalidOperation:
            continue
        if value.is_finite():
            if definition.frequency == "DAILY" and len(date_text) >= 10:
                daily[date_text[:10]] = value
            else:
                monthly[date_text[:7]].append(value)
    if definition.frequency == "DAILY":
        points = tuple(
            (period, value.quantize(Decimal("0.000001")))
            for period, value in sorted(daily.items())[-definition.point_limit:]
        )
    else:
        points = tuple(
            (period, Decimal(str(mean(values))).quantize(Decimal("0.000001")))
            for period, values in sorted(monthly.items())[-definition.point_limit:]
        )
    if not points:
        raise ValueError("공개 시계열에 유효한 값이 없습니다.")
    return IndicatorSnapshot(definition, points)


def _fetch_yahoo_market(definition: IndicatorDefinition, settings: Settings) -> IndicatorSnapshot:
    """Read recent daily closes from Yahoo's public chart endpoint.

    Market series are kept as negotiation signals only. A failed request does
    not overwrite the last successful cache, so a provider outage cannot turn
    into a fabricated current price.
    """

    assert definition.yahoo_symbol is not None
    response = httpx.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(definition.yahoo_symbol, safe='')}",
        params={"range": "3mo", "interval": "1d", "events": "history"},
        timeout=min(settings.kosis_request_timeout_seconds, 15.0),
        headers={"User-Agent": "Mozilla/5.0 price-analyzer/procurement-indicators"},
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("chart", {}).get("result") if isinstance(payload, dict) else None
    if not results:
        raise ValueError("일별 시장지표 응답에 유효한 결과가 없습니다.")
    result = results[0]
    timestamps = result.get("timestamp") or []
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quotes.get("close") or []
    daily: dict[str, Decimal] = {}
    for timestamp, raw in zip(timestamps, closes, strict=False):
        if raw is None:
            continue
        try:
            value = Decimal(str(raw)) * definition.value_multiplier
            period = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).date().isoformat()
        except (InvalidOperation, TypeError, ValueError, OSError, OverflowError):
            continue
        if value.is_finite() and value > 0:
            daily[period] = value.quantize(Decimal("0.000001"))
    points = tuple(sorted(daily.items())[-definition.point_limit:])
    if not points:
        raise ValueError("일별 시장지표에 유효한 종가가 없습니다.")
    return IndicatorSnapshot(definition, points)


def _fetch_wage(definition: IndicatorDefinition, settings: Settings) -> IndicatorSnapshot:
    endpoint = f"{settings.kosis_proxy_base_url.rstrip('/')}/v1/kosis/data"
    table_specs = (
        ("DT_118N_MON051", "190326INDUSTRY_10SC", "202501", "202512"),
        ("DT_118N_MON054", "260225INDUSTRY_11SC", "202601", f"{datetime.now():%Y%m}"),
    )
    values: dict[str, dict[str, Decimal]] = defaultdict(dict)
    for table_id, industry_code, start, end in table_specs:
        for item_id, field in (
            ("13103110311MD_7", "hours"),
            ("13103110311MD_12", "wages"),
        ):
            response = httpx.get(
                endpoint,
                params={
                    "method": "getList",
                    "format": "json",
                    "jsonVD": "Y",
                    "orgId": "118",
                    "tblId": table_id,
                    "itmId": item_id,
                    "prdSe": "M",
                    "startPrdDe": start,
                    "endPrdDe": end,
                    "objL1": industry_code,
                    "objL2": "size01",
                },
                timeout=min(settings.kosis_request_timeout_seconds, 15.0),
                headers={"User-Agent": "price-analyzer/wage-sync"},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("KOSIS 임금 응답 형식이 올바르지 않습니다.")
            for row in payload:
                if not isinstance(row, dict) or str(row.get("ITM_ID")) != item_id:
                    continue
                period = str(row.get("PRD_DE", ""))
                try:
                    number = Decimal(str(row.get("DT")))
                except InvalidOperation:
                    continue
                if len(period) == 6 and number.is_finite() and number > 0:
                    values[period][field] = number
    points = tuple(
        (period, (parts["wages"] / parts["hours"]).quantize(Decimal("0.000001")))
        for period, parts in sorted(values.items())
        if parts.get("wages") and parts.get("hours")
    )[-12:]
    if not points:
        raise ValueError("KOSIS 제조업 임금·근로시간 자료가 없습니다.")
    return IndicatorSnapshot(definition, points)


def _fetch_monthly_cpi(definition: IndicatorDefinition, settings: Settings) -> IndicatorSnapshot:
    endpoint = f"{settings.kosis_proxy_base_url.rstrip('/')}/v1/kosis/data"
    response = httpx.get(
        endpoint,
        params={
            "method": "getList",
            "format": "json",
            "jsonVD": "Y",
            "orgId": "101",
            "tblId": "DT_1J22003",
            "itmId": "T",
            "prdSe": "M",
            "startPrdDe": f"{datetime.now().year - 1}01",
            "endPrdDe": f"{datetime.now():%Y%m}",
            "objL1": "T10",
        },
        timeout=min(settings.kosis_request_timeout_seconds, 15.0),
        headers={"User-Agent": "price-analyzer/cpi-dashboard-sync"},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("KOSIS 소비자물가 응답 형식이 올바르지 않습니다.")
    monthly: dict[str, Decimal] = {}
    for row in payload:
        if not isinstance(row, dict) or str(row.get("ITM_ID")) != "T":
            continue
        period = str(row.get("PRD_DE", ""))
        try:
            value = Decimal(str(row.get("DT")))
        except InvalidOperation:
            continue
        if len(period) == 6 and value.is_finite() and value > 0:
            monthly[period] = value.quantize(Decimal("0.000001"))
    points = tuple(sorted(monthly.items())[-definition.point_limit:])
    if not points:
        raise ValueError("KOSIS 월별 소비자물가 총지수 자료가 없습니다.")
    return IndicatorSnapshot(definition, points)
