from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.market.adapters.base import CollectedProduct
from app.market.evidence import EvidenceStore
from app.market.models import MarketSource


def _product(*, screenshot_bytes: bytes | None = None) -> CollectedProduct:
    return CollectedProduct(
        source=MarketSource.DEVICEMART,
        source_product_id="12345",
        title="SERVO MOTOR",
        product_url="https://www.devicemart.co.kr/goods/view?no=12345",
        currency="KRW",
        unit_price=Decimal("25000"),
        raw_payload=b'{"source":"test"}',
        raw_extension=".json",
        screenshot_bytes=screenshot_bytes,
    )


class _FakeScreenshotter:
    def __init__(self, returns: bytes | None) -> None:
        self._returns = returns
        self.captured_urls: list[str] = []

    def capture(self, url: str) -> bytes | None:
        self.captured_urls.append(url)
        return self._returns


def test_evidence_store_saves_screenshot_when_screenshotter_provided(
    tmp_path: Path,
) -> None:
    screenshot_bytes = b"\x89PNG\r\nfakeimage"
    screenshotter = _FakeScreenshotter(returns=screenshot_bytes)
    store = EvidenceStore(tmp_path, screenshotter=screenshotter)

    result = store.save("run-1", _product())

    assert result.screenshot_path is not None
    assert result.screenshot_sha256 is not None
    saved = (tmp_path / result.screenshot_path).read_bytes()
    assert saved == screenshot_bytes
    assert screenshotter.captured_urls == [
        "https://www.devicemart.co.kr/goods/view?no=12345"
    ]


def test_evidence_store_skips_screenshot_when_no_screenshotter(
    tmp_path: Path,
) -> None:
    store = EvidenceStore(tmp_path)

    result = store.save("run-1", _product())

    assert result.screenshot_path is None
    assert result.screenshot_sha256 is None
    assert not any(tmp_path.rglob("page.png"))


def test_evidence_store_skips_screenshot_when_screenshotter_returns_none(
    tmp_path: Path,
) -> None:
    screenshotter = _FakeScreenshotter(returns=None)
    store = EvidenceStore(tmp_path, screenshotter=screenshotter)

    result = store.save("run-1", _product())

    assert result.screenshot_path is None
    assert not any(tmp_path.rglob("page.png"))
    assert screenshotter.captured_urls == [
        "https://www.devicemart.co.kr/goods/view?no=12345"
    ]


def test_evidence_store_prefers_product_screenshot_bytes_over_screenshotter(
    tmp_path: Path,
) -> None:
    product_screenshot = b"product-level-screenshot"
    screenshotter = _FakeScreenshotter(returns=b"should-not-be-used")
    store = EvidenceStore(tmp_path, screenshotter=screenshotter)

    result = store.save("run-1", _product(screenshot_bytes=product_screenshot))

    saved = (tmp_path / result.screenshot_path).read_bytes()
    assert saved == product_screenshot
    assert screenshotter.captured_urls == []


def test_playwright_screenshotter_is_unavailable_when_playwright_not_installed() -> None:
    from app.market.screenshot import PlaywrightScreenshotter

    screenshotter = PlaywrightScreenshotter()
    result = screenshotter.capture("https://www.devicemart.co.kr/goods/view?no=1")

    assert result is None
