from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.time import utc_now
from app.db.types import ExactDecimal, NaiveUTCDateTime


class MarketSource(StrEnum):
    DEVICEMART = "DEVICEMART"
    MOUSER = "MOUSER"


class CollectionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class _ImmutableMarketRow:
    __evidence_immutable__: ClassVar[bool] = True


class MarketCollectionRun(_ImmutableMarketRow, Base):
    __tablename__ = "market_collection_run"
    __table_args__ = (
        CheckConstraint(
            "length(query_fingerprint) = 64",
            name="ck_market_collection_query_fingerprint",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[MarketSource] = mapped_column(
        Enum(
            MarketSource,
            name="market_source",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        index=True,
    )
    query_text: Mapped[str] = mapped_column(Text)
    query_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[CollectionStatus] = mapped_column(
        Enum(
            CollectionStatus,
            name="market_collection_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        )
    )
    error_detail: Mapped[str | None] = mapped_column(Text)
    collected_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(NaiveUTCDateTime(), index=True)

    observations: Mapped[list[MarketPriceObservation]] = relationship(
        "MarketPriceObservation",
        back_populates="collection_run",
    )


class MarketProduct(_ImmutableMarketRow, Base):
    __tablename__ = "market_product"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "source_product_id",
            name="uq_market_product_source_id",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[MarketSource] = mapped_column(
        Enum(
            MarketSource,
            name="market_product_source",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        index=True,
    )
    source_product_id: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(Text)
    manufacturer: Mapped[str | None] = mapped_column(Text)
    model_number: Mapped[str | None] = mapped_column(Text)
    product_url: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime(),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    observations: Mapped[list[MarketPriceObservation]] = relationship(
        "MarketPriceObservation",
        back_populates="product",
    )


class MarketPriceObservation(_ImmutableMarketRow, Base):
    __tablename__ = "market_price_observation"
    __table_args__ = (
        CheckConstraint(
            "unit_price > 0",
            name="ck_market_observation_positive_price",
        ),
        CheckConstraint(
            "moq IS NULL OR moq > 0",
            name="ck_market_observation_positive_moq",
        ),
        CheckConstraint(
            "length(raw_sha256) = 64",
            name="ck_market_observation_raw_sha256",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    collection_run_id: Mapped[int] = mapped_column(
        ForeignKey("market_collection_run.id", ondelete="RESTRICT"),
        index=True,
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("market_product.id", ondelete="RESTRICT"),
        index=True,
    )
    currency: Mapped[str] = mapped_column(String(10))
    unit_price: Mapped[Decimal] = mapped_column(ExactDecimal())
    stock_quantity: Mapped[int | None] = mapped_column(Integer)
    stock_text: Mapped[str | None] = mapped_column(Text)
    moq: Mapped[int | None] = mapped_column(Integer)
    vat_note: Mapped[str | None] = mapped_column(Text)
    shipping_note: Mapped[str | None] = mapped_column(Text)
    raw_evidence_path: Mapped[str] = mapped_column(Text)
    raw_sha256: Mapped[str] = mapped_column(String(64))
    image_evidence_path: Mapped[str | None] = mapped_column(Text)
    image_sha256: Mapped[str | None] = mapped_column(String(64))
    screenshot_evidence_path: Mapped[str | None] = mapped_column(Text)
    screenshot_sha256: Mapped[str | None] = mapped_column(String(64))

    collection_run: Mapped[MarketCollectionRun] = relationship(
        "MarketCollectionRun",
        back_populates="observations",
    )
    product: Mapped[MarketProduct] = relationship(
        "MarketProduct",
        back_populates="observations",
    )
    tiers: Mapped[list[MarketPriceTier]] = relationship(
        "MarketPriceTier",
        back_populates="observation",
    )


class MarketPriceTier(_ImmutableMarketRow, Base):
    __tablename__ = "market_price_tier"
    __table_args__ = (
        UniqueConstraint(
            "observation_id",
            "minimum_quantity",
            name="uq_market_tier_observation_quantity",
        ),
        CheckConstraint(
            "minimum_quantity > 0 AND unit_price > 0",
            name="ck_market_tier_positive_values",
        ),
        {"info": {"evidence_immutable": True}},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    observation_id: Mapped[int] = mapped_column(
        ForeignKey("market_price_observation.id", ondelete="RESTRICT"),
        index=True,
    )
    minimum_quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(ExactDecimal())
    currency: Mapped[str] = mapped_column(String(10))

    observation: Mapped[MarketPriceObservation] = relationship(
        "MarketPriceObservation",
        back_populates="tiers",
    )
