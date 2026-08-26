from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.procurement.dashboard import dashboard_overview, price_trend
from app.procurement.families import get_item_family, list_item_families


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


@router.get("/item-families")
def get_item_families(
    session: Session = Depends(get_session),
    *,
    search: str | None = Query(None, max_length=200),
    category: str | None = Query(None, max_length=64),
) -> dict[str, object]:
    families = list_item_families(session, search=search, category_code=category)
    return {
        "families": [
            {key: value for key, value in family.items() if key != "members"}
            for family in families
        ],
        "family_count": len(families),
        "item_count": sum(int(family["item_count"]) for family in families),
        "rule_version": "item-family-keyword-v1",
    }


@router.get("/item-families/{family_code}")
def get_item_family_detail(
    family_code: str,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        return get_item_family(session, family_code)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
