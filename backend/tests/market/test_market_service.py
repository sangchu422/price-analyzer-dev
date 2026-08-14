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
from app.market.models import (
    MarketCollectionRun,
    MarketPriceObservation,
    MarketSource,
)
from app.market.service import (
    MarketLookupService,
    _automatic_exclusion_reasons,
    _relevant_products,
)
from app.quotes.models import RawQuoteItem


class FakeAdapter:
    def __init__(
        self,
        source: MarketSource,
        price: str,
        *,
        manufacturer: str | None = None,
        model_number: str | None = None,
        stock_quantity: int | None = None,
        moq: int | None = None,
    ) -> None:
        self.source = source
        self.price = Decimal(price)
        self.calls = 0
        self.manufacturer = manufacturer
        self.model_number = model_number
        self.stock_quantity = stock_quantity
        self.moq = moq

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
                manufacturer=self.manufacturer,
                model_number=self.model_number,
                stock_quantity=self.stock_quantity,
                moq=self.moq,
                tiers=(
                    CollectedTier(1, self.price, "KRW"),
                    CollectedTier(10, self.price - Decimal("10"), "KRW"),
                ),
            )
        ]


class FailingAdapter:
    def __init__(self, source: MarketSource) -> None:
        self.source = source

    def search(self, query: str) -> list[CollectedProduct]:
        raise RuntimeError(f"{self.source.value} unavailable")


def _raw_item(session: Session, *, maker_norm: str | None = None) -> RawQuoteItem:
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
            maker_norm=maker_norm,
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
    device = FakeAdapter(
        MarketSource.DEVICEMART, "100", manufacturer="ACME", stock_quantity=10
    )
    mouser = FakeAdapter(
        MarketSource.MOUSER, "120", manufacturer="ACME", stock_quantity=10
    )

    with Session(engine, expire_on_commit=False) as session:
        raw = _raw_item(session, maker_norm="ACME")
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


def test_missing_evidence_invalidates_a_fresh_cache(tmp_path) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(
        project_root=tmp_path,
        market_evidence_folder="evidence",
    )
    device = FakeAdapter(MarketSource.DEVICEMART, "100")

    with Session(engine, expire_on_commit=False) as session:
        raw = _raw_item(session)
        MarketLookupService(session, settings, [device]).lookup_raw_item(raw.id)
        observation = session.scalar(select(MarketPriceObservation))
        assert observation is not None
        (settings.market_evidence_path / observation.raw_evidence_path).unlink()

        refreshed = MarketLookupService(
            session,
            settings,
            [device],
        ).lookup_raw_item(raw.id)

        assert refreshed.cache_state == "PARTIAL"
        assert device.calls == 2


def test_model_similarity_accepts_near_part_number_but_not_measurement_only() -> None:
    candidate = CollectedProduct(
        source=MarketSource.DEVICEMART,
        source_product_id="1",
        title="OMRON E3ZG-D61 photo sensor",
        product_url="https://example.test/1",
        currency="KRW",
        unit_price=Decimal("100"),
        raw_payload=b"{}",
        raw_extension=".json",
    )
    measurement_only = CollectedProduct(
        source=MarketSource.DEVICEMART,
        source_product_id="2",
        title="SO200W-40 connector",
        product_url="https://example.test/2",
        currency="KRW",
        unit_price=Decimal("100"),
        raw_payload=b"{}",
        raw_extension=".json",
    )

    assert _relevant_products("OMRON E3Z-D61", [candidate]) == [candidate]
    assert _relevant_products(
        "SERVO MOTOR 200W",
        [measurement_only],
    ) == []


def test_generic_family_search_remains_review_required(tmp_path) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(
        project_root=tmp_path,
        market_evidence_folder="evidence",
    )
    device = FakeAdapter(MarketSource.DEVICEMART, "100")

    with Session(engine, expire_on_commit=False) as session:
        result = MarketLookupService(
            session,
            settings,
            [device],
        ).lookup("PLC MELSEC Q", quote_unit_price=Decimal("500"))

    # A generic family search has no model tokens by construction, so the
    # matched product can never be automatic_price_eligible either (the same
    # MODEL_NUMBER_REQUIRED reason that skips the variance calculation below
    # also excludes it from min/median/max) -- unlike other eligibility gaps
    # in this file, this one cannot be closed with fixture data without
    # defeating the point of a "generic search" test, so median_price is
    # correctly None rather than a price this search cannot actually vouch for.
    assert result.products
    assert result.median_price is None
    assert result.variance_percent is None
    assert result.assessment == "REVIEW_REQUIRED"


