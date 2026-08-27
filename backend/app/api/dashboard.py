from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.core.config import settings
from app.procurement.dashboard import (
    dashboard_overview,
    family_indicator_impacts,
    price_trend,
)
from app.procurement.families import get_item_family, list_item_families
from app.procurement.indicators import indicator_cache_payloads, sync_procurement_indicators


router = APIRouter()


def _family_payload(family: dict[str, object]) -> dict[str, object]:
    payload = {key: value for key, value in family.items() if key != "members"}
    name = str(payload.get("name", "")).strip()
    payload["display_name"] = name[:-1].rstrip() if name.endswith("류") else name
    return payload


@router.get("/indicators")
def get_procurement_indicators(
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    return indicator_cache_payloads(session)


@router.post("/indicators/sync")
def post_procurement_indicator_sync(
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    sync_procurement_indicators(session, settings)
    session.commit()
    return indicator_cache_payloads(session)


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
            _family_payload(family)
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
        family = get_item_family(session, family_code)
        return {
            **_family_payload(family),
            "members": family.get("members", []),
            "indicator_impacts": family_indicator_impacts(family_code),
        }
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
