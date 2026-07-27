from __future__ import annotations

from datetime import datetime
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.market.adapters.base import CollectedProduct
from app.market.evidence import EvidenceStore
from app.market.models import (
    CollectionStatus,
    MarketCollectionRun,
    MarketPriceObservation,
    MarketPriceTier,
    MarketProduct,
    MarketSource,
)


def normalize_query(query: str) -> str:
    return " ".join(query.upper().split())


def query_fingerprint(query: str) -> str:
    return sha256(normalize_query(query).encode("utf-8")).hexdigest()


class MarketRepository:
    def __init__(self, session: Session, evidence_store: EvidenceStore) -> None:
        self.session = session
        self.evidence_store = evidence_store

    def fresh_run(
        self,
        source: MarketSource,
        query: str,
        now: datetime,
    ) -> MarketCollectionRun | None:
        return self.session.scalar(
            select(MarketCollectionRun)
            .where(
                MarketCollectionRun.source == source,
                MarketCollectionRun.query_fingerprint
                == query_fingerprint(query),
                MarketCollectionRun.status == CollectionStatus.SUCCEEDED,
                MarketCollectionRun.expires_at > now,
            )
            .options(
                selectinload(MarketCollectionRun.observations)
                .selectinload(MarketPriceObservation.product),
                selectinload(MarketCollectionRun.observations)
                .selectinload(MarketPriceObservation.tiers),
            )
            .order_by(MarketCollectionRun.collected_at.desc())
        )

    def save_success(
        self,
        *,
        source: MarketSource,
        query: str,
        products: list[CollectedProduct],
        collected_at: datetime,
        expires_at: datetime,
    ) -> MarketCollectionRun:
        run = MarketCollectionRun(
            source=source,
            query_text=query,
            query_fingerprint=query_fingerprint(query),
            status=CollectionStatus.SUCCEEDED,
            collected_at=collected_at,
            expires_at=expires_at,
        )
        self.session.add(run)
        self.session.flush()
        run_key = f"{collected_at:%Y%m%dT%H%M%S}_{run.id}_{source.value.lower()}"
        for collected in products:
            product = self.session.scalar(
                select(MarketProduct).where(
                    MarketProduct.source == source,
                    MarketProduct.source_product_id
                    == collected.source_product_id,
                )
            )
            if product is None:
                product = MarketProduct(
                    source=source,
                    source_product_id=collected.source_product_id,
                    title=collected.title,
                    manufacturer=collected.manufacturer,
                    model_number=collected.model_number,
                    product_url=collected.product_url,
                    image_url=collected.image_url,
                )
                self.session.add(product)
                self.session.flush()
            evidence = self.evidence_store.save(run_key, collected)
            observation = MarketPriceObservation(
                collection_run=run,
                product=product,
                currency=collected.currency,
                unit_price=collected.unit_price,
                stock_quantity=collected.stock_quantity,
                stock_text=collected.stock_text,
                moq=collected.moq,
                vat_note=collected.vat_note,
                shipping_note=collected.shipping_note,
                raw_evidence_path=evidence.raw_path,
                raw_sha256=evidence.raw_sha256,
                image_evidence_path=evidence.image_path,
                image_sha256=evidence.image_sha256,
                screenshot_evidence_path=evidence.screenshot_path,
                screenshot_sha256=evidence.screenshot_sha256,
            )
            observation.tiers = [
                MarketPriceTier(
                    minimum_quantity=tier.minimum_quantity,
                    unit_price=tier.unit_price,
                    currency=tier.currency,
                )
                for tier in collected.tiers
            ]
            self.session.add(observation)
        self.session.flush()
        return run

    def save_failure(
        self,
        *,
        source: MarketSource,
        query: str,
        detail: str,
        collected_at: datetime,
    ) -> MarketCollectionRun:
        run = MarketCollectionRun(
            source=source,
            query_text=query,
            query_fingerprint=query_fingerprint(query),
            status=CollectionStatus.FAILED,
            error_detail=detail[:2000],
            collected_at=collected_at,
            expires_at=collected_at,
        )
        self.session.add(run)
        self.session.flush()
        return run
