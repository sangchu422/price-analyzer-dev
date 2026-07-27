from __future__ import annotations

import json
from decimal import Decimal

import httpx

from app.market.adapters.devicemart import parse_product_page, parse_sse_products
from app.market.adapters.mouser import MouserAdapter


def test_mouser_parses_quantity_prices_from_official_response() -> None:
    payload = {
        "Errors": [],
        "SearchResults": {
            "Parts": [
                {
                    "MouserPartNumber": "511-STM32F407",
                    "ManufacturerPartNumber": "STM32F407VGT6",
                    "Manufacturer": "STMicroelectronics",
                    "Description": "ARM Microcontroller",
                    "ProductDetailUrl": "https://www.mouser.kr/ProductDetail/1",
                    "Currency": "KRW",
                    "AvailabilityInStock": "120",
                    "Min": "1",
                    "PriceBreaks": [
                        {"Quantity": 1, "Price": "₩12,000", "Currency": "KRW"},
                        {"Quantity": 10, "Price": "₩10,500", "Currency": "KRW"},
                    ],
                }
            ]
        },
    }
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload, request=request)
    )
    with httpx.Client(transport=transport) as client:
        products = MouserAdapter(
            api_key="test",
            base_url="https://api.mouser.test/api/v1",
            client=client,
        ).search("STM32F407")

    assert products[0].model_number == "STM32F407VGT6"
    assert products[0].stock_quantity == 120
    assert products[0].tiers[1].unit_price == Decimal("10500")


def test_devicemart_parses_product_json_ld_evidence() -> None:
    payload = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "STM32F407 개발보드",
        "sku": "12345",
        "mpn": "STM32F407",
        "brand": {"@type": "Brand", "name": "ST"},
        "image": "https://img.example.test/12345.jpg",
        "offers": {
            "@type": "Offer",
            "price": "25000",
            "priceCurrency": "KRW",
            "availability": "https://schema.org/InStock",
        },
    }
    source = (
        '<html><script type="application/ld+json">'
        + json.dumps(payload, ensure_ascii=False)
        + "</script><div>최소 구매 수량 2개</div></html>"
    ).encode()

    product = parse_product_page(
        source,
        "https://www.devicemart.co.kr/goods/view?no=12345",
    )

    assert product is not None
    assert product.source_product_id == "12345"
    assert product.unit_price == Decimal("25000")
    assert product.moq == 2
    assert product.raw_payload == source


def test_devicemart_parses_public_sse_search_result() -> None:
    record = {
        "goods_seq": "12345",
        "goods_name": "STM32F407 개발보드",
        "manufacture": "ST",
        "sale_price": 25000,
        "stock": "12",
        "min_purchase_ea": "2",
        "tax": "tax",
        "image": "/data/goods/12345.jpg",
        "shipping_group": {"first_cost": "3000"},
    }
    raw = (
        "event: sphinx_result\n"
        f"data: {json.dumps({'list': {'record': [record]}}, ensure_ascii=False)}\n\n"
        "event: end\ndata: {}\n"
    ).encode()

    products = parse_sse_products(raw, "https://www.devicemart.co.kr")

    assert products[0].source_product_id == "12345"
    assert products[0].unit_price == Decimal("25000")
    assert products[0].stock_quantity == 12
    assert products[0].image_url == (
        "https://www.devicemart.co.kr/data/goods/12345.jpg"
    )
