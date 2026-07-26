"""Current operational projections over immutable standard evidence."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.catalog.models import (
    ItemMembershipDecision,
    MembershipStatus,
    StandardPriceVersion,
)
from app.cleansing.models import CleanDecision, CleanStatus
from app.documents.models import SourceDocument, SourceVariant
from app.pricing.service import calculate_standard_prices
from app.quotes.models import RawQuoteItem
from app.standard_database.models import (
    QuoteDocumentPurpose,
    QuoteDocumentRole,
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


def current_standard_member_counts(
    session: Session,
    standard_item_ids: Iterable[int] | None = None,
) -> dict[int, int]:
    """Count current matched, included rows from historical documents."""

    latest_memberships = _latest(
        ItemMembershipDecision.raw_item_id,
        ItemMembershipDecision.id,
        name="operational_latest_membership",
    )
    latest_cleans = _latest(
        CleanDecision.raw_item_id,
        CleanDecision.id,
        name="operational_latest_clean",
    )
    latest_roles = _latest(
        QuoteDocumentRole.document_id,
        QuoteDocumentRole.id,
        name="operational_latest_role",
    )
    statement = (
        select(
            ItemMembershipDecision.standard_item_id,
            func.count(ItemMembershipDecision.id),
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
    )
    if standard_item_ids is not None:
        item_ids = tuple(dict.fromkeys(standard_item_ids))
        if not item_ids:
            return {}
        statement = statement.where(
            ItemMembershipDecision.standard_item_id.in_(item_ids)
        )
    return {
        standard_item_id: member_count
        for standard_item_id, member_count in session.execute(statement).tuples()
    }


def operational_standard_prices(
    session: Session,
    standard_item_ids: Iterable[int],
) -> dict[int, StandardPriceVersion]:
    """Return latest prices whose evidence set exactly matches current input."""

    item_ids = list(dict.fromkeys(standard_item_ids))
    if not item_ids:
        return {}
    current_drafts = calculate_standard_prices(session, item_ids)
    if not current_drafts:
        return {}
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
    operational: dict[int, StandardPriceVersion] = {}
    for price in prices:
        draft = current_drafts.get(price.standard_item_id)
        if draft is None:
            continue
        current_ids = {row.raw_item_id for row in draft.observations}
        captured_ids = {row.raw_item_id for row in price.observations}
        if (
            price.observation_count == len(current_ids)
            == len(captured_ids)
            and captured_ids == current_ids
        ):
            operational[price.standard_item_id] = price
    return operational
