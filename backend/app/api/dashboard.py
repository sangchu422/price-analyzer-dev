from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.procurement.dashboard import dashboard_overview, price_trend


router = APIRouter()


@router.get("/overview")
def get_dashboard_overview(
    session: Session = Depends(get_session),
) -> dict[str, object]:
    return dashboard_overview(session)


@router.get("/standard-items/{standard_item_id}/price-trend")
def get_standard_item_price_trend(
    standard_item_id: int,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        return price_trend(session, standard_item_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
