"""Read-only quote comparison with explicit, auditable evidence pointers."""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app.catalog.models import (
    ItemMembershipDecision,
    MembershipStatus,
    StandardItemVersion,
    StandardPriceVersion,
)
from app.catalog.service import CandidateEmbeddingRuntime
from app.cleansing.models import CleanDecision, CleanStatus
from app.documents.models import SourceDocument, SourceVariant
from app.ingestion.service import preferred_variant_for
from app.matching.candidates import (
    CandidateItem,
    CandidateScore,
    MatchQuery,
    rank_candidate_batch,
)
from app.matching.normalization import normalize_search_text
from app.quotes.models import RawQuoteItem


MatchStatus = Literal[
    "EXCLUDED",
    "REVIEW_REQUIRED",
    "CANDIDATE",
    "NO_MATCH",
    "MATCHED_NO_PRICE",
    "MATCHED",
]
Assessment = Literal[
    "NOT_APPLICABLE",
    "REVIEW_REQUIRED",
    "LOW",
    "WITHIN_RANGE",
    "REVIEW",
    "HIGH",
]
MarketLookupStatus = Literal["NOT_REQUIRED", "FUTURE_MARKET_LOOKUP"]

PERCENT_QUANTUM = Decimal("0.000001")


class AnalysisNotFound(LookupError):
    """The requested logical quote document does not exist."""


@dataclass(frozen=True)
class AnalysisSource:
    document_id: int
    logical_name: str
    variant_id: int
    path: str
    sha256: str
    sheet: str | None
    page: int | None
    row: int | None
    cells: str | None
    parser_name: str
    parser_version: str


@dataclass(frozen=True)
class AnalysisCandidate:
    standard_item_id: int
    standard_item_version_id: int
    canonical_name: str
    canonical_spec: str | None
    canonical_unit: str | None
    final_score: Decimal
    method: str
    matched_tokens: tuple[str, ...]
    embedding_status: str
    embedding_model: str | None


@dataclass(frozen=True)
class AnalysisLine:
    raw_item_id: int
    item_name: str | None
    spec: str | None
    unit: str | None
    quote_unit_price: Decimal | None
    match_status: MatchStatus
    assessment: Assessment
    reference_price: Decimal | None
    minimum_price: Decimal | None
    average_price: Decimal | None
    maximum_price: Decimal | None
    variance_amount: Decimal | None
    variance_percent: Decimal | None
    clean_decision_id: int | None
    membership_decision_id: int | None
    standard_item_id: int | None
    standard_item_version_id: int | None
    canonical_name: str | None
    canonical_spec: str | None
    canonical_unit: str | None
    standard_price_version_id: int | None
    standard_price_item_version_id: int | None
    market_price_lookup_required: bool
    market_price_lookup_status: MarketLookupStatus
    candidates: tuple[AnalysisCandidate, ...]
    source: AnalysisSource


@dataclass(frozen=True)
class DocumentAnalysis:
    document_id: int
    logical_name: str
    lines: tuple[AnalysisLine, ...]
    next_cursor: int | None
    limit: int


@dataclass(frozen=True)
class AnalysisDocumentSummary:
    id: int
    logical_name: str
    raw_item_count: int
    included_count: int
    excluded_count: int
    review_required_count: int
    undecided_count: int
    analysis_ready: bool


