from __future__ import annotations

import html
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode, urljoin

import httpx

from app.market.adapters.base import CollectedProduct, CollectedTier
from app.market.models import MarketSource


class DeviceMartAdapter:
    source = MarketSource.DEVICEMART

    def __init__(
        self,
        *,
        base_url: str = "https://www.devicemart.co.kr",
        timeout: float = 15.0,
        delay_seconds: float = 1.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.delay_seconds = delay_seconds
        self.client = client

    def search(self, query: str) -> list[CollectedProduct]:
        client = self.client or httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": "PriceAnalyzer/1.0 (+market evidence)"},
        )
        close_client = self.client is None
        try:
            search_url = (
                f"{self.base_url}/goods/search_sse_stream?"
                f"{urlencode({'search_text': query})}"
            )
            response = client.get(search_url)
            response.raise_for_status()
            return parse_sse_products(response.content, self.base_url)[:10]
        finally:
            if close_client:
                client.close()


def parse_sse_products(raw: bytes, base_url: str) -> list[CollectedProduct]:
    source = raw.decode("utf-8", errors="replace")
    event_name = ""
    products: list[CollectedProduct] = []
    for line in source.splitlines():
        if line.startswith("event:"):
            event_name = line.partition(":")[2].strip()
            continue
        if event_name != "sphinx_result" or not line.startswith("data:"):
            continue
        try:
            payload = json.loads(line.partition(":")[2].strip())
        except json.JSONDecodeError:
            continue
        records = ((payload.get("list") or {}).get("record") or [])
        for record in records:
            product = _product_from_sse_record(record, base_url)
            if product is not None:
                products.append(product)
        event_name = ""
    return products


