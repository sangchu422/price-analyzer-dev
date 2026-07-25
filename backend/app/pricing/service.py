"""Exact, append-only standard-price drafts and approvals."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import (
    DocumentMetadataVersion,
    ItemMembershipDecision,
    MembershipStatus,
    StandardItem,
    StandardItemVersion,
    StandardPriceObservation,
    StandardPriceVersion,
)
from app.catalog.service import (
    current_document_metadata,
    current_membership,
    current_standard_item_version,
)
from app.cleansing.models import CleanDecision, CleanStatus
from app.db.types import EXACT_DECIMAL_MAX, EXACT_DECIMAL_QUANTUM
from app.quotes.models import RawQuoteItem


CALCULATION_VERSION = "INTERNAL_STANDARD_PRICE_V1"


class PricingNotFound(LookupError):
    """The requested catalog identity does not exist."""


class PriceDraftChanged(RuntimeError):
    """The approved draft or current price-version pointer is stale."""

    error_code = "PRICE_DRAFT_CHANGED"


class NoEligiblePriceObservations(RuntimeError):
    """No current row is safe to use in a price draft."""

    error_code = "NO_ELIGIBLE_PRICE_OBSERVATIONS"


@dataclass(frozen=True)
class PriceStatistics:
    minimum: Decimal
    median: Decimal
    average: Decimal
    maximum: Decimal


@dataclass(frozen=True)
class PriceSource:
    document_id: int
    logical_name: str
    variant_id: int
    path: str
    source_sheet: str | None
    source_page: int | None
    source_row: int | None


@dataclass(frozen=True)
class PriceObservationDraft:
    raw_item_id: int
    clean_decision_id: int
    membership_decision_id: int
    unit_price: Decimal
    metadata_version_id: int | None
    supplier_name: str | None
    quote_date: date | None
    source: PriceSource
    clean_decision: CleanDecision
    membership_decision: ItemMembershipDecision


ExclusionReason = Literal[
    "EXCLUDED",
    "REVIEW_REQUIRED",
    "MEMBERSHIP_REJECTED",
    "MATCHED_TO_OTHER_ITEM",
    "MISSING_OR_INVALID_PRICE",
    "UNIT_INCOMPATIBLE",
]


@dataclass(frozen=True)
class PriceExclusion:
    raw_item_id: int
    reason: ExclusionReason
    clean_decision_id: int | None
    membership_decision_id: int | None
    source: PriceSource


@dataclass(frozen=True)
class PriceDraftContext:
    excluded_count: int
    review_required_count: int
    membership_rejected_count: int
    other_target_count: int
    invalid_price_count: int
    unit_incompatible_count: int


@dataclass(frozen=True)
class StandardPriceDraft:
    standard_item_id: int
    standard_item_version_id: int
    canonical_unit: str | None
    observation_count: int
    supplier_count: int
    latest_quote_date: date | None
    prices: PriceStatistics
    observations: tuple[PriceObservationDraft, ...]
    exclusions: tuple[PriceExclusion, ...]
    context: PriceDraftContext
    calculation_version: str
    fingerprint: str

    @property
    def decision_ids(self) -> tuple[int, ...]:
        return tuple(row.clean_decision_id for row in self.observations)

    @property
    def membership_decision_ids(self) -> tuple[int, ...]:
        return tuple(
            row.membership_decision_id for row in self.observations
        )


def _current_item_version(
    session: Session, standard_item_id: int
) -> StandardItemVersion:
    if session.get(StandardItem, standard_item_id) is None:
        raise PricingNotFound("standard item not found")
    version = current_standard_item_version(session, standard_item_id)
    if version is None:
        raise NoEligiblePriceObservations(
            "standard item has no descriptive version"
        )
    return version


def _source(raw: RawQuoteItem) -> PriceSource:
    variant = raw.source_variant
    document = variant.document
    return PriceSource(
        document_id=document.id,
        logical_name=document.logical_name,
        variant_id=variant.id,
        path=variant.path,
        source_sheet=raw.source_sheet,
        source_page=raw.source_page,
        source_row=raw.source_row,
    )


def _current_metadata(
    session: Session, document_id: int
) -> DocumentMetadataVersion | None:
    return current_document_metadata(session, document_id)


def _same_unit(observation: str | None, canonical: str | None) -> bool:
    if canonical is None:
        return True
    if observation is None:
        return False
    return observation.strip().casefold() == canonical.strip().casefold()


def _safe_positive_price(value: Decimal | None) -> bool:
    return (
        value is not None
        and value.is_finite()
        and value > 0
        and value <= EXACT_DECIMAL_MAX
        and value.as_tuple().exponent >= -6
    )


def _statistics(prices: list[Decimal]) -> PriceStatistics:
    ordered = sorted(prices)
    size = len(ordered)
    middle = size // 2
    median = (
        ordered[middle]
        if size % 2
        else (ordered[middle - 1] + ordered[middle]) / Decimal(2)
    )
    average = sum(ordered, Decimal(0)) / Decimal(size)
    return PriceStatistics(
        minimum=ordered[0],
        median=median,
        average=average,
        maximum=ordered[-1],
    )


def _fingerprint(
    item_version_id: int,
    observations: list[PriceObservationDraft],
    exclusions: list[PriceExclusion],
) -> str:
    payload = {
        "calculation_version": CALCULATION_VERSION,
        "standard_item_version_id": item_version_id,
        "evidence": sorted(
            (
                row.raw_item_id,
                row.clean_decision_id,
                row.membership_decision_id,
                row.metadata_version_id,
            )
            for row in observations
        ),
        "excluded_context": sorted(
            (
                row.raw_item_id,
                row.clean_decision_id,
                row.membership_decision_id,
                row.reason,
            )
            for row in exclusions
        ),
    }
    encoded = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _candidate_raw_items(
    session: Session, standard_item_id: int
) -> list[RawQuoteItem]:
    raw_ids = select(ItemMembershipDecision.raw_item_id).where(
        ItemMembershipDecision.standard_item_id == standard_item_id,
        ItemMembershipDecision.status == MembershipStatus.MATCHED,
    )
    return list(
        session.scalars(
            select(RawQuoteItem)
            .where(RawQuoteItem.id.in_(raw_ids))
            .order_by(RawQuoteItem.id)
        )
    )


def _latest_clean(
    session: Session, raw_item_id: int
) -> CleanDecision | None:
    return session.scalar(
        select(CleanDecision)
        .where(CleanDecision.raw_item_id == raw_item_id)
        .order_by(CleanDecision.id.desc())
        .limit(1)
    )


def _latest_membership(
    session: Session, raw_item_id: int
) -> ItemMembershipDecision | None:
    return current_membership(session, raw_item_id)


def calculate_standard_price(
    session: Session, standard_item_id: int
) -> StandardPriceDraft:
    """Calculate a deterministic draft without adding or changing rows."""

    item_version = _current_item_version(session, standard_item_id)
    observations: list[PriceObservationDraft] = []
    exclusions: list[PriceExclusion] = []
    counts: dict[str, int] = {
        "EXCLUDED": 0,
        "REVIEW_REQUIRED": 0,
        "MEMBERSHIP_REJECTED": 0,
        "MATCHED_TO_OTHER_ITEM": 0,
        "MISSING_OR_INVALID_PRICE": 0,
        "UNIT_INCOMPATIBLE": 0,
    }
    for raw in _candidate_raw_items(session, standard_item_id):
        clean = _latest_clean(session, raw.id)
        membership = _latest_membership(session, raw.id)
        reason: ExclusionReason | None = None
        if clean is None or clean.status != CleanStatus.INCLUDED:
            reason = (
                "REVIEW_REQUIRED"
                if clean is None
                or clean.status == CleanStatus.REVIEW_REQUIRED
                else "EXCLUDED"
            )
        elif membership is None or membership.status != MembershipStatus.MATCHED:
            reason = "MEMBERSHIP_REJECTED"
        elif membership.standard_item_id != standard_item_id:
            reason = "MATCHED_TO_OTHER_ITEM"
        elif not _safe_positive_price(clean.unit_price):
            reason = "MISSING_OR_INVALID_PRICE"
        elif not _same_unit(clean.unit_norm, item_version.canonical_unit):
            reason = "UNIT_INCOMPATIBLE"
        if reason is not None:
            counts[reason] += 1
            exclusions.append(
                PriceExclusion(
                    raw_item_id=raw.id,
                    reason=reason,
                    clean_decision_id=None if clean is None else clean.id,
                    membership_decision_id=(
                        None if membership is None else membership.id
                    ),
                    source=_source(raw),
                )
            )
            continue
        assert clean is not None and clean.unit_price is not None
        assert membership is not None
        metadata = _current_metadata(
            session, raw.source_variant.document_id
        )
        observations.append(
            PriceObservationDraft(
                raw_item_id=raw.id,
                clean_decision_id=clean.id,
                membership_decision_id=membership.id,
                unit_price=clean.unit_price,
                metadata_version_id=(
                    None if metadata is None else metadata.id
                ),
                supplier_name=(
                    None if metadata is None else metadata.supplier_name
                ),
                quote_date=None if metadata is None else metadata.quote_date,
                source=_source(raw),
                clean_decision=clean,
                membership_decision=membership,
            )
        )
    if not observations:
        raise NoEligiblePriceObservations(
            "standard item has no eligible positive price observations"
        )
    supplier_count = len(
        {
            row.supplier_name.strip().casefold()
            for row in observations
            if row.supplier_name is not None and row.supplier_name.strip()
        }
    )
    quote_dates = [
        row.quote_date for row in observations if row.quote_date is not None
    ]
    context = PriceDraftContext(
        excluded_count=counts["EXCLUDED"],
        review_required_count=counts["REVIEW_REQUIRED"],
        membership_rejected_count=counts["MEMBERSHIP_REJECTED"],
        other_target_count=counts["MATCHED_TO_OTHER_ITEM"],
        invalid_price_count=counts["MISSING_OR_INVALID_PRICE"],
        unit_incompatible_count=counts["UNIT_INCOMPATIBLE"],
    )
    return StandardPriceDraft(
        standard_item_id=standard_item_id,
        standard_item_version_id=item_version.id,
        canonical_unit=item_version.canonical_unit,
        observation_count=len(observations),
        supplier_count=supplier_count,
        latest_quote_date=max(quote_dates) if quote_dates else None,
        prices=_statistics([row.unit_price for row in observations]),
        observations=tuple(observations),
        exclusions=tuple(exclusions),
        context=context,
        calculation_version=CALCULATION_VERSION,
        fingerprint=_fingerprint(
            item_version.id,
            observations,
            exclusions,
        ),
    )


def current_standard_price_version(
    session: Session, standard_item_id: int
) -> StandardPriceVersion | None:
    return session.scalar(
        select(StandardPriceVersion)
        .where(StandardPriceVersion.standard_item_id == standard_item_id)
        .order_by(StandardPriceVersion.id.desc())
        .limit(1)
    )


def standard_price_versions(
    session: Session, standard_item_id: int
) -> list[StandardPriceVersion]:
    if session.get(StandardItem, standard_item_id) is None:
        raise PricingNotFound("standard item not found")
    return list(
        session.scalars(
            select(StandardPriceVersion)
            .where(StandardPriceVersion.standard_item_id == standard_item_id)
            .order_by(StandardPriceVersion.version_number.desc())
        )
    )


def approve_standard_price(
    session: Session,
    standard_item_id: int,
    *,
    expected_fingerprint: str,
    expected_current_version_id: int | None,
    approved_by: str,
) -> StandardPriceVersion:
    """Append an immutable version and its evidence in one caller transaction."""

    actor = approved_by.strip()
    if not actor or actor.casefold() == "system":
        raise ValueError("standard-price approval requires a human actor")
    draft = calculate_standard_price(session, standard_item_id)
    current = current_standard_price_version(session, standard_item_id)
    current_id = None if current is None else current.id
    if (
        draft.fingerprint != expected_fingerprint
        or current_id != expected_current_version_id
    ):
        raise PriceDraftChanged(
            "price draft or current approved version changed"
        )
    version_number = 1 if current is None else current.version_number + 1
    version = StandardPriceVersion(
        standard_item_id=standard_item_id,
        version_number=version_number,
        observation_count=draft.observation_count,
        supplier_count=draft.supplier_count,
        latest_quote_date=draft.latest_quote_date,
        minimum_price=draft.prices.minimum.quantize(
            EXACT_DECIMAL_QUANTUM, rounding=ROUND_HALF_UP
        ),
        median_price=draft.prices.median.quantize(
            EXACT_DECIMAL_QUANTUM, rounding=ROUND_HALF_UP
        ),
        average_price=draft.prices.average.quantize(
            EXACT_DECIMAL_QUANTUM, rounding=ROUND_HALF_UP
        ),
        maximum_price=draft.prices.maximum.quantize(
            EXACT_DECIMAL_QUANTUM, rounding=ROUND_HALF_UP
        ),
        calculation_version=draft.calculation_version,
        approved_by=actor,
    )
    version.observations = [
        StandardPriceObservation(
            clean_decision=row.clean_decision,
            membership_decision=row.membership_decision,
        )
        for row in draft.observations
    ]
    session.add(version)
    session.flush()
    return version
