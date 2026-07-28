from __future__ import annotations

from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.cleansing.models import CleanDecision, CleanStatus
from app.core.config import Settings
from app.db.base import Base
from app.db import models as _models
from app.documents.models import SourceDocument, SourceVariant
from app.market.adapters.base import CollectedProduct, CollectedTier
from app.market.models import MarketCollectionRun, MarketSource
from app.market.service import MarketLookupService
from app.quotes.models import RawQuoteItem


class FakeAdapter:
    def __init__(self, source: MarketSource, price: str) -> None:
        self.source = source
        self.price = Decimal(price)
        self.calls = 0

    def search(self, query: str) -> list[CollectedProduct]:
        self.calls += 1
        return [
            CollectedProduct(
                source=self.source,
                source_product_id=f"{self.source.value}-1",
                title=f"{query} product",
                product_url="https://example.test/product",
                currency="KRW",
                unit_price=self.price,
                raw_payload=b'{"source":"fixture"}',
                raw_extension=".json",
                tiers=(
                    CollectedTier(1, self.price, "KRW"),
                    CollectedTier(10, self.price - Decimal("10"), "KRW"),
                ),
            )
        ]


def _raw_item(session: Session) -> RawQuoteItem:
    document = SourceDocument(logical_name="incoming.xlsx")
    variant = SourceVariant(
        document=document,
        path="incoming.xlsx",
        sha256="a" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    raw = RawQuoteItem(
        source_variant=variant,
        source_sheet="Sheet1",
        source_row=1,
        item_name_raw="STM32",
        spec_raw="F407",
        unit_raw="EA",
        quantity_raw="10",
        unit_price_raw="130",
        parser_name="xlsx",
        parser_version="v1",
    )
    session.add(
        CleanDecision(
            raw_item=raw,
            status=CleanStatus.INCLUDED,
            reason_code="VALID",
            item_name_norm="STM32",
            spec_norm="F407",
            unit_norm="EA",
            quantity=Decimal("10"),
            unit_price=Decimal("130"),
            rule_version="clean-v1",
        )
    )
    session.commit()
    return raw


def test_market_lookup_collects_then_reuses_fresh_cache(tmp_path) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(
        project_root=tmp_path,
        market_evidence_folder="evidence",
        market_price_ttl_hours=168,
    )
    device = FakeAdapter(MarketSource.DEVICEMART, "100")
    mouser = FakeAdapter(MarketSource.MOUSER, "120")

    with Session(engine, expire_on_commit=False) as session:
        raw = _raw_item(session)
        first = MarketLookupService(
            session,
            settings,
            [device, mouser],
        ).lookup_raw_item(raw.id)
        second = MarketLookupService(
            session,
            settings,
            [device, mouser],
        ).lookup_raw_item(raw.id)

        assert first.cache_state == "LIVE"
        assert second.cache_state == "CACHE"
        assert first.minimum_price == Decimal("90")
        assert first.maximum_price == Decimal("110")
        assert first.assessment == "HIGH"
        assert device.calls == mouser.calls == 1
        assert session.scalar(
            select(func.count(MarketCollectionRun.id))
        ) == 2
        assert all(
            (tmp_path / "evidence" / product.raw_evidence_url.split("/")[-2])
            is not None
            for product in first.products
        )


def test_market_service_calls_screenshotter_for_each_collected_product(
    tmp_path,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(
        project_root=tmp_path,
        market_evidence_folder="evidence",
    )

    class _Screenshotter:
        captured: list[str] = []

        def capture(self, url: str) -> bytes | None:
            self.captured.append(url)
            return b"\x89PNG\r\nfake"

    screenshotter = _Screenshotter()
    device = FakeAdapter(MarketSource.DEVICEMART, "100")

    with Session(engine, expire_on_commit=False) as session:
        raw = _raw_item(session)
        MarketLookupService(
            session,
            settings,
            [device],
            screenshotter=screenshotter,
        ).lookup_raw_item(raw.id)

    assert len(screenshotter.captured) == 1
    assert screenshotter.captured[0] == "https://example.test/product"
    evidence_dir = tmp_path / "evidence"
    screenshots = list(evidence_dir.rglob("page.png"))
    assert len(screenshots) == 1
    assert screenshots[0].read_bytes() == b"\x89PNG\r\nfake"
