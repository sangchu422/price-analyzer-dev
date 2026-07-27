from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

import httpx

from app.market.adapters.base import CollectedProduct, CollectedTier
from app.market.models import MarketSource


class MouserAdapter:
    source = MarketSource.MOUSER

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client = client

    def search(self, query: str) -> list[CollectedProduct]:
        payload = {
            "SearchByKeywordRequest": {
                "keyword": query,
                "records": 10,
                "startingRecord": 0,
                "searchOptions": "None",
                "searchWithYourSignUpLanguage": "None",
            }
        }
        raw_client = self.client or httpx.Client(timeout=self.timeout)
        close_client = self.client is None
        try:
            response = raw_client.post(
                f"{self.base_url}/search/keyword",
                params={"apiKey": self.api_key},
                json=payload,
            )
            response.raise_for_status()
            raw = response.content
            data = response.json()
        finally:
            if close_client:
                raw_client.close()

        errors = data.get("Errors") or []
        if errors:
            raise RuntimeError(
                "; ".join(str(error.get("Message", error)) for error in errors)
            )
        parts = (data.get("SearchResults") or {}).get("Parts") or []
        products: list[CollectedProduct] = []
        for part in parts:
            tiers = tuple(
                tier
                for price_break in part.get("PriceBreaks") or []
                if (
                    tier := _tier_from_price_break(
                        price_break,
                        part.get("Currency") or "KRW",
                    )
                )
                is not None
            )
            if not tiers:
                continue
            first = min(tiers, key=lambda tier: tier.minimum_quantity)
            product_url = (
                part.get("ProductDetailUrl")
                or part.get("DataSheetUrl")
                or "https://www.mouser.kr"
            )
            source_id = str(
                part.get("MouserPartNumber")
                or part.get("ManufacturerPartNumber")
                or product_url
            )
            products.append(
                CollectedProduct(
                    source=self.source,
                    source_product_id=source_id,
                    title=str(
                        part.get("Description")
                        or part.get("ManufacturerPartNumber")
                        or source_id
                    ),
                    manufacturer=part.get("Manufacturer"),
                    model_number=part.get("ManufacturerPartNumber"),
                    product_url=product_url,
                    image_url=part.get("ImagePath"),
                    currency=first.currency,
                    unit_price=first.unit_price,
                    raw_payload=raw,
                    raw_extension=".json",
                    stock_quantity=_integer(part.get("AvailabilityInStock")),
                    stock_text=part.get("Availability"),
                    moq=_integer(part.get("Min")),
                    vat_note="Mouser 표시 가격 기준",
                    shipping_note="배송비·관부가세는 주문 조건에 따라 별도",
                    tiers=tiers,
                )
            )
        return products


def _tier_from_price_break(
    price_break: dict[str, object],
    default_currency: str,
) -> CollectedTier | None:
    quantity = _integer(price_break.get("Quantity"))
    price = _decimal(price_break.get("Price"))
    if not quantity or price is None or price <= 0:
        return None
    currency = str(price_break.get("Currency") or default_currency or "KRW")
    return CollectedTier(quantity, price, currency)


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    normalized = re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))
    if not normalized:
        return None
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _integer(value: object) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d+", str(value).replace(",", ""))
    return int(match.group()) if match else None
