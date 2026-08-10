from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import re
from typing import Protocol

from app.market.models import MarketSource


@dataclass(frozen=True)
class CollectedTier:
    minimum_quantity: int
    unit_price: Decimal
    currency: str


@dataclass(frozen=True)
class CollectedProduct:
    source: MarketSource
    source_product_id: str
    title: str
    product_url: str
    currency: str
    unit_price: Decimal
    raw_payload: bytes
    raw_extension: str
    manufacturer: str | None = None
    model_number: str | None = None
    image_url: str | None = None
    image_bytes: bytes | None = None
    image_extension: str | None = None
    screenshot_bytes: bytes | None = None
    stock_quantity: int | None = None
    stock_text: str | None = None
    moq: int | None = None
    vat_note: str | None = None
    shipping_note: str | None = None
    tiers: tuple[CollectedTier, ...] = field(default_factory=tuple)


class MarketAdapter(Protocol):
    source: MarketSource

    def search(self, query: str) -> list[CollectedProduct]: ...


_GENERIC_SEARCH_TOKENS = {
    "ASSY",
    "AXIS",
    "CONTROL",
    "FOR",
    "MOTOR",
    "PANEL",
    "PART",
    "SERVO",
    "SET",
    "SYSTEM",
    "UNIT",
}
_MEASUREMENT_TOKEN = re.compile(
    r"^\d+(?:\.\d+)?(?:A|CM|EA|HP|HZ|KG|KW|M|MM|V|W)$",
    re.IGNORECASE,
)


def market_model_tokens(query: str) -> tuple[str, ...]:
    tokens = re.findall(r"[0-9A-Z가-힣][0-9A-Z가-힣._/-]+", query.upper())
    return tuple(
        token
        for token in tokens
        if len(token) >= 4
        and any(character.isalpha() for character in token)
        and any(character.isdigit() for character in token)
        and not _MEASUREMENT_TOKEN.fullmatch(token)
    )


def market_meaningful_tokens(query: str) -> tuple[str, ...]:
    tokens = re.findall(r"[0-9A-Z가-힣][0-9A-Z가-힣._/-]+", query.upper())
    return tuple(
        token
        for token in tokens
        if len(token) >= 2 and not _MEASUREMENT_TOKEN.fullmatch(token)
    )


def search_query_variants(query: str) -> tuple[str, ...]:
    normalized = " ".join(query.upper().split())
    variants = [normalized]
    for token in market_model_tokens(normalized):
        expanded = " ".join(part for part in re.split(r"[^0-9A-Z가-힣]+", token) if part)
        if expanded and expanded != normalized:
            variants.append(expanded)
        if token != normalized:
            variants.append(token)
    tokens = re.findall(r"[0-9A-Z가-힣][0-9A-Z가-힣._/-]+", normalized)
    for token in tokens:
        if (
            len(token) >= 5
            and token not in _GENERIC_SEARCH_TOKENS
            and token not in variants
        ):
            variants.append(token)
    return tuple(dict.fromkeys(variants))[:3]