def _product_from_sse_record(
    record: dict[str, object],
    base_url: str,
) -> CollectedProduct | None:
    source_id = str(record.get("goods_seq") or "")
    title = str(record.get("goods_name") or "").strip()
    price = _decimal(record.get("sale_price") or record.get("price"))
    if not source_id or not title or price is None or price <= 0:
        return None
    image = record.get("image")
    stock = _integer(record.get("stock"))
    moq = _integer(record.get("min_purchase_ea")) or 1
    shipping = record.get("shipping_group")
    shipping_note = None
    if isinstance(shipping, dict):
        first_cost = _decimal(shipping.get("first_cost"))
        shipping_note = (
            f"기본 배송비 {first_cost:,.0f}원"
            if first_cost is not None
            else str(shipping.get("default_type") or "") or None
        )
    raw_record = json.dumps(
        {
            "source_endpoint": "/goods/search_sse_stream",
            "record": record,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return CollectedProduct(
        source=MarketSource.DEVICEMART,
        source_product_id=source_id,
        title=title,
        manufacturer=str(
            record.get("manufacture") or record.get("brand_title") or ""
        )
        or None,
        model_number=str(record.get("goods_code") or "") or None,
        product_url=f"{base_url}/goods/view?no={source_id}",
        image_url=urljoin(base_url, str(image)) if image else None,
        currency="KRW",
        unit_price=price,
        raw_payload=raw_record,
        raw_extension=".json",
        stock_quantity=stock,
        stock_text="재고 수량 미표시" if stock is None else None,
        moq=moq,
        vat_note=(
            "부가세 포함 표시가격"
            if str(record.get("tax") or "").lower() == "tax"
            else "상품 응답 표시가격"
        ),
        shipping_note=shipping_note,
        tiers=(CollectedTier(moq, price, "KRW"),),
    )


def parse_search_products(
    source: str,
    base_url: str,
) -> list[dict[str, str | None]]:
    products: list[dict[str, str | None]] = []
    seen: set[str] = set()
    patterns = (
        r'<a[^>]+href=["\'](?P<url>/goods/view\?no=(?P<id>\d+)[^"\']*)'
        r'["\'][^>]*>(?P<title>.*?)</a>',
        r'data-goods-seq=["\'](?P<id>\d+)["\'][^>]*'
        r'data-goods-name=["\'](?P<title>[^"\']+)',
    )
    for pattern in patterns:
        for match in re.finditer(pattern, source, re.I | re.S):
            source_id = match.group("id")
            if source_id in seen:
                continue
            title = _text(match.groupdict().get("title") or source_id)
            if not title or title.startswith("#"):
                continue
            url = match.groupdict().get("url") or f"/goods/view?no={source_id}"
            seen.add(source_id)
            products.append(
                {
                    "source_product_id": source_id,
                    "title": title,
                    "product_url": urljoin(base_url, html.unescape(url)),
                }
            )
    return products


def parse_product_page(
    raw: bytes,
    product_url: str,
    *,
    fallback: dict[str, str | None] | None = None,
) -> CollectedProduct | None:
    source = raw.decode("utf-8", errors="replace")
    structured = _product_json_ld(source)
    fallback = fallback or {}
    source_id_match = re.search(r"[?&]no=(\d+)", product_url)
    source_id = (
        str(structured.get("sku") or structured.get("mpn") or "")
        or (source_id_match.group(1) if source_id_match else "")
        or str(fallback.get("source_product_id") or product_url)
    )
    offer = structured.get("offers") or {}
    if isinstance(offer, list):
        offer = offer[0] if offer else {}
    if not isinstance(offer, dict):
        offer = {}
    price = _decimal(
        offer.get("price")
        or _meta(source, "product:price:amount")
        or _regex_value(source, r"gl_goods_price\s*=\s*([\d.]+)")
    )
    if price is None or price <= 0:
        return None
    title = str(
        structured.get("name")
        or _meta(source, "og:title")
        or fallback.get("title")
        or source_id
    )
    image = structured.get("image") or _meta(source, "og:image")
    if isinstance(image, list):
        image = image[0] if image else None
    manufacturer = structured.get("brand")
    if isinstance(manufacturer, dict):
        manufacturer = manufacturer.get("name")
    currency = str(
        offer.get("priceCurrency")
        or _meta(source, "product:price:currency")
        or "KRW"
    )
    stock_text = str(offer.get("availability") or "") or None
    moq = _integer(
        _regex_value(
            source,
            r"(?:최소\s*구매\s*수량|minimum\s*order)[^0-9]{0,30}(\d+)",
        )
    )
    return CollectedProduct(
        source=MarketSource.DEVICEMART,
        source_product_id=source_id,
        title=_text(title),
        manufacturer=str(manufacturer) if manufacturer else None,
        model_number=str(structured.get("mpn") or structured.get("sku") or "")
        or None,
        product_url=product_url,
        image_url=urljoin(product_url, str(image)) if image else None,
        currency=currency,
        unit_price=price,
        raw_payload=raw,
        raw_extension=".html",
        stock_text=stock_text,
        moq=moq,
        vat_note="DeviceMart 상품 페이지 표시 조건 기준",
        shipping_note="배송비는 주문 조건에 따라 별도",
        tiers=(CollectedTier(moq or 1, price, currency),),
    )


def _product_json_ld(source: str) -> dict[str, object]:
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>'
        r"(.*?)</script>",
        source,
        re.I | re.S,
    ):
        try:
            payload = json.loads(html.unescape(match.group(1)).strip())
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("@type") == "Product":
                return candidate
    return {}


def _meta(source: str, property_name: str) -> str | None:
    match = re.search(
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(property_name)}["\']'
        rf'[^>]+content=["\']([^"\']+)',
        source,
        re.I,
    )
    return html.unescape(match.group(1)) if match else None


def _regex_value(source: str, pattern: str) -> str | None:
    match = re.search(pattern, source, re.I | re.S)
    return match.group(1) if match else None


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    normalized = re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))
    try:
        return Decimal(normalized) if normalized else None
    except InvalidOperation:
        return None


def _integer(value: object) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d+", str(value).replace(",", ""))
    return int(match.group()) if match else None


def _text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()
