from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.market.models import MarketSource


MarketAssessment = Literal["LOW", "WITHIN_RANGE", "REVIEW", "HIGH", "REVIEW_REQUIRED"]
MarketCacheState = Literal["CACHE", "LIVE", "PARTIAL", "UNAVAILABLE"]
MarketLookupOutcome = Literal[
    "CACHE_HIT",
    "LIVE_HIT",
    "REFERENCE_ONLY",
    "NO_REFERENCE",
    "SOURCE_UNAVAILABLE",
]
MarketBatchStatus = Literal[
    "STANDARD_APPLIED",
    "IDENTIFIER_REQUIRED",
    "CACHE_HIT",
    "LIVE_HIT",
    "REFERENCE_ONLY",
    "NO_REFERENCE",
    "SOURCE_UNAVAILABLE",
    "CLEANING_REQUIRED",
    "EXCLUDED",
    "NOT_FOUND",
]


class MarketTierResponse(BaseModel):
    minimum_quantity: int
    unit_price: Decimal
    currency: str


class MarketProductResponse(BaseModel):
    observation_id: int
    source: MarketSource
    title: str
    manufacturer: str | None
    model_number: str | None
    product_url: str
    image_url: str | None
    currency: str
    applicable_unit_price: Decimal
    stock_quantity: int | None
    stock_text: str | None
    moq: int | None
    vat_note: str | None
    shipping_note: str | None
    collected_at: datetime
    expires_at: datetime
    is_stale: bool
    tiers: list[MarketTierResponse]
    image_evidence_url: str | None
    raw_evidence_url: str
    screenshot_evidence_url: str | None
    automatic_price_eligible: bool = False
    automatic_price_exclusion_reasons: list[str] = Field(default_factory=list)


class MarketSourceFailure(BaseModel):
    source: MarketSource
    detail: str


class MarketLookupResponse(BaseModel):
    raw_item_id: int
    query: str
    quote_unit_price: Decimal | None
    quantity: Decimal | None
    cache_state: MarketCacheState
    assessment: MarketAssessment
    minimum_price: Decimal | None
    median_price: Decimal | None
    maximum_price: Decimal | None
    variance_percent: Decimal | None
    products: list[MarketProductResponse]
    source_failures: list[MarketSourceFailure]
    outcome: MarketLookupOutcome
    automatic_price_product_count: int = 0


class MarketBatchLookupRequest(BaseModel):
    analysis_run_id: int
    raw_item_ids: list[int] = Field(min_length=1, max_length=100)
    force_refresh: bool = False


class MarketBatchItemResponse(BaseModel):
    raw_item_id: int
    status: MarketBatchStatus
    detail: str
    result: MarketLookupResponse | None = None


class MarketBatchLookupResponse(BaseModel):
    items: list[MarketBatchItemResponse]
    completed: int
    unavailable: int


class MarketPrecollectRequest(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=100)
    force_refresh: bool = False


class MarketPrecollectResponse(BaseModel):
    completed: int
    unavailable: int