@dataclass(frozen=True)
class AnalysisDocumentPage:
    items: tuple[AnalysisDocumentSummary, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class _CatalogProjection:
    versions: dict[int, StandardItemVersion]
    prices: dict[int, StandardPriceVersion]
    candidate_items: tuple[CandidateItem, ...]


def list_analysis_documents(
    session: Session,
    *,
    limit: int = 50,
    offset: int = 0,
) -> AnalysisDocumentPage:
    """List logical documents with current cleansing counts in bounded queries."""

    _validate_list_page(limit=limit, offset=offset)
    total = session.scalar(select(func.count(SourceDocument.id))) or 0
    documents = list(
        session.scalars(
            select(SourceDocument)
            .options(selectinload(SourceDocument.variants))
            .order_by(SourceDocument.logical_name, SourceDocument.id)
            .offset(offset)
            .limit(limit)
        )
    )
    parsing_variants = _parsing_variant_ids(session, documents)
    counts = _document_counts(session, parsing_variants)
    items = []
    for document in documents:
        raw_count, included, excluded, review, undecided = counts.get(
            document.id, (0, 0, 0, 0, 0)
        )
        items.append(
            AnalysisDocumentSummary(
                id=document.id,
                logical_name=document.logical_name,
                raw_item_count=raw_count,
                included_count=included,
                excluded_count=excluded,
                review_required_count=review,
                undecided_count=undecided,
                analysis_ready=raw_count > 0 and undecided == 0,
            )
        )
    return AnalysisDocumentPage(tuple(items), total, limit, offset)


def analyze_document(
    session: Session,
    document_id: int,
    *,
    after_id: int | None = None,
    limit: int = 100,
    match_status: MatchStatus | Collection[MatchStatus] | None = None,
    assessment: Assessment | None = None,
    review_percent: Decimal = Decimal("10"),
    high_percent: Decimal = Decimal("20"),
    embedding_runtime: CandidateEmbeddingRuntime | None = None,
    top_n: int = 5,
) -> DocumentAnalysis:
    """Compare current parsed rows without creating catalog decisions."""

    with session.no_autoflush:
        return _analyze_document(
            session,
            document_id,
            after_id=after_id,
            limit=limit,
            match_status=match_status,
            assessment=assessment,
            review_percent=review_percent,
            high_percent=high_percent,
            embedding_runtime=embedding_runtime,
            top_n=top_n,
        )


def _analyze_document(
    session: Session,
    document_id: int,
    *,
    after_id: int | None,
    limit: int,
    match_status: MatchStatus | Collection[MatchStatus] | None,
    assessment: Assessment | None,
    review_percent: Decimal,
    high_percent: Decimal,
    embedding_runtime: CandidateEmbeddingRuntime | None,
    top_n: int,
) -> DocumentAnalysis:
    _validate_detail_page(after_id=after_id, limit=limit, top_n=top_n)
    _validate_thresholds(review_percent, high_percent)
    document = session.scalar(
        select(SourceDocument)
        .where(SourceDocument.id == document_id)
        .options(selectinload(SourceDocument.variants))
    )
    if document is None:
        raise AnalysisNotFound("analysis document not found")
    parsing_variant_id = _parsing_variant_ids(
        session, [document]
    ).get(document.id)
    statuses = _status_filter(match_status)
    projection = _catalog_projection(session)
    runtime = embedding_runtime or CandidateEmbeddingRuntime(None, None, None)
    matches: list[AnalysisLine] = []
    cursor = after_id
    exhausted = False
    chunk_size = max(100, limit * 2)
    while len(matches) <= limit and not exhausted:
        rows = _current_rows(
            session,
            parsing_variant_id=parsing_variant_id,
            after_id=cursor,
            limit=chunk_size,
        )
        if not rows:
            break
        cursor = rows[-1][0].id
        exhausted = len(rows) < chunk_size
        candidates_by_raw_id = _candidate_batch(
            rows=rows,
            projection=projection,
            runtime=runtime,
            top_n=top_n,
        )
        for raw, variant, clean, membership in rows:
            line = _classify_line(
                raw=raw,
                variant=variant,
                document=document,
                clean=clean,
                membership=membership,
                projection=projection,
                review_percent=review_percent,
                high_percent=high_percent,
                candidates=candidates_by_raw_id.get(raw.id, ()),
            )
            if statuses is not None and line.match_status not in statuses:
                continue
            if assessment is not None and line.assessment != assessment:
                continue
            matches.append(line)
            if len(matches) > limit:
                break
    has_more = len(matches) > limit
    page = tuple(matches[:limit])
    next_cursor = page[-1].raw_item_id if has_more and page else None
    return DocumentAnalysis(
        document_id=document.id,
        logical_name=document.logical_name,
        lines=page,
        next_cursor=next_cursor,
        limit=limit,
    )


def _latest_decision_ids(model: type, id_label: str):
    return (
        select(
            model.raw_item_id.label("raw_item_id"),
            func.max(model.id).label(id_label),
        )
        .group_by(model.raw_item_id)
        .subquery()
    )


def _current_rows(
    session: Session,
    *,
    parsing_variant_id: int | None,
    after_id: int | None,
    limit: int,
) -> list[
    tuple[
        RawQuoteItem,
        SourceVariant,
        CleanDecision | None,
        ItemMembershipDecision | None,
    ]
]:
    if parsing_variant_id is None:
        return []
    latest_clean = _latest_decision_ids(CleanDecision, "clean_id")
    latest_membership = _latest_decision_ids(
        ItemMembershipDecision, "membership_id"
    )
    statement = (
        select(
            RawQuoteItem,
            SourceVariant,
            CleanDecision,
            ItemMembershipDecision,
        )
        .join(
            SourceVariant,
            SourceVariant.id == RawQuoteItem.source_variant_id,
        )
        .outerjoin(
            latest_clean,
            latest_clean.c.raw_item_id == RawQuoteItem.id,
        )
        .outerjoin(
            CleanDecision,
            CleanDecision.id == latest_clean.c.clean_id,
        )
        .outerjoin(
            latest_membership,
            latest_membership.c.raw_item_id == RawQuoteItem.id,
        )
        .outerjoin(
            ItemMembershipDecision,
            ItemMembershipDecision.id == latest_membership.c.membership_id,
        )
        .where(SourceVariant.id == parsing_variant_id)
        .order_by(RawQuoteItem.id)
        .limit(limit)
    )
    if after_id is not None:
        statement = statement.where(RawQuoteItem.id > after_id)
    return [tuple(row) for row in session.execute(statement)]


def _catalog_projection(session: Session) -> _CatalogProjection:
    latest_versions = (
        select(
            StandardItemVersion.standard_item_id.label("standard_item_id"),
            func.max(StandardItemVersion.id).label("version_id"),
        )
        .group_by(StandardItemVersion.standard_item_id)
        .subquery()
    )
    versions = list(
        session.scalars(
            select(StandardItemVersion)
            .join(
                latest_versions,
                latest_versions.c.version_id == StandardItemVersion.id,
            )
            .order_by(StandardItemVersion.standard_item_id)
        )
    )
    latest_prices = (
        select(
            StandardPriceVersion.standard_item_id.label("standard_item_id"),
            func.max(StandardPriceVersion.id).label("price_id"),
        )
        .group_by(StandardPriceVersion.standard_item_id)
        .subquery()
    )
    prices = list(
        session.scalars(
            select(StandardPriceVersion).join(
                latest_prices,
                latest_prices.c.price_id == StandardPriceVersion.id,
            )
        )
    )
    by_id = {version.standard_item_id: version for version in versions}
    prices_by_id = {price.standard_item_id: price for price in prices}
    return _CatalogProjection(
        versions=by_id,
        prices=prices_by_id,
        candidate_items=tuple(
            CandidateItem(
                standard_item_id=version.standard_item_id,
                name=version.canonical_name,
                spec=version.canonical_spec,
                unit=version.canonical_unit,
                aliases=_aliases(version.aliases_json),
            )
            for version in versions
        ),
    )


def _classify_line(
    *,
    raw: RawQuoteItem,
    variant: SourceVariant,
    document: SourceDocument,
    clean: CleanDecision | None,
    membership: ItemMembershipDecision | None,
    projection: _CatalogProjection,
    review_percent: Decimal,
    high_percent: Decimal,
    candidates: tuple[AnalysisCandidate, ...],
) -> AnalysisLine:
    source = AnalysisSource(
        document_id=document.id,
        logical_name=document.logical_name,
        variant_id=variant.id,
        path=variant.path,
        sha256=variant.sha256,
        sheet=raw.source_sheet,
        page=raw.source_page,
        row=raw.source_row,
        cells=raw.source_cells,
        parser_name=raw.parser_name,
        parser_version=raw.parser_version,
    )
    matched_item_version = (
        projection.versions.get(membership.standard_item_id)
        if membership is not None
        and membership.status == MembershipStatus.MATCHED
        and membership.standard_item_id is not None
        else None
    )
    base = {
        "raw_item_id": raw.id,
        "item_name": (
            clean.item_name_norm
            if clean is not None and clean.item_name_norm is not None
            else raw.item_name_raw
        ),
        "spec": (
            clean.spec_norm
            if clean is not None and clean.spec_norm is not None
            else raw.spec_raw
        ),
        "unit": (
            clean.unit_norm
            if clean is not None and clean.unit_norm is not None
            else raw.unit_raw
        ),
        "quote_unit_price": None if clean is None else clean.unit_price,
        "clean_decision_id": None if clean is None else clean.id,
        "membership_decision_id": (
            None if membership is None else membership.id
        ),
        "standard_item_id": (
            None if membership is None else membership.standard_item_id
        ),
        "canonical_name": (
            None
            if matched_item_version is None
            else matched_item_version.canonical_name
        ),
        "canonical_spec": (
            None
            if matched_item_version is None
            else matched_item_version.canonical_spec
        ),
        "canonical_unit": (
            None
            if matched_item_version is None
            else matched_item_version.canonical_unit
        ),
        "source": source,
    }
    if clean is None or clean.status == CleanStatus.REVIEW_REQUIRED:
        return _unpriced_line(
            **base,
            match_status="REVIEW_REQUIRED",
            assessment="REVIEW_REQUIRED",
        )
    if clean.status == CleanStatus.EXCLUDED:
        return _unpriced_line(
            **base,
            match_status="EXCLUDED",
            assessment="NOT_APPLICABLE",
        )
    if (
        membership is not None
        and membership.status == MembershipStatus.MATCHED
        and membership.standard_item_id is not None
    ):
        item_id = membership.standard_item_id
        item_version = projection.versions.get(item_id)
        price = projection.prices.get(item_id)
        if price is None:
            return _unpriced_line(
                **base,
                match_status="MATCHED_NO_PRICE",
                assessment="REVIEW_REQUIRED",
                standard_item_version_id=(
                    None if item_version is None else item_version.id
                ),
            )
        quote_price = clean.unit_price
        assessment: Assessment = "REVIEW_REQUIRED"
        amount = None
        percent = None
        if quote_price is not None and quote_price.is_finite():
            amount = quote_price - price.median_price
            exact_percent = amount / price.median_price * Decimal("100")
            assessment = _assessment(
                exact_percent,
                review_percent=review_percent,
                high_percent=high_percent,
            )
            percent = exact_percent.quantize(
                PERCENT_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
        return AnalysisLine(
            **base,
            match_status="MATCHED",
            assessment=assessment,
            reference_price=price.median_price,
            minimum_price=price.minimum_price,
            average_price=price.average_price,
            maximum_price=price.maximum_price,
            variance_amount=amount,
            variance_percent=percent,
            standard_item_version_id=(
                None if item_version is None else item_version.id
            ),
            standard_price_version_id=price.id,
            standard_price_item_version_id=price.standard_item_version_id,
            market_price_lookup_required=False,
            market_price_lookup_status="NOT_REQUIRED",
            candidates=(),
        )
    if candidates:
        return _unpriced_line(
            **base,
            match_status="CANDIDATE",
            assessment="REVIEW_REQUIRED",
            candidates=candidates,
        )
    return _unpriced_line(
        **base,
        match_status="NO_MATCH",
        assessment="REVIEW_REQUIRED",
        market_lookup=True,
    )


def _unpriced_line(
    *,
    raw_item_id: int,
    item_name: str | None,
    spec: str | None,
    unit: str | None,
    quote_unit_price: Decimal | None,
    clean_decision_id: int | None,
    membership_decision_id: int | None,
    standard_item_id: int | None,
    canonical_name: str | None,
    canonical_spec: str | None,
    canonical_unit: str | None,
    source: AnalysisSource,
    match_status: MatchStatus,
    assessment: Assessment,
    standard_item_version_id: int | None = None,
    candidates: tuple[AnalysisCandidate, ...] = (),
    market_lookup: bool = False,
) -> AnalysisLine:
    return AnalysisLine(
        raw_item_id=raw_item_id,
        item_name=item_name,
        spec=spec,
        unit=unit,
        quote_unit_price=quote_unit_price,
        match_status=match_status,
        assessment=assessment,
        reference_price=None,
        minimum_price=None,
        average_price=None,
        maximum_price=None,
        variance_amount=None,
        variance_percent=None,
        clean_decision_id=clean_decision_id,
        membership_decision_id=membership_decision_id,
        standard_item_id=standard_item_id,
        standard_item_version_id=standard_item_version_id,
        canonical_name=canonical_name,
        canonical_spec=canonical_spec,
        canonical_unit=canonical_unit,
        standard_price_version_id=None,
        standard_price_item_version_id=None,
        market_price_lookup_required=market_lookup,
        market_price_lookup_status=(
            "FUTURE_MARKET_LOOKUP" if market_lookup else "NOT_REQUIRED"
        ),
        candidates=candidates,
        source=source,
    )


def _candidate_batch(
    *,
    rows: list[
        tuple[
            RawQuoteItem,
            SourceVariant,
            CleanDecision | None,
            ItemMembershipDecision | None,
        ]
    ],
    projection: _CatalogProjection,
    runtime: CandidateEmbeddingRuntime,
    top_n: int,
) -> dict[int, tuple[AnalysisCandidate, ...]]:
    eligible: list[tuple[RawQuoteItem, CleanDecision, MatchQuery]] = []
    for raw, _, clean, membership in rows:
        if clean is None or clean.status != CleanStatus.INCLUDED:
            continue
        if (
            membership is not None
            and membership.status == MembershipStatus.MATCHED
        ):
            continue
        name = clean.item_name_norm or raw.item_name_raw or ""
        if not normalize_search_text(name):
            continue
        eligible.append(
            (
                raw,
                clean,
                MatchQuery(
                    name=name,
                    spec=clean.spec_norm,
                    unit=clean.unit_norm,
                ),
            )
        )
    if not eligible or not projection.candidate_items:
        return {raw.id: () for raw, _, _ in eligible}
    ranked = rank_candidate_batch(
        queries=[query for _, _, query in eligible],
        items=projection.candidate_items,
        top_n=top_n,
        embedding_client=runtime.client,
        embedding_index=runtime.index,
    )
    return {
        raw.id: _analysis_candidates(
            scores,
            projection,
            embedding_model=(
                runtime.model
                or (
                    runtime.index.metadata.model
                    if runtime.index is not None
                    else None
                )
            ),
        )
        for (raw, _, _), scores in zip(eligible, ranked, strict=True)
    }


def _analysis_candidates(
    scores: list[CandidateScore],
    projection: _CatalogProjection,
    *,
    embedding_model: str | None,
) -> tuple[AnalysisCandidate, ...]:
    return tuple(
        AnalysisCandidate(
            standard_item_id=score.standard_item_id,
            standard_item_version_id=projection.versions[
                score.standard_item_id
            ].id,
            canonical_name=projection.versions[
                score.standard_item_id
            ].canonical_name,
            canonical_spec=projection.versions[
                score.standard_item_id
            ].canonical_spec,
            canonical_unit=projection.versions[
                score.standard_item_id
            ].canonical_unit,
            final_score=score.final_score,
            method=score.method,
            matched_tokens=score.matched_tokens,
            embedding_status=score.embedding_status,
            embedding_model=(
                embedding_model
                if score.embedding_status in {"AVAILABLE", "MOCK_ONLY"}
                else None
            ),
        )
        for score in scores
    )


def _assessment(
    percent: Decimal,
    *,
    review_percent: Decimal,
    high_percent: Decimal,
) -> Assessment:
    if percent < -review_percent:
        return "LOW"
    if percent <= review_percent:
        return "WITHIN_RANGE"
    if percent <= high_percent:
        return "REVIEW"
    return "HIGH"


def _document_counts(
    session: Session,
    parsing_variants: dict[int, int | None],
) -> dict[int, tuple[int, int, int, int, int]]:
    variant_ids = [
        variant_id
        for variant_id in parsing_variants.values()
        if variant_id is not None
    ]
    if not variant_ids:
        return {}
    latest_clean = _latest_decision_ids(CleanDecision, "clean_id")
    statement = (
        select(
            SourceVariant.document_id,
            func.count(RawQuoteItem.id),
            func.sum(
                case((CleanDecision.status == CleanStatus.INCLUDED, 1), else_=0)
            ),
            func.sum(
                case((CleanDecision.status == CleanStatus.EXCLUDED, 1), else_=0)
            ),
            func.sum(
                case(
                    (CleanDecision.status == CleanStatus.REVIEW_REQUIRED, 1),
                    else_=0,
                )
            ),
            func.sum(case((CleanDecision.id.is_(None), 1), else_=0)),
        )
        .join(
            RawQuoteItem,
            RawQuoteItem.source_variant_id == SourceVariant.id,
        )
        .outerjoin(
            latest_clean,
            latest_clean.c.raw_item_id == RawQuoteItem.id,
        )
        .outerjoin(
            CleanDecision,
            CleanDecision.id == latest_clean.c.clean_id,
        )
        .where(SourceVariant.id.in_(variant_ids))
        .group_by(SourceVariant.document_id)
    )
    return {
        int(row[0]): tuple(int(value or 0) for value in row[1:])
        for row in session.execute(statement)
    }


def _parsing_variant_ids(
    session: Session,
    documents: list[SourceDocument],
) -> dict[int, int | None]:
    """Resolve current preferred content to the one variant owning its rows."""

    if not documents:
        return {}
    document_ids = [document.id for document in documents]
    row_owner_ids = set(
        session.scalars(
            select(RawQuoteItem.source_variant_id)
            .join(
                SourceVariant,
                SourceVariant.id == RawQuoteItem.source_variant_id,
            )
            .where(SourceVariant.document_id.in_(document_ids))
            .distinct()
        )
    )
    result: dict[int, int | None] = {}
    for document in documents:
        if not document.variants:
            result[document.id] = None
            continue
        preferred = preferred_variant_for(document)
        if preferred.id in row_owner_ids:
            result[document.id] = preferred.id
            continue
        parsed_siblings = sorted(
            (
                variant.id
                for variant in document.variants
                if variant.sha256 == preferred.sha256
                and variant.id in row_owner_ids
            )
        )
        result[document.id] = (
            parsed_siblings[0] if parsed_siblings else preferred.id
        )
    return result


def _aliases(value: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(alias for alias in parsed if isinstance(alias, str))


def _status_filter(
    value: MatchStatus | Collection[MatchStatus] | None,
) -> frozenset[MatchStatus] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return frozenset((value,))
    return frozenset(value)


def _validate_list_page(*, limit: int, offset: int) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if isinstance(offset, bool) or offset < 0:
        raise ValueError("offset must be non-negative")


def _validate_detail_page(
    *,
    after_id: int | None,
    limit: int,
    top_n: int,
) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if after_id is not None and (
        isinstance(after_id, bool) or after_id < 0
    ):
        raise ValueError("after_id must be non-negative")
    if isinstance(top_n, bool) or not 1 <= top_n <= 50:
        raise ValueError("top_n must be between 1 and 50")


def _validate_thresholds(review: Decimal, high: Decimal) -> None:
    if (
        not review.is_finite()
        or not high.is_finite()
        or review < 0
        or high < review
    ):
        raise ValueError(
            "variance thresholds must be finite and high >= review >= 0"
        )
