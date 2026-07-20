"""
신뢰도 채점 — 헤더 및 라인 아이템 단위
"""
from config import MIN_AMOUNT

# 단위 정규화 테이블
_UNIT_MAP = {
    "ea": "EA", "개": "EA", "pcs": "EA", "pc": "EA", "개소": "EA",
    "set": "SET", "식": "SET", "세트": "SET",
    "m": "M", "미터": "M",
    "m2": "M2", "㎡": "M2",
    "kg": "KG", "킬로": "KG",
    "l": "L", "리터": "L",
    "식": "SET",
}


def normalize_unit(raw: str) -> str:
    if not raw:
        return ""
    key = raw.strip().lower().replace(" ", "")
    return _UNIT_MAP.get(key, raw.strip().upper())


def score_header(meta: dict) -> float:
    score = 1.0
    if not meta.get("vendor"):    score -= 0.15
    if not meta.get("quote_no"):  score -= 0.10
    if not meta.get("quote_date"):score -= 0.10
    if not meta.get("project"):   score -= 0.10
    return round(max(0.0, score), 2)


def score_item(item: dict) -> float:
    score = 1.0

    if not item.get("item_name"):               score -= 0.40
    elif len(item["item_name"]) < 2:            score -= 0.30

    if not item.get("unit_price") or item["unit_price"] <= 0:
        score -= 0.30

    if not item.get("quantity") or item["quantity"] <= 0:
        score -= 0.15

    if not item.get("unit"):                    score -= 0.05

    amount = item.get("amount") or 0
    qty    = item.get("quantity") or 0
    price  = item.get("unit_price") or 0

    # 수량 × 단가 ≈ 금액 검증
    if qty and price and amount:
        expected = qty * price
        if abs(expected - amount) > max(1, expected * 0.01):
            score -= 0.20

    # 최소 금액 필터
    if amount and amount < MIN_AMOUNT:
        score -= 0.15

    return round(max(0.0, score), 2)


def enrich_items(items: list[dict]) -> list[dict]:
    """단위 정규화 + 신뢰도 채점을 일괄 적용."""
    for item in items:
        item["unit_norm"] = normalize_unit(item.get("unit", ""))
        item["confidence"] = score_item(item)
    return items
