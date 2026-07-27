from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_session
from app.market.adapters import DeviceMartAdapter, MouserAdapter
from app.market.evidence import EvidenceStore
from app.market.models import MarketPriceObservation
from app.market.schemas import (
    MarketLookupResponse,
    MarketPrecollectRequest,
    MarketPrecollectResponse,
)
from app.market.service import MarketLookupError, MarketLookupService


router = APIRouter()


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
    return MarketLookupService(session, settings, adapters)


@router.post(
    "/lookup/{raw_item_id}",
    response_model=MarketLookupResponse,
)
def lookup_market_price(
    raw_item_id: int,
    force_refresh: bool = Query(False),
    session: Session = Depends(get_session),
) -> MarketLookupResponse:
    try:
        return _service(session).lookup_raw_item(
            raw_item_id,
            force_refresh=force_refresh,
        )
    except MarketLookupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
