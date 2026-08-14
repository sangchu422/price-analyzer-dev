from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_session
from app.analysis.models import QuoteAnalysisRun
from app.analysis.service import market_lookup_eligibilities
from app.market.adapters import DeviceMartAdapter, MouserAdapter
from app.market.evidence import EvidenceStore
from app.market.models import MarketPriceObservation
from app.market.screenshot import PlaywrightScreenshotter
from app.market.schemas import (
    MarketBatchItemResponse,
    MarketBatchLookupRequest,
    MarketBatchLookupResponse,
    MarketLookupResponse,
    MarketPrecollectRequest,
    MarketPrecollectResponse,
)
from app.market.service import MarketLookupError, MarketLookupService


router = APIRouter()


def _market_worker_count(bind: object, eligible_count: int) -> int:
    if eligible_count <= 0:
        return 0
    dialect = getattr(getattr(bind, "dialect", None), "name", "")
    return 1 if dialect == "sqlite" else min(4, eligible_count)


def _service(session: Session) -> MarketLookupService:
    adapters = []
    if settings.devicemart_enabled:
        adapters.append(
            DeviceMartAdapter(
                base_url=settings.devicemart_base_url,
                timeout=settings.market_request_timeout_seconds,
                delay_seconds=settings.devicemart_request_delay_seconds,
            )
        )
    mouser_key = (
        settings.mouser_api_key.get_secret_value().strip()
        if settings.mouser_api_key is not None
        else ""
    )
    if mouser_key:
        adapters.append(
            MouserAdapter(
                api_key=mouser_key,
                base_url=settings.mouser_api_base_url,
                timeout=settings.market_request_timeout_seconds,
            )
        )
    return MarketLookupService(session, settings, adapters, PlaywrightScreenshotter())


@router.post(
    "/lookup/{raw_item_id}",
    response_model=MarketLookupResponse,
)
def lookup_market_price(
    raw_item_id: int,
    analysis_run_id: int = Query(...),
    force_refresh: bool = Query(False),
    session: Session = Depends(get_session),
) -> MarketLookupResponse:
    run = session.get(QuoteAnalysisRun, analysis_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="분석 실행 이력을 찾을 수 없습니다.")
    try:
        return _service(session).lookup_raw_item(
            raw_item_id,
            force_refresh=force_refresh,
            review_percent=run.review_percent,
            high_percent=run.high_percent,
        )
    except MarketLookupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _automatic_lookup(
    raw_item_id: int,
    force_refresh: bool,
    bind: object,
    review_percent: Decimal,
    high_percent: Decimal,
) -> MarketBatchItemResponse:
    with Session(
        bind=bind,
        autoflush=False,
        expire_on_commit=False,
    ) as worker_session:
        try:
            result = _service(worker_session).lookup_raw_item(
                raw_item_id,
                force_refresh=force_refresh,
                automatic=True,
                review_percent=review_percent,
                high_percent=high_percent,
            )
        except MarketLookupError as exc:
            return MarketBatchItemResponse(
                raw_item_id=raw_item_id,
                status="SOURCE_UNAVAILABLE",
                detail=str(exc),
            )
        detail_by_outcome = {
            "CACHE_HIT": "저장된 시장가 근거를 적용했습니다.",
            "LIVE_HIT": "DeviceMart·Mouser에서 시장가 근거를 수집했습니다.",
            "REFERENCE_ONLY": "유사 상품은 찾았지만 자동 판정 조건을 충족하지 못했습니다.",
            "NO_REFERENCE": "두 출처에서 일치하는 시장가 근거를 찾지 못했습니다.",
            "SOURCE_UNAVAILABLE": "사용 가능한 시장가 출처가 없거나 조회에 실패했습니다.",
        }
        return MarketBatchItemResponse(
            raw_item_id=raw_item_id,
            status=result.outcome,
            detail=detail_by_outcome[result.outcome],
            result=result,
        )


