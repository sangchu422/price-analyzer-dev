from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from statistics import median

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.service import assess_variance
from app.cleansing.models import CleanDecision, CleanStatus
from app.core.config import Settings
from app.market.adapters.base import (
    CollectedProduct,
    MarketAdapter,
    market_meaningful_tokens,
    market_model_tokens,
)
from app.market.evidence import EvidenceStore
from app.market.screenshot import PageScreenshotter
from app.market.models import (
    MarketCollectionRun,
    MarketPriceObservation,
    MarketSource,
)
from app.market.repository import MarketRepository, normalize_query
from app.market.schemas import (
    MarketLookupResponse,
    MarketProductResponse,
    MarketSourceFailure,
    MarketTierResponse,
)
from app.quotes.models import RawQuoteItem


class MarketLookupError(RuntimeError):
    pass


class MarketLookupService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        adapters: list[MarketAdapter],
        screenshotter: PageScreenshotter | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.adapters = {adapter.source: adapter for adapter in adapters}
        self.repository = MarketRepository(
            session,
            EvidenceStore(
                settings.market_evidence_path,
                timeout=settings.market_request_timeout_seconds,
                screenshotter=screenshotter,
            ),
        )

    def lookup_raw_item(
        self,
        raw_item_id: int,
        *,
        force_refresh: bool = False,
        automatic: bool = False,
    ) -> MarketLookupResponse:
        raw_item = self.session.get(RawQuoteItem, raw_item_id)
        if raw_item is None:
            raise MarketLookupError("견적 항목을 찾을 수 없습니다.")
        decision = self.session.scalar(
            select(CleanDecision)
            .where(CleanDecision.raw_item_id == raw_item_id)
            .order_by(CleanDecision.id.desc())
        )
        if decision is None or decision.status is not CleanStatus.INCLUDED:
            raise MarketLookupError("정제가 완료된 포함 항목만 조회할 수 있습니다.")
        query = self._query(
            decision.item_name_norm or raw_item.item_name_raw,
            decision.spec_norm or raw_item.spec_raw,
            decision.maker_norm or raw_item.maker_raw,
        )
        if not query:
            raise MarketLookupError("시장가 검색에 사용할 품명 또는 사양이 없습니다.")
        return self.lookup(
            query,
            quote_unit_price=decision.unit_price,
            quantity=decision.quantity,
            force_refresh=force_refresh,
            raw_item_id=raw_item_id,
            automatic=automatic,
            required_manufacturer=decision.maker_norm,
        )

    def lookup(
        self,
        query: str,
        *,
        quote_unit_price: Decimal | None = None,
        quantity: Decimal | None = None,
        force_refresh: bool = False,
        raw_item_id: int = 0,
        automatic: bool = False,
        required_manufacturer: str | None = None,
    ) -> MarketLookupResponse:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        ttl = timedelta(hours=self.settings.market_price_ttl_hours)
        runs: list[MarketCollectionRun] = []
        failures: list[MarketSourceFailure] = []
        cache_count = live_count = 0
        for source in (MarketSource.DEVICEMART, MarketSource.MOUSER):
            cached = None if force_refresh else self.repository.fresh_run(
                source,
                query,
                now,
            )
            if cached is not None:
                runs.append(cached)
                cache_count += 1
                continue
            adapter = self.adapters.get(source)
            if adapter is None:
                failures.append(
                    MarketSourceFailure(
                        source=source,
                        detail="수집기가 비활성화되었거나 API 키가 없습니다.",
                    )
                )
                continue
            try:
                products = _relevant_products(query, adapter.search(query))
                expires_in = (
                    ttl
                    if products
                    else timedelta(
                        hours=self.settings.market_empty_result_ttl_hours
                    )
                )
                run = self.repository.save_success(
                    source=source,
                    query=query,
                    products=products,
                    collected_at=now,
                    expires_at=now + expires_in,
                )
                runs.append(run)
                live_count += 1
            except Exception as exc:
                detail = str(exc) or exc.__class__.__name__
                self.repository.save_failure(
                    source=source,
                    query=query,
                    detail=detail,
                    collected_at=now,
                )
                failures.append(
                    MarketSourceFailure(source=source, detail=detail)
                )
        self.session.commit()
        products = [
            self._product_response(
                observation,
                quantity,
                now,
                query=query,
                required_manufacturer=required_manufacturer,
            )
            for run in runs
            for observation in run.observations
            if observation.currency.upper() == "KRW"
        ]
        priced_products = (
            [product for product in products if product.automatic_price_eligible]
            if automatic
            else products
        )
        prices = sorted(
            product.applicable_unit_price for product in priced_products
        )
        minimum = prices[0] if prices else None
        maximum = prices[-1] if prices else None
        middle = Decimal(str(median(prices))) if prices else None
        variance = None
        assessment = "REVIEW_REQUIRED"
        review = self.settings.price_variance_review_percent
        high = self.settings.price_variance_high_percent
        if (
            market_model_tokens(query)
            and quote_unit_price is not None
            and middle
            and middle > 0
        ):
            variance = (
                (quote_unit_price - middle) / middle * Decimal("100")
            )
            assessment = assess_variance(
                variance,
                review_percent=review,
                high_percent=high,
            )
        if products and failures:
            state = "PARTIAL"
        elif live_count:
            state = "LIVE"
        elif cache_count:
            state = "CACHE"
        else:
            state = "UNAVAILABLE"
        if priced_products:
            outcome = "LIVE_HIT" if live_count else "CACHE_HIT"
        elif products:
            outcome = "REFERENCE_ONLY"
        elif runs:
            outcome = "NO_REFERENCE"
        else:
            outcome = "SOURCE_UNAVAILABLE"
        return MarketLookupResponse(
            raw_item_id=raw_item_id,
            query=query,
            quote_unit_price=quote_unit_price,
            quantity=quantity,
            cache_state=state,
            assessment=assessment,
            minimum_price=minimum,
            median_price=middle,
            maximum_price=maximum,
            variance_percent=variance,
            products=products,
            source_failures=failures,
            outcome=outcome,
            automatic_price_product_count=len(priced_products),
        )

    @staticmethod
    def _query(
        item_name: str | None,
        spec: str | None,
        maker: str | None,
    ) -> str:
        return normalize_query(
            " ".join(value for value in (maker, item_name, spec) if value)
        )

    @staticmethod
    def _applicable_price(
        observation: MarketPriceObservation,
        quantity: Decimal | None,
    ) -> Decimal:
        if not observation.tiers:
            return observation.unit_price
        requested = int(quantity or 1)
        eligible = [
            tier
            for tier in observation.tiers
            if tier.minimum_quantity <= requested
        ]
        selected = (
            max(eligible, key=lambda tier: tier.minimum_quantity)
            if eligible
            else min(observation.tiers, key=lambda tier: tier.minimum_quantity)
        )
        return selected.unit_price

    def _product_response(
        self,
        observation: MarketPriceObservation,
        quantity: Decimal | None,
        now: datetime,
        *,
        query: str,
        required_manufacturer: str | None,
    ) -> MarketProductResponse:
        product = observation.product
        run = observation.collection_run
        base = f"/api/market/evidence/{observation.id}"
        exclusion_reasons = _automatic_exclusion_reasons(
            query=query,
            product_title=product.title,
            product_manufacturer=product.manufacturer,
            product_model_number=product.model_number,
            required_manufacturer=required_manufacturer,
            quantity=quantity,
            moq=observation.moq,
            stock_quantity=observation.stock_quantity,
        )
        return MarketProductResponse(
            observation_id=observation.id,
            source=product.source,
            title=product.title,
            manufacturer=product.manufacturer,
            model_number=product.model_number,
            product_url=product.product_url,
            image_url=product.image_url,
            currency=observation.currency,
            applicable_unit_price=self._applicable_price(observation, quantity),
            stock_quantity=observation.stock_quantity,
            stock_text=observation.stock_text,
            moq=observation.moq,
            vat_note=observation.vat_note,
            shipping_note=observation.shipping_note,
            collected_at=run.collected_at,
            expires_at=run.expires_at,
            is_stale=run.expires_at <= now,
            tiers=[
                MarketTierResponse(
                    minimum_quantity=tier.minimum_quantity,
                    unit_price=tier.unit_price,
                    currency=tier.currency,
                )
                for tier in sorted(
                    observation.tiers,
                    key=lambda item: item.minimum_quantity,
                )
            ],
            image_evidence_url=(
                f"{base}/image" if observation.image_evidence_path else None
            ),
            raw_evidence_url=f"{base}/raw",
            screenshot_evidence_url=(
                f"{base}/screenshot"
                if observation.screenshot_evidence_path
                else None
            ),
            automatic_price_eligible=not exclusion_reasons,
            automatic_price_exclusion_reasons=exclusion_reasons,
        )


