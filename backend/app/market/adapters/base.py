from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
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
