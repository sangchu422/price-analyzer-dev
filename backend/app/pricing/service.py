"""Exact, append-only standard-price drafts and approvals."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import (
    Session,
    contains_eager,
    joinedload,
    selectinload,
)

from app.catalog.models import (
    DocumentMetadataVersion,
    ItemMembershipDecision,
    MembershipStatus,
    PriceAuditStatus,
    StandardItem,
    StandardItemVersion,
    StandardPriceObservation,
    StandardPriceObservationLineage,
    StandardPriceVersion,
)
from app.catalog.service import current_standard_item_version
from app.cleansing.models import CleanDecision, CleanStatus
from app.db.types import EXACT_DECIMAL_MAX, EXACT_DECIMAL_QUANTUM
from app.documents.models import SourceDocument, SourceVariant
from app.matching.normalization import normalize_search_text
from app.parsing.projection import current_raw_item_ids
from app.quotes.models import RawQuoteItem
from app.standard_database.models import (
    QuoteDocumentPurpose,
    QuoteDocumentRole,
)


CALCULATION_VERSION = "INTERNAL_STANDARD_PRICE_V4_LATEST_QUOTE_BUSINESS_SOURCE_DEDUP"
_COMPATIBLE_PREVIOUS_CALCULATION_VERSIONS = frozenset(
    {"INTERNAL_STANDARD_PRICE_V3_SEMANTIC_SOURCE_DEDUP"}
)
_EXPLICIT_REVISION_PATTERN = re.compile(
    r"(?:^|[\s_.()\-])(?:rev(?:ision)?|ver(?:sion)?|개정|수정)"
    r"[\s_.()\-]*(\d+(?:[._-]\d+)*)",
    re.IGNORECASE,
)
_CONFIRMED_QUOTE_DATE_QUALITIES = frozenset({"SOURCE_CONFIRMED"})


class PricingNotFound(LookupError):
    """The requested catalog identity does not exist."""


class PricingVersionNotFound(PricingNotFound):
    """The immutable price version does not belong to the requested item."""


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
    metadata_version: DocumentMetadataVersion | None
    lineage_raw_item_ids: tuple[int, ...]


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
    clean_status: CleanStatus | None
    membership_decision_id: int | None
    membership_status: MembershipStatus | None
    membership_standard_item_id: int | None
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


def _source(
    raw: RawQuoteItem,
    variant: SourceVariant,
    document: SourceDocument,
) -> PriceSource:
    return PriceSource(
        document_id=document.id,
        logical_name=document.logical_name,
        variant_id=variant.id,
        path=variant.path,
        source_sheet=raw.source_sheet,
        source_page=raw.source_page,
        source_row=raw.source_row,
    )


def _same_unit(observation: str | None, canonical: str | None) -> bool:
    if canonical is None:
        return True
    if observation is None:
        return False
    return normalize_search_text(observation) == normalize_search_text(
        canonical
    )


def _safe_positive_price(value: Decimal | None) -> bool:
    return (
        value is not None
        and value.is_finite()
        and value > 0
        and value <= EXACT_DECIMAL_MAX
        and value.as_tuple().exponent >= -6
    )


def _source_copy_key(
    raw: RawQuoteItem,
    clean: CleanDecision,
    variant: SourceVariant,
    document: SourceDocument,
    metadata: DocumentMetadataVersion | None,
    variant_signatures: dict[int, str],
) -> tuple[object, ...]:
    """Identify an exact business-source quote row across copied files.

    A document's recorded source remains immutable even when it is an exact
    copy.  A changed value remains part of an unrevisioned key; only an
    explicit revision series may replace its predecessor's value.
    """

    semantic_identity = _semantic_source_identity(
        variant,
        document,
        metadata,
        variant_signatures,
    )
    row_identity: tuple[object, ...] = (
        semantic_identity,
        (raw.source_sheet or "").strip().casefold(),
        raw.source_page,
        raw.source_row,
        (raw.source_cells or "").strip().casefold(),
    )
    # A confirmed revision means the business source itself has declared an
    # ordered replacement series, so a changed value belongs to the same row
    # lineage and is resolved by representative priority.  Without that
    # declaration, value fields are deliberately part of the key: a changed
    # value must remain an independent observation rather than an inferred
    # revision.
    if _explicit_revision(variant, document, metadata) is not None:
        return (
            _revision_series_identity(
                variant,
                document,
                metadata,
                variant_signatures,
            ),
            (raw.source_sheet or "").strip().casefold(),
            raw.source_page,
            raw.source_row,
            (raw.source_cells or "").strip().casefold(),
            normalize_search_text(clean.item_name_norm or raw.item_name_raw or ""),
            normalize_search_text(clean.spec_norm or raw.spec_raw or ""),
            normalize_search_text(clean.unit_norm or raw.unit_raw or ""),
            normalize_search_text(clean.maker_norm or raw.maker_raw or ""),
            "EXPLICIT_REVISION_SERIES",
        )
    return row_identity + (
        normalize_search_text(clean.item_name_norm or raw.item_name_raw or ""),
        normalize_search_text(clean.spec_norm or raw.spec_raw or ""),
        normalize_search_text(clean.unit_norm or raw.unit_raw or ""),
        str(clean.unit_price),
        normalize_search_text(clean.maker_norm or raw.maker_raw or ""),
    )


def _metadata_evidence(metadata: DocumentMetadataVersion | None) -> dict:
    if metadata is None:
        return {}
    try:
        value = json.loads(metadata.evidence_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _revision_value(value: object) -> tuple[int, ...] | None:
    if isinstance(value, bool) or value is None:
        return None
    match = _EXPLICIT_REVISION_PATTERN.search(str(value))
    if match is None:
        return None
    return tuple(int(part) for part in re.split(r"[._-]", match.group(1)))


def _explicit_revision_value(value: object) -> tuple[int, ...] | None:
    """Parse a revision field whose key already supplies the meaning."""

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return (value,)
    if isinstance(value, float) and value.is_integer():
        return (int(value),)
    match = re.fullmatch(r"\s*(\d+(?:[._-]\d+)*)\s*", str(value))
    if match is not None:
        return tuple(int(part) for part in re.split(r"[._-]", match.group(1)))
    return _revision_value(value)


def _explicit_revision(
    variant: SourceVariant,
    document: SourceDocument,
    metadata: DocumentMetadataVersion | None,
) -> tuple[int, ...] | None:
    evidence = _metadata_evidence(metadata)
    for key in (
        "revision",
        "revision_number",
        "quote_revision",
        "document_revision",
    ):
        revision = _explicit_revision_value(evidence.get(key))
        if revision is not None:
            return revision
    for value in (variant.path, document.logical_name):
        revision = _revision_value(value)
        if revision is not None:
            return revision
    return None


def _revision_family(value: str) -> str:
    return normalize_search_text(_EXPLICIT_REVISION_PATTERN.sub(" ", value))


def _semantic_source_identity(
    variant: SourceVariant,
    document: SourceDocument,
    metadata: DocumentMetadataVersion | None,
    variant_signatures: dict[int, str],
) -> tuple[object, ...]:
    """Return the V3 semantic source identity used for duplicate grouping."""

    supplier = normalize_search_text(
        "" if metadata is None else metadata.supplier_name or ""
    )
    project = normalize_search_text(
        "" if metadata is None else metadata.project_name or ""
    )
    if metadata is not None:
        # This is the established semantic duplicate set: a reviewed metadata
        # source (supplier, project, quote date) plus the parsed line identity.
        # It deliberately does not split copies merely because their registry
        # path or full parsed-document signature differs.
        return ("METADATA_SOURCE", supplier, project, metadata.quote_date)
    return (
        "PARSED_DOCUMENT",
        variant_signatures.get(variant.id, variant.sha256.casefold()),
    )


def _revision_series_identity(
    variant: SourceVariant,
    document: SourceDocument,
    metadata: DocumentMetadataVersion | None,
    variant_signatures: dict[int, str],
) -> tuple[object, ...]:
    """Identify a declared revision family without inferring one from value."""

    supplier = normalize_search_text(
        "" if metadata is None else metadata.supplier_name or ""
    )
    project = normalize_search_text(
        "" if metadata is None else metadata.project_name or ""
    )
    family = _revision_family(document.logical_name or variant.path)
    if supplier and project:
        return ("BUSINESS_REVISION", supplier, project)
    if family:
        return ("EXPLICIT_REVISION_FAMILY", family)
    return _semantic_source_identity(
        variant,
        document,
        metadata,
        variant_signatures,
    )


def _quote_date_rank(
    metadata: DocumentMetadataVersion | None,
) -> tuple[int, int]:
    """Prefer a latest date only when it is source-confirmed."""

    if metadata is None or metadata.quote_date is None:
        return (0, 0)
    evidence = _metadata_evidence(metadata)
    quote_date = evidence.get("quote_date")
    quality = quote_date.get("quality") if isinstance(quote_date, dict) else None
    confirmed = quality in _CONFIRMED_QUOTE_DATE_QUALITIES
    return (int(confirmed), metadata.quote_date.toordinal())


def _variant_quality_rank(variant: SourceVariant) -> tuple[int, int]:
    state = variant.security_state.strip().upper()
    security_rank = {
        "UNLOCKED": 3,
        "OPEN": 2,
        "UNKNOWN": 1,
    }.get(state, 0)
    return (security_rank, int(variant.selected_for_parsing_at_ingest))


def _representative_priority(
    row: tuple[
        RawQuoteItem,
        CleanDecision,
        ItemMembershipDecision,
        SourceVariant,
        SourceDocument,
        DocumentMetadataVersion | None,
    ],
) -> tuple[object, ...]:
    raw, _, _, variant, document, metadata = row
    revision = _explicit_revision(variant, document, metadata) or ()
    registered_at = variant.registered_at
    return (
        _quote_date_rank(metadata),
        (int(bool(revision)), revision),
        _variant_quality_rank(variant),
        registered_at,
        variant.id,
        document.id,
        raw.id,
    )


def _parsed_variant_signatures(
    session: Session,
    variant_ids: Iterable[int],
) -> dict[int, str]:
    """Hash parsed quote content, excluding registry paths and file metadata."""

    ids = tuple(dict.fromkeys(variant_ids))
    if not ids:
        return {}
    columns = (
        RawQuoteItem.source_sheet,
        RawQuoteItem.source_page,
        RawQuoteItem.source_row,
        RawQuoteItem.source_cells,
        RawQuoteItem.item_name_raw,
        RawQuoteItem.spec_raw,
        RawQuoteItem.unit_raw,
        RawQuoteItem.quantity_raw,
        RawQuoteItem.unit_price_raw,
        RawQuoteItem.amount_raw,
        RawQuoteItem.maker_raw,
    )
    digests = {variant_id: hashlib.sha256() for variant_id in ids}
    statement = (
        select(RawQuoteItem.source_variant_id, *columns)
        .where(RawQuoteItem.source_variant_id.in_(ids))
        .order_by(
            RawQuoteItem.source_variant_id,
            RawQuoteItem.source_sheet,
            RawQuoteItem.source_page,
            RawQuoteItem.source_row,
            RawQuoteItem.source_cells,
            RawQuoteItem.id,
        )
    )
    for row in session.execute(statement):
        variant_id = row[0]
        payload = json.dumps(
            list(row[1:]),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        digests[variant_id].update(payload)
        digests[variant_id].update(b"\n")
    return {
        variant_id: digest.hexdigest()
        for variant_id, digest in digests.items()
    }


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
    *,
    calculation_version: str = CALCULATION_VERSION,
    include_lineage: bool = True,
) -> str:
    payload = {
        "calculation_version": calculation_version,
        "standard_item_version_id": item_version_id,
        "evidence": sorted(
            (
                row.raw_item_id,
                row.clean_decision_id,
                row.membership_decision_id,
                row.metadata_version_id,
                (
                    row.lineage_raw_item_ids
                    if include_lineage
                    else ()
                ),
            )
            if include_lineage
            else (
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


def _captured_price_scalars_match_draft(
    version: StandardPriceVersion,
    draft: StandardPriceDraft,
) -> bool:
    """Compare a captured version with the draft at storage precision."""

    return (
        version.standard_item_version_id == draft.standard_item_version_id
        and version.observation_count == draft.observation_count
        and version.supplier_count == draft.supplier_count
        and version.latest_quote_date == draft.latest_quote_date
        and version.minimum_price
        == draft.prices.minimum.quantize(
            EXACT_DECIMAL_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        and version.median_price
        == draft.prices.median.quantize(
            EXACT_DECIMAL_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        and version.average_price
        == draft.prices.average.quantize(
            EXACT_DECIMAL_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        and version.maximum_price
        == draft.prices.maximum.quantize(
            EXACT_DECIMAL_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
    )


def price_version_matches_draft(
    version: StandardPriceVersion,
    draft: StandardPriceDraft,
) -> bool:
    """Accept an unchanged V3 capture while V4 only changes copied sources.

    V4 adds deterministic representative lineage.  A full version rewrite for
    every unaffected item would create thousands of duplicate immutable price
    records, so a V3 capture remains active only when its original V3 draft
    hash exactly matches the current evidence without a copy-lineage change.
    """

    # The evidence fingerprint is intentionally compact and does not encode
    # calculated statistics.  Always check the persisted scalar snapshot
    # first, including ExactDecimal's six-place storage precision, so a stale
    # price can never remain operational solely because raw evidence ids are
    # unchanged.
    if not _captured_price_scalars_match_draft(version, draft):
        return False
    if version.draft_fingerprint == draft.fingerprint:
        return True
    if version.calculation_version not in _COMPATIBLE_PREVIOUS_CALCULATION_VERSIONS:
        return False
    legacy_fingerprint = _fingerprint(
        draft.standard_item_version_id,
        list(draft.observations),
        list(draft.exclusions),
        calculation_version=version.calculation_version,
        include_lineage=False,
    )
    return version.draft_fingerprint == legacy_fingerprint


def _current_evidence_rows(
    session: Session,
    standard_item_id: int,
    *,
    raw_item_ids: tuple[int, ...] | None = None,
) -> list[
    tuple[
        RawQuoteItem,
        CleanDecision | None,
        ItemMembershipDecision | None,
        SourceVariant,
        SourceDocument,
        DocumentMetadataVersion | None,
    ]
]:
    current_raw = current_raw_item_ids()
    candidate_statement = (
        select(ItemMembershipDecision.raw_item_id)
        .join(
            current_raw,
            current_raw.c.raw_item_id == ItemMembershipDecision.raw_item_id,
        )
        .where(
        ItemMembershipDecision.standard_item_id == standard_item_id,
        ItemMembershipDecision.status == MembershipStatus.MATCHED,
        )
    )
    if raw_item_ids is not None:
        candidate_statement = candidate_statement.where(
            ItemMembershipDecision.raw_item_id.in_(raw_item_ids)
        )
    candidate_ids = candidate_statement.distinct().subquery()
    latest_clean = (
        select(
            CleanDecision.raw_item_id.label("raw_item_id"),
            func.max(CleanDecision.id).label("decision_id"),
        )
        .group_by(CleanDecision.raw_item_id)
        .subquery()
    )
    latest_membership = (
        select(
            ItemMembershipDecision.raw_item_id.label("raw_item_id"),
            func.max(ItemMembershipDecision.id).label("decision_id"),
        )
        .group_by(ItemMembershipDecision.raw_item_id)
        .subquery()
    )
    latest_metadata = (
        select(
            DocumentMetadataVersion.source_document_id.label("document_id"),
            func.max(DocumentMetadataVersion.id).label("metadata_id"),
        )
        .group_by(DocumentMetadataVersion.source_document_id)
        .subquery()
    )
    latest_role = (
        select(
            QuoteDocumentRole.document_id.label("document_id"),
            func.max(QuoteDocumentRole.id).label("role_id"),
        )
        .group_by(QuoteDocumentRole.document_id)
        .subquery()
    )
    statement = (
        select(
            RawQuoteItem,
            CleanDecision,
            ItemMembershipDecision,
            SourceVariant,
            SourceDocument,
            DocumentMetadataVersion,
        )
        .join(candidate_ids, candidate_ids.c.raw_item_id == RawQuoteItem.id)
        .join(
            SourceVariant,
            SourceVariant.id == RawQuoteItem.source_variant_id,
        )
        .join(
            SourceDocument,
            SourceDocument.id == SourceVariant.document_id,
        )
        .join(
            latest_role,
            latest_role.c.document_id == SourceDocument.id,
        )
        .join(
            QuoteDocumentRole,
            QuoteDocumentRole.id == latest_role.c.role_id,
        )
        .outerjoin(
            latest_clean,
            latest_clean.c.raw_item_id == RawQuoteItem.id,
        )
        .outerjoin(
            CleanDecision,
            CleanDecision.id == latest_clean.c.decision_id,
        )
        .outerjoin(
            latest_membership,
            latest_membership.c.raw_item_id == RawQuoteItem.id,
        )
        .outerjoin(
            ItemMembershipDecision,
            ItemMembershipDecision.id
            == latest_membership.c.decision_id,
        )
        .outerjoin(
            latest_metadata,
            latest_metadata.c.document_id == SourceDocument.id,
        )
        .outerjoin(
            DocumentMetadataVersion,
            DocumentMetadataVersion.id == latest_metadata.c.metadata_id,
        )
        .options(
            contains_eager(RawQuoteItem.source_variant)
            .contains_eager(SourceVariant.document)
        )
        .where(
            QuoteDocumentRole.purpose
            == QuoteDocumentPurpose.HISTORICAL_REFERENCE
        )
        .order_by(RawQuoteItem.id)
    )
    return [tuple(row) for row in session.execute(statement).all()]


def _draft_from_evidence_rows(
    item_version: StandardItemVersion,
    evidence_rows: list[
        tuple[
            RawQuoteItem,
            CleanDecision | None,
            ItemMembershipDecision | None,
            SourceVariant,
            SourceDocument,
            DocumentMetadataVersion | None,
        ]
    ],
    *,
    variant_signatures: dict[int, str],
) -> StandardPriceDraft:
    standard_item_id = item_version.standard_item_id
    observations: list[PriceObservationDraft] = []
    exclusions: list[PriceExclusion] = []
    eligible_by_source_key: dict[
        tuple[object, ...],
        list[
            tuple[
                RawQuoteItem,
                CleanDecision,
                ItemMembershipDecision,
                SourceVariant,
                SourceDocument,
                DocumentMetadataVersion | None,
            ]
        ],
    ] = {}
    counts: dict[str, int] = {
        "EXCLUDED": 0,
        "REVIEW_REQUIRED": 0,
        "MEMBERSHIP_REJECTED": 0,
        "MATCHED_TO_OTHER_ITEM": 0,
        "MISSING_OR_INVALID_PRICE": 0,
        "UNIT_INCOMPATIBLE": 0,
    }
    for (
        raw,
        clean,
        membership,
        variant,
        document,
        metadata,
    ) in evidence_rows:
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
                    clean_status=None if clean is None else clean.status,
                    membership_decision_id=(
                        None if membership is None else membership.id
                    ),
                    membership_status=(
                        None if membership is None else membership.status
                    ),
                    membership_standard_item_id=(
                        None
                        if membership is None
                        else membership.standard_item_id
                    ),
                    source=_source(raw, variant, document),
                )
            )
            continue
        assert clean is not None
        assert membership is not None
        source_copy_key = _source_copy_key(
            raw,
            clean,
            variant,
            document,
            metadata,
            variant_signatures,
        )
        eligible_by_source_key.setdefault(source_copy_key, []).append(
            (raw, clean, membership, variant, document, metadata)
        )

    for source_copy_key in sorted(eligible_by_source_key, key=repr):
        candidates = eligible_by_source_key[source_copy_key]
        raw, clean, membership, variant, document, metadata = max(
            candidates,
            key=_representative_priority,
        )
        assert clean.unit_price is not None
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
                source=_source(raw, variant, document),
                clean_decision=clean,
                membership_decision=membership,
                metadata_version=metadata,
                lineage_raw_item_ids=tuple(
                    sorted(candidate[0].id for candidate in candidates)
                ),
            )
        )
    observations.sort(key=lambda row: row.raw_item_id)
    if not observations:
        raise NoEligiblePriceObservations(
            "standard item has no eligible positive price observations "
            "from current historical references"
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


def _calculate_standard_price(
    session: Session,
    standard_item_id: int,
    *,
    raw_item_ids: tuple[int, ...] | None = None,
) -> StandardPriceDraft:
    """Calculate a deterministic draft without adding or changing rows."""

    item_version = _current_item_version(session, standard_item_id)
    evidence_rows = _current_evidence_rows(
        session,
        standard_item_id,
        raw_item_ids=raw_item_ids,
    )
    return _draft_from_evidence_rows(
        item_version,
        evidence_rows,
        variant_signatures=_parsed_variant_signatures(
            session,
            (row[3].id for row in evidence_rows),
        ),
    )


def _normalize_raw_item_ids(
    raw_item_ids: Iterable[int] | None,
) -> tuple[int, ...] | None:
    if raw_item_ids is None:
        return None
    values = tuple(raw_item_ids)
    if any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        for value in values
    ):
        raise ValueError("raw_item_ids must contain only positive integers")
    return tuple(sorted(set(values)))


def calculate_standard_price(
    session: Session,
    standard_item_id: int,
    *,
    raw_item_ids: Iterable[int] | None = None,
) -> StandardPriceDraft:
    """Read a deterministic draft, optionally restricted to explicit rows."""

    selected_ids = _normalize_raw_item_ids(raw_item_ids)
    with session.no_autoflush:
        return _calculate_standard_price(
            session,
            standard_item_id,
            raw_item_ids=selected_ids,
        )


def calculate_standard_prices(
    session: Session,
    standard_item_ids: list[int],
    *,
    chunk_size: int = 500,
) -> dict[int, StandardPriceDraft]:
    """Calculate many drafts with a bounded number of set-based queries."""

    if (
        isinstance(chunk_size, bool)
        or not isinstance(chunk_size, int)
        or chunk_size <= 0
    ):
        raise ValueError("chunk_size must be a positive integer")
    item_ids = list(dict.fromkeys(standard_item_ids))
    if not item_ids:
        return {}
    latest_versions = (
        select(
            StandardItemVersion.standard_item_id,
            func.max(StandardItemVersion.id).label("version_id"),
        )
        .where(StandardItemVersion.standard_item_id.in_(item_ids))
        .group_by(StandardItemVersion.standard_item_id)
        .subquery()
    )
    versions = {
        version.standard_item_id: version
        for version in session.scalars(
            select(StandardItemVersion).join(
                latest_versions,
                latest_versions.c.version_id == StandardItemVersion.id,
            )
        )
    }
    drafts: dict[int, StandardPriceDraft] = {}
    variant_signature_cache: dict[int, str] = {}
    for offset in range(0, len(item_ids), chunk_size):
        chunk = item_ids[offset : offset + chunk_size]
        evidence = _current_evidence_rows_for_items(session, chunk)
        evidence_variant_ids = {
            row[3].id
            for rows in evidence.values()
            for row in rows
        }
        variant_signature_cache.update(
            _parsed_variant_signatures(
                session,
                evidence_variant_ids - variant_signature_cache.keys(),
            )
        )
        for item_id in chunk:
            version = versions.get(item_id)
            if version is None:
                continue
            try:
                drafts[item_id] = _draft_from_evidence_rows(
                    version,
                    evidence.get(item_id, []),
                    variant_signatures=variant_signature_cache,
                )
            except NoEligiblePriceObservations:
                continue
    return drafts


def _current_evidence_rows_for_items(
    session: Session,
    standard_item_ids: list[int],
) -> dict[
    int,
    list[
        tuple[
            RawQuoteItem,
            CleanDecision | None,
            ItemMembershipDecision | None,
            SourceVariant,
            SourceDocument,
            DocumentMetadataVersion | None,
        ]
    ],
]:
    current_raw = current_raw_item_ids()
    candidate_rows = (
        select(
            ItemMembershipDecision.standard_item_id.label(
                "target_standard_item_id"
            ),
            ItemMembershipDecision.raw_item_id.label("raw_item_id"),
        )
        .join(
            current_raw,
            current_raw.c.raw_item_id == ItemMembershipDecision.raw_item_id,
        )
        .where(
            ItemMembershipDecision.standard_item_id.in_(standard_item_ids),
            ItemMembershipDecision.status == MembershipStatus.MATCHED,
        )
        .distinct()
        .subquery()
    )
    latest_clean = (
        select(
            CleanDecision.raw_item_id.label("raw_item_id"),
            func.max(CleanDecision.id).label("decision_id"),
        )
        .group_by(CleanDecision.raw_item_id)
        .subquery()
    )
    latest_membership = (
        select(
            ItemMembershipDecision.raw_item_id.label("raw_item_id"),
            func.max(ItemMembershipDecision.id).label("decision_id"),
        )
        .group_by(ItemMembershipDecision.raw_item_id)
        .subquery()
    )
    latest_metadata = (
        select(
            DocumentMetadataVersion.source_document_id.label("document_id"),
            func.max(DocumentMetadataVersion.id).label("metadata_id"),
        )
        .group_by(DocumentMetadataVersion.source_document_id)
        .subquery()
    )
    latest_role = (
        select(
            QuoteDocumentRole.document_id.label("document_id"),
            func.max(QuoteDocumentRole.id).label("role_id"),
        )
        .group_by(QuoteDocumentRole.document_id)
        .subquery()
    )
    statement = (
        select(
            candidate_rows.c.target_standard_item_id,
            RawQuoteItem,
            CleanDecision,
            ItemMembershipDecision,
            SourceVariant,
            SourceDocument,
            DocumentMetadataVersion,
        )
        .join(
            RawQuoteItem,
            RawQuoteItem.id == candidate_rows.c.raw_item_id,
        )
        .join(
            SourceVariant,
            SourceVariant.id == RawQuoteItem.source_variant_id,
        )
        .join(
            SourceDocument,
            SourceDocument.id == SourceVariant.document_id,
        )
        .join(
            latest_role,
            latest_role.c.document_id == SourceDocument.id,
        )
        .join(
            QuoteDocumentRole,
            QuoteDocumentRole.id == latest_role.c.role_id,
        )
        .outerjoin(
            latest_clean,
            latest_clean.c.raw_item_id == RawQuoteItem.id,
        )
        .outerjoin(
            CleanDecision,
            CleanDecision.id == latest_clean.c.decision_id,
        )
        .outerjoin(
            latest_membership,
            latest_membership.c.raw_item_id == RawQuoteItem.id,
        )
        .outerjoin(
            ItemMembershipDecision,
            ItemMembershipDecision.id
            == latest_membership.c.decision_id,
        )
        .outerjoin(
            latest_metadata,
            latest_metadata.c.document_id == SourceDocument.id,
        )
        .outerjoin(
            DocumentMetadataVersion,
            DocumentMetadataVersion.id == latest_metadata.c.metadata_id,
        )
        .where(
            QuoteDocumentRole.purpose
            == QuoteDocumentPurpose.HISTORICAL_REFERENCE
        )
        .order_by(
            candidate_rows.c.target_standard_item_id,
            RawQuoteItem.id,
        )
    )
    grouped: dict[
        int,
        list[
            tuple[
                RawQuoteItem,
                CleanDecision | None,
                ItemMembershipDecision | None,
                SourceVariant,
                SourceDocument,
                DocumentMetadataVersion | None,
            ]
        ],
    ] = {item_id: [] for item_id in standard_item_ids}
    for row in session.execute(statement):
        grouped[row[0]].append(tuple(row[1:]))
    return grouped


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
    session: Session,
    standard_item_id: int,
    *,
    after_id: int | None = None,
    limit: int = 50,
    include_observations: bool = True,
) -> tuple[list[StandardPriceVersion], int | None]:
    if session.get(StandardItem, standard_item_id) is None:
        raise PricingNotFound("standard item not found")
    statement = (
        select(StandardPriceVersion)
        .where(StandardPriceVersion.standard_item_id == standard_item_id)
        .order_by(StandardPriceVersion.id)
        .limit(limit + 1)
        .options(joinedload(StandardPriceVersion.standard_item_version))
    )
    if include_observations:
        statement = statement.options(
            selectinload(StandardPriceVersion.observations)
            .joinedload(StandardPriceObservation.clean_decision)
            .joinedload(CleanDecision.raw_item)
            .joinedload(RawQuoteItem.source_variant)
            .joinedload(SourceVariant.document),
            selectinload(StandardPriceVersion.observations).joinedload(
                StandardPriceObservation.metadata_version
            ),
        )
    if after_id is not None:
        statement = statement.where(StandardPriceVersion.id > after_id)
    rows = list(session.scalars(statement))
    has_more = len(rows) > limit
    page = rows[:limit]
    return page, page[-1].id if has_more and page else None


def standard_price_version(
    session: Session,
    standard_item_id: int,
    version_id: int,
) -> StandardPriceVersion:
    version = session.scalar(
        select(StandardPriceVersion)
        .where(
            StandardPriceVersion.id == version_id,
            StandardPriceVersion.standard_item_id == standard_item_id,
        )
        .options(
            joinedload(StandardPriceVersion.standard_item_version),
            selectinload(StandardPriceVersion.observations)
            .joinedload(StandardPriceObservation.clean_decision)
            .joinedload(CleanDecision.raw_item)
            .joinedload(RawQuoteItem.source_variant)
            .joinedload(SourceVariant.document),
            selectinload(StandardPriceVersion.observations).joinedload(
                StandardPriceObservation.metadata_version
            ),
        )
    )
    if version is None:
        raise PricingVersionNotFound("standard price version not found")
    return version


def approve_standard_price(
    session: Session,
    standard_item_id: int,
    *,
    expected_fingerprint: str,
    expected_current_version_id: int | None,
    approved_by: str,
    raw_item_ids: Iterable[int] | None = None,
) -> StandardPriceVersion:
    """Append an immutable version and its evidence in one caller transaction."""

    actor = approved_by.strip()
    if not actor or actor.casefold() == "system":
        raise ValueError("standard-price approval requires a human actor")
    draft = calculate_standard_price(
        session,
        standard_item_id,
        raw_item_ids=raw_item_ids,
    )
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
        standard_item_version_id=draft.standard_item_version_id,
        standard_item_version=session.get(
            StandardItemVersion,
            draft.standard_item_version_id,
        ),
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
        audit_status=PriceAuditStatus.CAPTURED,
        draft_fingerprint=draft.fingerprint,
        excluded_count=draft.context.excluded_count,
        review_required_count=draft.context.review_required_count,
        exclusion_context_json=_serialize_exclusions(draft.exclusions),
        approved_by=actor,
    )
    version.observations = [
        StandardPriceObservation(
            clean_decision=row.clean_decision,
            membership_decision=row.membership_decision,
            metadata_version=row.metadata_version,
            lineage=[
                StandardPriceObservationLineage(
                    raw_item_id=raw_item_id,
                )
                for raw_item_id in row.lineage_raw_item_ids
            ],
        )
        for row in draft.observations
    ]
    session.add(version)
    session.flush()
    return version


def _serialize_exclusions(
    exclusions: tuple[PriceExclusion, ...],
) -> str:
    payload = [
        {
            "raw_item_id": row.raw_item_id,
            "reason": row.reason,
            "clean_decision_id": row.clean_decision_id,
            "clean_status": (
                None if row.clean_status is None else row.clean_status.value
            ),
            "membership_decision_id": row.membership_decision_id,
            "membership_status": (
                None
                if row.membership_status is None
                else row.membership_status.value
            ),
            "membership_standard_item_id": (
                row.membership_standard_item_id
            ),
            "source": {
                "document_id": row.source.document_id,
                "logical_name": row.source.logical_name,
                "variant_id": row.source.variant_id,
                "path": row.source.path,
                "sheet": row.source.source_sheet,
                "page": row.source.source_page,
                "row": row.source.source_row,
            },
        }
        for row in exclusions
    ]
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