@router.post("/lookup-batch", response_model=MarketBatchLookupResponse)
def lookup_market_prices_automatically(
    request: MarketBatchLookupRequest,
    session: Session = Depends(get_session),
) -> MarketBatchLookupResponse:
    run = session.get(QuoteAnalysisRun, request.analysis_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="분석 실행 이력을 찾을 수 없습니다.")
    raw_ids = list(dict.fromkeys(request.raw_item_ids))
    eligibility = market_lookup_eligibilities(session, raw_ids)
    by_id: dict[int, MarketBatchItemResponse] = {}
    eligible_ids: list[int] = []
    for item in eligibility:
        if item.status == "ELIGIBLE":
            eligible_ids.append(item.raw_item_id)
            continue
        by_id[item.raw_item_id] = MarketBatchItemResponse(
            raw_item_id=item.raw_item_id,
            status=item.status,
            detail=item.detail,
        )
    if eligible_ids:
        # Each automatic lookup persists its own cache/evidence transaction. SQLite
        # only permits one writer at a time, so using multiple worker sessions can
        # turn an otherwise valid source lookup into ``database is locked``. Keep
        # the four-worker path for server databases and serialize local SQLite
        # writes; one worker still satisfies the "up to four" concurrency limit.
        bind = session.get_bind()
        max_workers = _market_worker_count(bind, len(eligible_ids))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _automatic_lookup,
                    raw_id,
                    request.force_refresh,
                    bind,
                    run.review_percent,
                    run.high_percent,
                ): raw_id
                for raw_id in eligible_ids
            }
            for future in as_completed(futures):
                raw_id = futures[future]
                try:
                    by_id[raw_id] = future.result()
                except Exception as exc:
                    by_id[raw_id] = MarketBatchItemResponse(
                        raw_item_id=raw_id,
                        status="SOURCE_UNAVAILABLE",
                        detail="시장가 조회 중 일시적인 저장 오류가 발생했습니다. 다시 조회해 주세요.",
                    )
    items = [by_id[raw_id] for raw_id in raw_ids]
    unavailable_statuses = {
        "NO_REFERENCE",
        "SOURCE_UNAVAILABLE",
        "CLEANING_REQUIRED",
        "EXCLUDED",
        "NOT_FOUND",
    }
    return MarketBatchLookupResponse(
        items=items,
        completed=sum(item.status not in unavailable_statuses for item in items),
        unavailable=sum(item.status in unavailable_statuses for item in items),
    )


@router.post("/precollect", response_model=MarketPrecollectResponse)
def precollect_market_prices(
    request: MarketPrecollectRequest,
    session: Session = Depends(get_session),
) -> MarketPrecollectResponse:
    service = _service(session)
    completed = unavailable = 0
    for query in request.queries:
        result = service.lookup(
            query,
            force_refresh=request.force_refresh,
        )
        if result.cache_state == "UNAVAILABLE":
            unavailable += 1
        else:
            completed += 1
    return MarketPrecollectResponse(
        completed=completed,
        unavailable=unavailable,
    )


@router.get("/evidence/{observation_id}/{kind}")
def market_evidence(
    observation_id: int,
    kind: str,
    session: Session = Depends(get_session),
) -> FileResponse:
    observation = session.get(MarketPriceObservation, observation_id)
    if observation is None:
        raise HTTPException(status_code=404, detail="증빙을 찾을 수 없습니다.")
    paths = {
        "raw": observation.raw_evidence_path,
        "image": observation.image_evidence_path,
        "screenshot": observation.screenshot_evidence_path,
    }
    relative_path = paths.get(kind)
    if relative_path is None:
        raise HTTPException(status_code=404, detail="증빙을 찾을 수 없습니다.")
    store = EvidenceStore(settings.market_evidence_path)
    try:
        path = store.resolve(relative_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="잘못된 증빙 경로입니다.") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="증빙 파일이 없습니다.")
    return FileResponse(path, filename=Path(path).name)
