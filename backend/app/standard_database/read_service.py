"""Read-only projections for the operator-facing standard database."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.catalog.models import (
    DocumentMetadataVersion,
    StandardItemVersion,
    StandardPriceObservation,
    StandardPriceVersion,
)
from app.cleansing.models import CleanDecision
from app.documents.models import SourceDocument, SourceVariant
from app.quotes.models import RawQuoteItem
from app.standard_database.models import (
    StandardBuildStatus,
    StandardDatabaseBuildRun,
)


class EvidenceQuality(StrEnum):
    SINGLE_OBSERVATION = "SINGLE_OBSERVATION"
    MULTI_OBSERVATION = "MULTI_OBSERVATION"


class StandardExplorerNotFound(LookupError):
    pass


@dataclass(frozen=True)
class StandardBuildProvenance:
    build_run_id: int
    status: StandardBuildStatus
    built_at: datetime
    rule_version: str


@dataclass(frozen=True)
class StandardExplorerSummary:
    current_version: StandardItemVersion
    current_price: StandardPriceVersion | None
    supplier_summary: tuple[str, ...]
    maker_summary: tuple[str, ...]
    quote_date_start: date | None
    quote_date_end: date | None
    provenance: StandardBuildProvenance | None

    @property
    def evidence_quality(self) -> EvidenceQuality | None:
        if self.current_price is None:
            return None
        return evidence_quality(self.current_price.observation_count)


@dataclass(frozen=True)
class StandardEvidenceRow:
    raw_item_id: int
    unit_price: Decimal
    supplier_name: str | None
    maker: str | None
    quote_date: date | None
    document_id: int
    logical_name: str
    variant_id: int
    path: str
    sheet: str | None
    page: int | None
    row: int | None
    cells: str | None


def evidence_quality(observation_count: int) -> EvidenceQuality:
    if observation_count == 1:
        return EvidenceQuality.SINGLE_OBSERVATION
    return EvidenceQuality.MULTI_OBSERVATION


def latest_build_provenance(
    session: Session,
) -> StandardBuildProvenance | None:
    run = session.scalar(
        select(StandardDatabaseBuildRun)
        .where(
            StandardDatabaseBuildRun.status
            == StandardBuildStatus.SUCCEEDED
        )
        .order_by(
            StandardDatabaseBuildRun.finished_at.desc(),
            StandardDatabaseBuildRun.id.desc(),
        )
        .limit(1)
    )
    if run is None or run.finished_at is None:
        return None
    return StandardBuildProvenance(
        build_run_id=run.id,
        status=run.status,
        built_at=run.finished_at,
        rule_version=run.rule_version,
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


def list_standard_explorer_items(
    session: Session,
    *,
    after_id: int | None,
    limit: int,
    search: str | None,
    quality: EvidenceQuality | None,
) -> tuple[
    list[StandardExplorerSummary],
    int | None,
    StandardBuildProvenance | None,
]:
    latest_versions = _latest(
        StandardItemVersion.standard_item_id,
        StandardItemVersion.id,
        name="explorer_latest_item_version",
    )
    latest_prices = _latest(
        StandardPriceVersion.standard_item_id,
        StandardPriceVersion.id,
        name="explorer_latest_price_version",
    )
    statement = (
        select(StandardItemVersion, StandardPriceVersion)
        .join(
            latest_versions,
            latest_versions.c.row_id == StandardItemVersion.id,
        )
        .outerjoin(
            latest_prices,
            latest_prices.c.parent_id
            == StandardItemVersion.standard_item_id,
        )
        .outerjoin(
            StandardPriceVersion,
            StandardPriceVersion.id == latest_prices.c.row_id,
        )
        .order_by(StandardItemVersion.standard_item_id)
        .limit(limit + 1)
    )
    if after_id is not None:
        statement = statement.where(
            StandardItemVersion.standard_item_id > after_id
        )
    if search and (needle := search.strip()):
        pattern = f"%{needle}%"
        statement = statement.where(
            or_(
                StandardItemVersion.canonical_name.ilike(pattern),
                StandardItemVersion.canonical_spec.ilike(pattern),
                StandardItemVersion.canonical_unit.ilike(pattern),
            )
        )
    if quality is EvidenceQuality.SINGLE_OBSERVATION:
        statement = statement.where(
            StandardPriceVersion.observation_count == 1
        )
    elif quality is EvidenceQuality.MULTI_OBSERVATION:
        statement = statement.where(
            StandardPriceVersion.observation_count > 1
        )

    page = list(session.execute(statement).tuples())
    has_more = len(page) > limit
    page = page[:limit]
    if not page:
        return [], None, latest_build_provenance(session)

    price_ids = [price.id for _, price in page if price is not None]
    suppliers: dict[int, set[str]] = defaultdict(set)
    makers: dict[int, set[str]] = defaultdict(set)
    dates: dict[int, list[date]] = defaultdict(list)
    evidence_statement = (
        select(
            StandardPriceObservation.standard_price_version_id,
            DocumentMetadataVersion.supplier_name,
            DocumentMetadataVersion.quote_date,
            CleanDecision.maker_norm,
        )
        .outerjoin(
            DocumentMetadataVersion,
            DocumentMetadataVersion.id
            == StandardPriceObservation.metadata_version_id,
        )
        .join(
            CleanDecision,
            CleanDecision.id == StandardPriceObservation.clean_decision_id,
        )
        .where(
            StandardPriceObservation.standard_price_version_id.in_(price_ids)
        )
    )
    if price_ids:
        for price_id, supplier, quote_date, maker in session.execute(
            evidence_statement
        ):
            if supplier:
                suppliers[price_id].add(supplier)
            if maker:
                makers[price_id].add(maker)
            if quote_date:
                dates[price_id].append(quote_date)

    provenance = latest_build_provenance(session)
    summaries = [
        StandardExplorerSummary(
            current_version=version,
            current_price=price,
            supplier_summary=(
                () if price is None else tuple(sorted(suppliers[price.id]))
            ),
            maker_summary=(
                () if price is None else tuple(sorted(makers[price.id]))
            ),
            quote_date_start=(
                None
                if price is None or not dates[price.id]
                else min(dates[price.id])
            ),
            quote_date_end=(
                None
                if price is None or not dates[price.id]
                else max(dates[price.id])
            ),
            provenance=provenance,
        )
        for version, price in page
    ]
    next_cursor = (
        summaries[-1].current_version.standard_item_id if has_more else None
    )
    return summaries, next_cursor, provenance


def standard_item_evidence(
    session: Session,
    standard_item_id: int,
    *,
    after_id: int | None,
    limit: int,
) -> tuple[
    StandardPriceVersion,
    list[StandardEvidenceRow],
    int | None,
    StandardBuildProvenance | None,
]:
    price = session.scalar(
        select(StandardPriceVersion)
        .where(StandardPriceVersion.standard_item_id == standard_item_id)
        .order_by(StandardPriceVersion.id.desc())
        .limit(1)
    )
    if price is None:
        raise StandardExplorerNotFound("standard item price not found")

    statement = (
        select(
            StandardPriceObservation.raw_item_id,
            CleanDecision.unit_price,
            DocumentMetadataVersion.supplier_name,
            CleanDecision.maker_norm,
            DocumentMetadataVersion.quote_date,
            SourceDocument.id,
            SourceDocument.logical_name,
            SourceVariant.id,
            SourceVariant.path,
            RawQuoteItem.source_sheet,
            RawQuoteItem.source_page,
            RawQuoteItem.source_row,
            RawQuoteItem.source_cells,
        )
        .join(
            CleanDecision,
            CleanDecision.id == StandardPriceObservation.clean_decision_id,
        )
        .join(
            RawQuoteItem,
            RawQuoteItem.id == StandardPriceObservation.raw_item_id,
        )
        .join(
            SourceVariant,
            SourceVariant.id == RawQuoteItem.source_variant_id,
        )
        .join(
            SourceDocument,
            SourceDocument.id == SourceVariant.document_id,
        )
        .outerjoin(
            DocumentMetadataVersion,
            DocumentMetadataVersion.id
            == StandardPriceObservation.metadata_version_id,
        )
        .where(
            StandardPriceObservation.standard_price_version_id == price.id
        )
        .order_by(StandardPriceObservation.raw_item_id)
        .limit(limit + 1)
    )
    if after_id is not None:
        statement = statement.where(
            StandardPriceObservation.raw_item_id > after_id
        )
    result = list(session.execute(statement).tuples())
    has_more = len(result) > limit
    result = result[:limit]
    rows = [
        StandardEvidenceRow(
            raw_item_id=row[0],
            unit_price=row[1],
            supplier_name=row[2],
            maker=row[3],
            quote_date=row[4],
            document_id=row[5],
            logical_name=row[6],
            variant_id=row[7],
            path=row[8],
            sheet=row[9],
            page=row[10],
            row=row[11],
            cells=row[12],
        )
        for row in result
    ]
    next_cursor = rows[-1].raw_item_id if has_more else None
    return price, rows, next_cursor, latest_build_provenance(session)
