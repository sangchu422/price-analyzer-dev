"""Fetch and cache real procurement reference indicators."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import mean

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


@dataclass(frozen=True)
class IndicatorSnapshot:
    definition: IndicatorDefinition
    points: tuple[tuple[str, Decimal], ...]


DEFINITIONS = (
    IndicatorDefinition(
        "USD_KRW", "원/달러", "환율", "원/$",
        "미 연준 H.10 · FRED", "https://fred.stlouisfed.org/series/DEXKOUS", "DEXKOUS",
    ),
    IndicatorDefinition(
        "COPPER", "전기동", "원자재", "USD/톤",
        "IMF 원자재 가격 · FRED", "https://fred.stlouisfed.org/series/PCOPPUSDM", "PCOPPUSDM",
    ),
    IndicatorDefinition(
        "STEEL", "냉연강판", "원자재", "지수",
        "미 노동통계국 생산자물가 · FRED", "https://fred.stlouisfed.org/series/PCU3312213312211", "PCU3312213312211",
    ),
    IndicatorDefinition(
        "WAGE", "제조 임율", "임율", "원/시간",
        "고용노동부 사업체노동력조사 · KOSIS",
        "https://kosis.kr/statHtml/statHtml.do?orgId=118&tblId=DT_118N_MON054",
    ),
    IndicatorDefinition(
        "SEMICON", "반도체 수입가격지수", "시황", "지수",
        "미 노동통계국 수입물가 · FRED", "https://fred.stlouisfed.org/series/IR21320", "IR21320",
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
                for period, value in snapshot.points[-12:]
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
            monthly[date_text[:7]].append(value)
    points = tuple(
        (period, Decimal(str(mean(values))).quantize(Decimal("0.000001")))
        for period, values in sorted(monthly.items())[-12:]
    )
    if not points:
        raise ValueError("공개 시계열에 유효한 값이 없습니다.")
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
