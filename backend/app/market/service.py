from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cleansing.models import CleanDecision, CleanStatus
from app.core.config import Settings
from app.market.adapters.base import CollectedProduct, MarketAdapter
from app.market.evidence import EvidenceStore
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
    ) -> None:
        self.session = session
        self.settings = settings
        self.adapters = {adapter.source: adapter for adapter in adapters}
        self.repository = MarketRepository(
            session,
            EvidenceStore(
                settings.market_evidence_path,
                timeout=settings.market_request_timeout_seconds,
            ),
        )

    def lookup_raw_item(
        self,
        raw_item_id: int,
        *,
        force_refresh: bool = False,
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
        )

    def lookup(
        self,
        query: str,
        *,
        quote_unit_price: Decimal | None = None,
        quantity: Decimal | None = None,
        force_refresh: bool = False,
        raw_item_id: int = 0,
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
                run = self.repository.save_success(
                    source=source,
                    query=query,
                    products=products,
                    collected_at=now,
                    expires_at=now + ttl,
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
            self._product_response(observation, quantity, now)
            for run in runs
            for observation in run.observations
            if observation.currency.upper() == "KRW"
        ]
        prices = sorted(product.applicable_unit_price for product in products)
        minimum = prices[0] if prices else None
        maximum = prices[-1] if prices else None
        middle = Decimal(str(median(prices))) if prices else None
        variance = None
        assessment = "REVIEW_REQUIRED"
        if quote_unit_price is not None and middle and middle > 0:
            variance = (
                (quote_unit_price - middle) / middle * Decimal("100")
            )
            high = self.settings.price_variance_high_percent
            review = self.settings.price_variance_review_percent
            if variance > high:
                assessment = "HIGH"
            elif variance < -high:
                assessment = "LOW"
            elif abs(variance) <= review:
                assessment = "WITHIN_RANGE"
        if products and failures:
            state = "PARTIAL"
        elif live_count:
            state = "LIVE"
        elif cache_count:
            state = "CACHE"
        else:
            state = "UNAVAILABLE"
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
    ) -> MarketProductResponse:
        product = observation.product
        run = observation.collection_run
        base = f"/api/market/evidence/{observation.id}"
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
        )


def _relevant_products(
    query: str,
    products: list[CollectedProduct],
) -> list[CollectedProduct]:
    normalized_query = normalize_query(query)
    tokens = re.findall(r"[0-9A-Z가-힣][0-9A-Z가-힣._/-]+", normalized_query)
    model_tokens = [
        token
        for token in tokens
        if len(token) >= 4
        and any(character.isalpha() for character in token)
        and any(character.isdigit() for character in token)
    ]
    meaningful = [token for token in tokens if len(token) >= 2]
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
            if any(token in haystack for token in model_tokens):
                accepted.append(product)
        elif any(token in haystack for token in meaningful):
            accepted.append(product)
    return accepted