def _relevant_products(
    query: str,
    products: list[CollectedProduct],
) -> list[CollectedProduct]:
    normalized_query = normalize_query(query)
    model_tokens = market_model_tokens(normalized_query)
    meaningful = market_meaningful_tokens(normalized_query)
    accepted: list[CollectedProduct] = []
    for product in products:
        haystack = normalize_query(
            " ".join(
                value
                for value in (
                    product.title,
                    product.manufacturer,
                    product.model_number,
                )
                if value
            )
        )
        if model_tokens:
            product_tokens = re.findall(
                r"[0-9A-Z가-힣][0-9A-Z가-힣._/-]+",
                haystack,
            )
            if any(
                token in haystack
                or any(fuzz.ratio(token, candidate) >= 85 for candidate in product_tokens)
                for token in model_tokens
            ):
                accepted.append(product)
        elif meaningful and sum(token in haystack for token in meaningful) >= min(
            2, len(meaningful)
        ):
            accepted.append(product)
    return accepted


def _automatic_exclusion_reasons(
    *,
    query: str,
    product_title: str,
    product_manufacturer: str | None,
    product_model_number: str | None,
    required_manufacturer: str | None,
    quantity: Decimal | None,
    moq: int | None,
    stock_quantity: int | None,
) -> list[str]:
    """Return conservative reasons a market result cannot drive a verdict."""

    reasons: list[str] = []
    model_tokens = market_model_tokens(normalize_query(query))
    product_tokens = re.findall(
        r"[0-9A-Z][0-9A-Z_./-]+",
        normalize_query(
            " ".join(
                value
                for value in (product_model_number, product_title)
                if value
            )
        ),
    )
    canonical_product_tokens = {
        re.sub(r"[^0-9A-Z]", "", token) for token in product_tokens
    }
    if not model_tokens:
        reasons.append("MODEL_NUMBER_REQUIRED")
    elif not any(
        re.sub(r"[^0-9A-Z]", "", token) in canonical_product_tokens
        for token in model_tokens
    ):
        reasons.append("MODEL_NUMBER_NOT_EXACT")

    maker = normalize_query(required_manufacturer or "")
    maker_haystack = normalize_query(
        " ".join(
            value
            for value in (product_manufacturer, product_title)
            if value
        )
    )
    if not maker:
        reasons.append("MANUFACTURER_REQUIRED")
    elif maker not in maker_haystack:
        reasons.append("MANUFACTURER_MISMATCH")

    requested = int(quantity or 0)
    if requested <= 0:
        reasons.append("QUANTITY_REQUIRED")
    if moq is not None and requested < moq:
        reasons.append("MOQ_NOT_MET")
    if stock_quantity is None:
        reasons.append("STOCK_UNCONFIRMED")
    elif stock_quantity < max(requested, 1):
        reasons.append("INSUFFICIENT_STOCK")
    return reasons