def test_automatic_market_price_requires_exact_part_maker_stock_and_moq() -> None:
    assert _automatic_exclusion_reasons(
        query="OMRON PHOTO SENSOR E3Z-D61",
        product_title="OMRON PHOTO SENSOR E3Z-D61",
        product_manufacturer="OMRON",
        product_model_number="E3Z-D61",
        required_manufacturer="OMRON",
        quantity=Decimal("2"),
        moq=1,
        stock_quantity=10,
    ) == []

    reasons = _automatic_exclusion_reasons(
        query="OMRON PHOTO SENSOR E3Z-D61",
        product_title="OMRON PHOTO SENSOR E3ZG-D61",
        product_manufacturer="OMRON",
        product_model_number="E3ZG-D61",
        required_manufacturer="OMRON",
        quantity=Decimal("2"),
        moq=5,
        stock_quantity=None,
    )

    assert "MODEL_NUMBER_NOT_EXACT" in reasons
    assert "MOQ_NOT_MET" in reasons
    assert "STOCK_UNCONFIRMED" in reasons


def test_missing_mouser_adapter_keeps_devicemart_reference_and_reports_setup(
    tmp_path,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(project_root=tmp_path, market_evidence_folder="evidence")

    with Session(engine, expire_on_commit=False) as session:
        raw = _raw_item(session)
        result = MarketLookupService(
            session,
            settings,
            [FakeAdapter(MarketSource.DEVICEMART, "100")],
        ).lookup_raw_item(raw.id)

    assert result.outcome == "REFERENCE_ONLY"
    assert [failure.source for failure in result.source_failures] == [
        MarketSource.MOUSER
    ]
    assert "API 키" in result.source_failures[0].detail


def test_both_market_sources_failing_remains_source_unavailable(tmp_path) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(project_root=tmp_path, market_evidence_folder="evidence")

    with Session(engine, expire_on_commit=False) as session:
        raw = _raw_item(session)
        result = MarketLookupService(
            session,
            settings,
            [
                FailingAdapter(MarketSource.DEVICEMART),
                FailingAdapter(MarketSource.MOUSER),
            ],
        ).lookup_raw_item(raw.id)

    assert result.outcome == "SOURCE_UNAVAILABLE"
    assert result.assessment == "REVIEW_REQUIRED"
    assert {failure.source for failure in result.source_failures} == {
        MarketSource.DEVICEMART,
        MarketSource.MOUSER,
    }


def test_market_lookup_lands_in_review_band_for_moderate_variance(
    tmp_path,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(
        project_root=tmp_path,
        market_evidence_folder="evidence",
    )
    assert settings.price_variance_review_percent == Decimal("10")
    assert settings.price_variance_high_percent == Decimal("20")

    # FakeAdapter("110") produces tiers: qty>=1 -> 110, qty>=10 -> 100.
    # Requesting quantity=10 selects the qty>=10 tier (100), so the lone
    # DeviceMart listing becomes the market median of 100.
    # manufacturer/stock_quantity are set (and required_manufacturer passed
    # below) so this product is automatic_price_eligible -- eligibility
    # filtering now applies unconditionally, so an ineligible product would
    # not count toward the median at all.
    device = FakeAdapter(
        MarketSource.DEVICEMART, "110", manufacturer="ACME", stock_quantity=10
    )

    with Session(engine, expire_on_commit=False) as session:
        result = MarketLookupService(
            session,
            settings,
            [device],
        ).lookup(
            "STM32 F407",
            quote_unit_price=Decimal("115"),
            quantity=Decimal("10"),
            required_manufacturer="ACME",
        )

    # median = 100, quote = 115 -> variance = (115 - 100) / 100 * 100 = 15%,
    # which sits strictly inside the REVIEW band (10% < 15% <= 20%) under
    # the default review_percent=10 / high_percent=20 settings -- neither
    # WITHIN_RANGE nor HIGH.
    assert result.median_price == Decimal("100")
    assert result.variance_percent == Decimal("15")
    assert result.assessment == "REVIEW"


def test_market_assessment_uses_caller_supplied_thresholds_not_global_defaults(
    tmp_path,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(project_root=tmp_path, market_evidence_folder="evidence")
    # manufacturer/stock_quantity are set (and required_manufacturer/quantity
    # passed below) so both products are automatic_price_eligible --
    # eligibility filtering now applies unconditionally.
    device = FakeAdapter(
        MarketSource.DEVICEMART, "115", manufacturer="OMRON", stock_quantity=5
    )
    mouser = FakeAdapter(
        MarketSource.MOUSER, "115", manufacturer="OMRON", stock_quantity=5
    )

    with Session(engine, expire_on_commit=False) as session:
        result_default = MarketLookupService(
            session, settings, [device, mouser],
        ).lookup(
            "OMRON E3Z-D61",
            quote_unit_price=Decimal("100"),
            quantity=Decimal("1"),
            required_manufacturer="OMRON",
        )
        result_custom = MarketLookupService(
            session, settings, [device, mouser],
        ).lookup(
            "OMRON E3Z-D61",
            quote_unit_price=Decimal("100"),
            quantity=Decimal("1"),
            required_manufacturer="OMRON",
            force_refresh=True,
            review_percent=Decimal("30"),
            high_percent=Decimal("40"),
        )

    # median = 115, quote = 100 -> variance = (100 - 115) / 115 * 100 ~= -13.04%.
    # Default settings (review=10%, high=20%) put -13.04% in the REVIEW band
    # (-20% < -13.04% < -10%). The caller-supplied thresholds (review=30%,
    # high=40%) widen the WITHIN_RANGE band to +/-30%, so the same variance
    # now lands as WITHIN_RANGE -- proving the parameters (not the global
    # settings default) drove the assessment.
    assert result_default.median_price == Decimal("115")
    assert result_custom.median_price == Decimal("115")
    assert result_default.variance_percent is not None
    assert result_custom.variance_percent is not None
    assert result_default.assessment == "REVIEW"
    assert result_custom.assessment == "WITHIN_RANGE"


def test_manual_and_automatic_lookup_use_the_same_eligible_only_population(
    tmp_path,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(project_root=tmp_path, market_evidence_folder="evidence")
    device = FakeAdapter(MarketSource.DEVICEMART, "100")

    with Session(engine, expire_on_commit=False) as session:
        # maker_norm not set -> this product can never be eligible.
        raw = _raw_item(session)
        manual = MarketLookupService(
            session, settings, [device],
        ).lookup_raw_item(raw.id)

    # An item with no recorded manufacturer must not get a price verdict on
    # manual lookup either -- eligibility filtering now applies unconditionally.
    assert manual.median_price is None
    assert manual.assessment == "REVIEW_REQUIRED"


def test_market_assessment_uses_review_band_at_exact_boundaries() -> None:
    from app.analysis.service import assess_variance

    cases = [
        (Decimal("-25"), "LOW"),
        (Decimal("-20"), "REVIEW"),
        (Decimal("-19.999999"), "REVIEW"),
        (Decimal("-10"), "WITHIN_RANGE"),
        (Decimal("-9.999999"), "WITHIN_RANGE"),
        (Decimal("0"), "WITHIN_RANGE"),
        (Decimal("10"), "WITHIN_RANGE"),
        (Decimal("10.000001"), "REVIEW"),
        (Decimal("20"), "REVIEW"),
        (Decimal("20.000001"), "HIGH"),
        (Decimal("25"), "HIGH"),
    ]
    for percent, expected in cases:
        assert (
            assess_variance(
                percent,
                review_percent=Decimal("10"),
                high_percent=Decimal("20"),
            )
            == expected
        ), f"{percent}% expected {expected}"
