"""Current operational projections over immutable standard evidence."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.catalog.models import (
    ItemMembershipDecision,
    MembershipStatus,
    StandardPriceVersion,
)
from app.cleansing.models import CleanDecision, CleanStatus
from app.documents.models import SourceDocument, SourceVariant
from app.parsing.projection import current_raw_item_ids
from app.pricing.service import (
    calculate_standard_prices,
    price_version_matches_draft,
)
from app.quotes.models import RawQuoteItem
from app.standard_database.models import (
    QuoteDocumentPurpose,
    QuoteDocumentRole,
    StandardBuildStatus,
    StandardDatabaseBuildProjection,
    StandardDatabaseBuildRun,
    StandardOperationalStatus,
)


def _latest(parent_column, id_column, *, name: str):
    return (
        select(
            parent_column.label("parent_id"),
            func.max(id_column).label("row_id"),
        )
        .group_by(parent_column)
        .subquery(name)
    )


def current_standard_member_counts_subquery(*, name: str):
    """Build the reusable current historical-member count relation."""

    latest_memberships = _latest(
        ItemMembershipDecision.raw_item_id,
        ItemMembershipDecision.id,
        name=f"{name}_latest_membership",
    )
    latest_cleans = _latest(
        CleanDecision.raw_item_id,
        CleanDecision.id,
        name=f"{name}_latest_clean",
    )
    latest_roles = _latest(
        QuoteDocumentRole.document_id,
        QuoteDocumentRole.id,
        name=f"{name}_latest_role",
    )
    current_raw = current_raw_item_ids()
    return (
        select(
            ItemMembershipDecision.standard_item_id.label(
                "standard_item_id"
            ),
            func.count(ItemMembershipDecision.id).label("member_count"),
        )
        .join(
            latest_memberships,
            latest_memberships.c.row_id == ItemMembershipDecision.id,
        )
        .join(
            latest_cleans,
            latest_cleans.c.parent_id
            == ItemMembershipDecision.raw_item_id,
        )
        .join(CleanDecision, CleanDecision.id == latest_cleans.c.row_id)
        .join(
            RawQuoteItem,
            RawQuoteItem.id == ItemMembershipDecision.raw_item_id,
        )
        .join(current_raw, current_raw.c.raw_item_id == RawQuoteItem.id)
        .join(
            SourceVariant,
            SourceVariant.id == RawQuoteItem.source_variant_id,
        )
        .join(
            SourceDocument,
            SourceDocument.id == SourceVariant.document_id,
        )
        .join(
            latest_roles,
            latest_roles.c.parent_id == SourceDocument.id,
        )
        .join(QuoteDocumentRole, QuoteDocumentRole.id == latest_roles.c.row_id)
        .where(
            ItemMembershipDecision.status == MembershipStatus.MATCHED,
            CleanDecision.status == CleanStatus.INCLUDED,
            QuoteDocumentRole.purpose
            == QuoteDocumentPurpose.HISTORICAL_REFERENCE,
        )
        .group_by(ItemMembershipDecision.standard_item_id)
        .subquery(name)
    )


def current_standard_member_counts(
    session: Session,
    standard_item_ids: Iterable[int] | None = None,
) -> dict[int, int]:
    """Count current matched, included rows from historical documents."""

    member_counts = current_standard_member_counts_subquery(
        name="operational_member_counts"
    )
    statement = select(
        member_counts.c.standard_item_id,
        member_counts.c.member_count,
    )
    if standard_item_ids is not None:
        item_ids = tuple(dict.fromkeys(standard_item_ids))
        if not item_ids:
            return {}
        statement = statement.where(
            member_counts.c.standard_item_id.in_(item_ids)
        )
    return {
        standard_item_id: member_count
        for standard_item_id, member_count in session.execute(statement).tuples()
    }


def operational_standard_prices(
    session: Session,
    standard_item_ids: Iterable[int],
) -> dict[int, StandardPriceVersion]:
    """Return only captured prices which are operationally active."""

    return {
        item_id: state.current_price
        for item_id, state in operational_standard_price_states(
            session,
            standard_item_ids,
        ).items()
        if state.current_price is not None
    }


@dataclass(frozen=True)
class OperationalStandardPriceState:
    """Current status and immutable captured-version pointer for one item."""

    status: StandardOperationalStatus
    current_price: StandardPriceVersion | None
    captured_price: StandardPriceVersion | None


@dataclass(frozen=True)
class OperationalBuildContext:
    """The one build provenance that may provide current projections.

    Price history predates build projections.  The explicit fallback flag is
    therefore true only while the database has no projection history at all,
    or while the builder is assembling the first projection transaction.
    """

    matching_build_run_id: int | None
    allow_latest_price_fallback: bool


def current_operational_build_context(
    session: Session,
) -> OperationalBuildContext:
    """Find the successful build whose full provenance matches current input.

    Once projections exist, a newest price-version ID is not proof that the
    evidence is current.  Only a successful build with matching input,
    calculation, and code fingerprints can make a projection operational.
    """

    has_projection_history = session.scalar(
        select(StandardDatabaseBuildProjection.id).limit(1)
    )
    if has_projection_history is None:
        return OperationalBuildContext(
            matching_build_run_id=None,
            allow_latest_price_fallback=True,
        )

    # Kept as local imports to avoid a module cycle: build service emits its
    # projections through this module after it has calculated current prices.
    from app.pricing.service import CALCULATION_VERSION
    from app.standard_database.fingerprint import (
        standard_build_calculation_fingerprint,
        standard_build_code_fingerprint,
        standard_build_fingerprint,
    )
    from app.standard_database.service import (
        NORMALIZATION_VERSION,
        RULE_VERSION,
        _load_historical_rows,
    )

    with session.no_autoflush:
        evidence_rows, _ = _load_historical_rows(session)
    input_fingerprint = standard_build_fingerprint(evidence_rows)
    calculation_fingerprint = standard_build_calculation_fingerprint(
        rule_version=RULE_VERSION,
        normalization_version=NORMALIZATION_VERSION,
        calculation_version=CALCULATION_VERSION,
    )
    code_fingerprint = standard_build_code_fingerprint()
    run_id = session.scalar(
        select(StandardDatabaseBuildRun.id)
        .join(
            StandardDatabaseBuildProjection,
            StandardDatabaseBuildProjection.build_run_id
            == StandardDatabaseBuildRun.id,
        )
        .where(
            StandardDatabaseBuildRun.status == StandardBuildStatus.SUCCEEDED,
            StandardDatabaseBuildRun.input_fingerprint == input_fingerprint,
            StandardDatabaseBuildRun.calculation_fingerprint
            == calculation_fingerprint,
            StandardDatabaseBuildRun.code_fingerprint == code_fingerprint,
        )
        .order_by(
            StandardDatabaseBuildRun.finished_at.desc(),
            StandardDatabaseBuildRun.id.desc(),
        )
        .limit(1)
    )
    return OperationalBuildContext(
        matching_build_run_id=run_id,
        allow_latest_price_fallback=False,
    )


def _prices_by_id(
    session: Session,
    price_ids: Iterable[int],
) -> dict[int, StandardPriceVersion]:
    ids = tuple(dict.fromkeys(price_ids))
    if not ids:
        return {}
    return {
        price.id: price
        for price in session.scalars(
            select(StandardPriceVersion)
            .where(StandardPriceVersion.id.in_(ids))
            .options(selectinload(StandardPriceVersion.observations))
        )
    }


def _matching_projection_states(
    session: Session,
    *,
    build_run_id: int,
    item_ids: list[int],
) -> dict[int, OperationalStandardPriceState]:
    projections = list(
        session.scalars(
            select(StandardDatabaseBuildProjection).where(
                StandardDatabaseBuildProjection.build_run_id == build_run_id,
                StandardDatabaseBuildProjection.standard_item_id.in_(item_ids),
            )
        )
    )
    prices = _prices_by_id(
        session,
        (
            projection.standard_price_version_id
            for projection in projections
            if projection.standard_price_version_id is not None
        ),
    )
    states: dict[int, OperationalStandardPriceState] = {}
    for projection in projections:
        captured = (
            None
            if projection.standard_price_version_id is None
            else prices.get(projection.standard_price_version_id)
        )
        states[projection.standard_item_id] = OperationalStandardPriceState(
            status=projection.operational_status,
            current_price=(
                captured
                if projection.operational_status
                == StandardOperationalStatus.ACTIVE
                else None
            ),
            captured_price=captured,
        )
    return states


def _last_captured_projection_prices(
    session: Session,
    item_ids: list[int],
) -> dict[int, StandardPriceVersion]:
    """Return stale pointers from the most recently completed projections.

    These pointers are never returned as current evidence; they preserve an
    audit link while a new build is required.
    """

    rows = session.execute(
        select(
            StandardDatabaseBuildProjection.standard_item_id,
            StandardDatabaseBuildProjection.standard_price_version_id,
        )
        .join(
            StandardDatabaseBuildRun,
            StandardDatabaseBuildRun.id
            == StandardDatabaseBuildProjection.build_run_id,
        )
        .where(
            StandardDatabaseBuildProjection.standard_item_id.in_(item_ids),
            StandardDatabaseBuildRun.status == StandardBuildStatus.SUCCEEDED,
        )
        .order_by(
            StandardDatabaseBuildRun.finished_at.desc(),
            StandardDatabaseBuildRun.id.desc(),
            StandardDatabaseBuildProjection.id.desc(),
        )
    )
    pointers: dict[int, int] = {}
    for item_id, price_id in rows:
        if item_id not in pointers and price_id is not None:
            pointers[item_id] = price_id
    prices = _prices_by_id(session, pointers.values())
    return {
        item_id: prices[price_id]
        for item_id, price_id in pointers.items()
        if price_id in prices
    }


def operational_standard_price_states(
    session: Session,
    standard_item_ids: Iterable[int],
    *,
    context: OperationalBuildContext | None = None,
) -> dict[int, OperationalStandardPriceState]:
    """Classify every requested item without treating stale evidence as zero."""

    item_ids = list(dict.fromkeys(standard_item_ids))
    if not item_ids:
        return {}
    context = context or current_operational_build_context(session)
    if context.matching_build_run_id is not None:
        states = _matching_projection_states(
            session,
            build_run_id=context.matching_build_run_id,
            item_ids=item_ids,
        )
        # A matching provenance should have projected every current standard.
        # Treat an incomplete projection as stale rather than choosing a price
        # from another run or the latest price-version ID.
        for item_id in item_ids:
            states.setdefault(
                item_id,
                OperationalStandardPriceState(
                    status=StandardOperationalStatus.REBUILD_REQUIRED,
                    current_price=None,
                    captured_price=None,
                ),
            )
        return states

    current_drafts = calculate_standard_prices(session, item_ids)
    if not context.allow_latest_price_fallback:
        captured_prices = _last_captured_projection_prices(session, item_ids)
        return {
            item_id: OperationalStandardPriceState(
                status=(
                    StandardOperationalStatus.NO_ELIGIBLE_EVIDENCE
                    if item_id not in current_drafts
                    else StandardOperationalStatus.REBUILD_REQUIRED
                ),
                current_price=None,
                captured_price=captured_prices.get(item_id),
            )
            for item_id in item_ids
        }

    latest_prices = _latest(
        StandardPriceVersion.standard_item_id,
        StandardPriceVersion.id,
        name="operational_latest_price",
    )
    prices = list(
        session.scalars(
            select(StandardPriceVersion)
            .join(
                latest_prices,
                latest_prices.c.row_id == StandardPriceVersion.id,
            )
            .where(StandardPriceVersion.standard_item_id.in_(item_ids))
            .options(selectinload(StandardPriceVersion.observations))
        )
    )
    prices_by_item = {price.standard_item_id: price for price in prices}
    states: dict[int, OperationalStandardPriceState] = {}
    for item_id in item_ids:
        draft = current_drafts.get(item_id)
        price = prices_by_item.get(item_id)
        if draft is None:
            states[item_id] = OperationalStandardPriceState(
                status=StandardOperationalStatus.NO_ELIGIBLE_EVIDENCE,
                current_price=None,
                captured_price=price,
            )
            continue
        current_ids = {row.raw_item_id for row in draft.observations}
        captured_ids = (
            set()
            if price is None
            else {row.raw_item_id for row in price.observations}
        )
        if (
            price is not None
            and price.observation_count == len(current_ids) == len(captured_ids)
            and captured_ids == current_ids
            and price_version_matches_draft(price, draft)
        ):
            states[item_id] = OperationalStandardPriceState(
                status=StandardOperationalStatus.ACTIVE,
                current_price=price,
                captured_price=price,
            )
            continue
        states[item_id] = OperationalStandardPriceState(
            status=StandardOperationalStatus.REBUILD_REQUIRED,
            current_price=None,
            captured_price=price,
        )
    return states
