"""Read-only projections for the operator-facing standard database."""

from __future__ import annotations

import json
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
    StandardOperationalStatus,
)
from app.standard_database.operational import (
    current_standard_member_counts_subquery,
    operational_standard_price_states,
)

EXPLORER_SCAN_CHUNK_SIZE = 128


class EvidenceQuality(StrEnum):
    SINGLE_OBSERVATION = "SINGLE_OBSERVATION"
    MULTI_OBSERVATION = "MULTI_OBSERVATION"


class QuoteDateQuality(StrEnum):
    CONFIRMED = "CONFIRMED"
    REFERENCE_BACKFILL = "REFERENCE_BACKFILL"
    FILE_DATE_INFERRED = "FILE_DATE_INFERRED"


class StandardExplorerNotFound(LookupError):
    pass


@dataclass(frozen=True)
class StandardBuildProvenance:
    build_run_id: int
    status: StandardBuildStatus
    built_at: datetime
    rule_version: str
    input_fingerprint: str
    calculation_fingerprint: str
    code_fingerprint: str


@dataclass(frozen=True)
class StandardExplorerSummary:
    current_version: StandardItemVersion
    current_price: StandardPriceVersion | None
    captured_price_version_id: int | None
    operational_status: StandardOperationalStatus
    member_count: int
    supplier_summary: tuple[str, ...]
    maker_summary: tuple[str, ...]
    quote_date_start: date | None
    quote_date_end: date | None
    quote_date_end_quality: QuoteDateQuality | None
    spec_source_status: str
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
    quote_date_quality: QuoteDateQuality | None
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
        input_fingerprint=run.input_fingerprint,
        calculation_fingerprint=run.calculation_fingerprint,
        code_fingerprint=run.code_fingerprint,
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
    member_counts = current_standard_member_counts_subquery(
        name="explorer_current_member_counts"
    )
    base_statement = (
        select(
            StandardItemVersion,
            func.coalesce(member_counts.c.member_count, 0).label(
                "member_count"
            ),
        )
        .join(
            latest_versions,
            latest_versions.c.row_id == StandardItemVersion.id,
        )
        .outerjoin(
            member_counts,
            member_counts.c.standard_item_id
            == StandardItemVersion.standard_item_id,
        )
    )
    if after_id is not None:
        base_statement = base_statement.where(
            StandardItemVersion.standard_item_id > after_id
        )
    if search and (needle := search.strip()):
        pattern = f"%{needle}%"
        base_statement = base_statement.where(
            or_(
                StandardItemVersion.canonical_name.ilike(pattern),
                StandardItemVersion.canonical_spec.ilike(pattern),
                StandardItemVersion.canonical_unit.ilike(pattern),
            )
        )
    page_candidates: list[
        tuple[
            StandardItemVersion,
            StandardPriceVersion | None,
            int,
            StandardOperationalStatus,
            int | None,
        ]
    ] = []
    scan_after = after_id
    while len(page_candidates) < limit + 1:
        chunk_statement = base_statement
        if scan_after is not None:
            chunk_statement = chunk_statement.where(
                StandardItemVersion.standard_item_id > scan_after
            )
        chunk = list(
            session.execute(
                chunk_statement.order_by(
                    StandardItemVersion.standard_item_id
                ).limit(EXPLORER_SCAN_CHUNK_SIZE)
            ).tuples()
        )
        if not chunk:
            break
        scan_after = chunk[-1][0].standard_item_id
        states = operational_standard_price_states(
            session,
            (version.standard_item_id for version, _ in chunk),
        )
        for version, member_count in chunk:
            state = states[version.standard_item_id]
            price = state.current_price
            if (
                quality is EvidenceQuality.SINGLE_OBSERVATION
                and (
                    price is None
                    or price.observation_count != 1
                )
            ):
                continue
            if (
                quality is EvidenceQuality.MULTI_OBSERVATION
                and (
                    price is None
                    or price.observation_count <= 1
                )
            ):
                continue
            page_candidates.append(
                (
                    version,
                    price,
                    member_count,
                    state.status,
                    (
                        None
                        if state.captured_price is None
                        else state.captured_price.id
                    ),
                )
            )
            if len(page_candidates) == limit + 1:
                break
        if len(chunk) < EXPLORER_SCAN_CHUNK_SIZE:
            break

    has_more = len(page_candidates) > limit
    page = page_candidates[:limit]
    if not page:
        return [], None, latest_build_provenance(session)

    price_ids = [
        price.id for _, price, _, _, _ in page if price is not None
    ]
    suppliers: dict[int, set[str]] = defaultdict(set)
    makers: dict[int, set[str]] = defaultdict(set)
    dates: dict[int, list[tuple[date, QuoteDateQuality]]] = defaultdict(list)
    spec_statuses: dict[int, set[str]] = defaultdict(set)
    evidence_statement = (
        select(
            StandardPriceObservation.standard_price_version_id,
            DocumentMetadataVersion.supplier_name,
            DocumentMetadataVersion.quote_date,
            DocumentMetadataVersion.evidence_json,
            CleanDecision.maker_norm,
            RawQuoteItem.spec_raw,
            RawQuoteItem.parse_warnings_json,
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
        .join(
            RawQuoteItem,
            RawQuoteItem.id == StandardPriceObservation.raw_item_id,
        )
        .where(
            StandardPriceObservation.standard_price_version_id.in_(price_ids)
        )
    )
    if price_ids:
        for (
            price_id,
            supplier,
            quote_date,
            metadata_evidence_json,
            maker,
            spec_raw,
            warnings_json,
        ) in session.execute(
            evidence_statement
        ):
            if supplier:
                suppliers[price_id].add(supplier)
            if maker:
                makers[price_id].add(maker)
            if quote_date:
                dates[price_id].append(
                    (quote_date, _quote_date_quality(metadata_evidence_json))
                )
            spec_statuses[price_id].add(
                _spec_source_status(spec_raw, warnings_json)
            )

    provenance = latest_build_provenance(session)
    summaries = [
        StandardExplorerSummary(
            current_version=version,
            current_price=price,
            captured_price_version_id=captured_price_version_id,
            operational_status=operational_status,
            member_count=member_count,
            supplier_summary=(
                () if price is None else tuple(sorted(suppliers[price.id]))
            ),
            maker_summary=(
                () if price is None else tuple(sorted(makers[price.id]))
            ),
            quote_date_start=(
                None
                if price is None or not dates[price.id]
                else min(row[0] for row in dates[price.id])
            ),
            quote_date_end=(
                None
                if price is None or not dates[price.id]
                else max(row[0] for row in dates[price.id])
            ),
            quote_date_end_quality=(
                None
                if price is None or not dates[price.id]
                else _latest_quote_date_quality(dates[price.id])
            ),
            spec_source_status=(
                "UNKNOWN"
                if price is None
                else _aggregate_spec_status(spec_statuses[price.id])
            ),
            provenance=provenance,
        )
        for (
            version,
            price,
            member_count,
            operational_status,
            captured_price_version_id,
        ) in page
    ]
    next_cursor = (
        summaries[-1].current_version.standard_item_id if has_more else None
    )
    return summaries, next_cursor, provenance


def _spec_source_status(spec_raw: str | None, warnings_json: str) -> str:
    if spec_raw is not None and spec_raw.strip():
        return "PRESENT"
    try:
        warnings = json.loads(warnings_json)
    except (json.JSONDecodeError, TypeError):
        warnings = []
    if "SOURCE_SPEC_BLANK" in warnings:
        return "SOURCE_BLANK"
    if (
        "SPEC_COLUMN_NOT_FOUND" in warnings
        or "FALLBACK_FIXED_C_E_F_H" in warnings
    ):
        return "PARSER_UNMAPPED"
    return "UNKNOWN"


def _aggregate_spec_status(statuses: set[str]) -> str:
    if not statuses:
        return "UNKNOWN"
    if len(statuses) == 1:
        return next(iter(statuses))
    if "PARSER_UNMAPPED" in statuses:
        return "MIXED_REVIEW_REQUIRED"
    if statuses <= {"PRESENT", "SOURCE_BLANK"}:
        return "MIXED_SOURCE_VALUES"
    return "UNKNOWN"


def _quote_date_quality(evidence_json: str | None) -> QuoteDateQuality:
    try:
        evidence = json.loads(evidence_json or "{}")
    except (json.JSONDecodeError, TypeError):
        evidence = {}
    quote_date = evidence.get("quote_date") if isinstance(evidence, dict) else None
    quality = quote_date.get("quality") if isinstance(quote_date, dict) else None
    if quality == QuoteDateQuality.FILE_DATE_INFERRED.value:
        return QuoteDateQuality.FILE_DATE_INFERRED
    if quality == QuoteDateQuality.REFERENCE_BACKFILL.value:
        return QuoteDateQuality.REFERENCE_BACKFILL
    return QuoteDateQuality.CONFIRMED


def _evidence_cells(
    raw_cells: str | None,
    reason_evidence_json: str | None,
) -> str | None:
    """Prefer a later, evidence-backed source range over the parser's old range."""
    if not reason_evidence_json:
        return raw_cells
    try:
        recovered_cells = json.loads(reason_evidence_json).get("cells")
    except (json.JSONDecodeError, AttributeError):
        return raw_cells
    if isinstance(recovered_cells, str) and recovered_cells.strip():
        return recovered_cells.strip()
    return raw_cells


def _latest_quote_date_quality(
    dates: list[tuple[date, QuoteDateQuality]],
) -> QuoteDateQuality:
    latest = max(row[0] for row in dates)
    qualities = {quality for value, quality in dates if value == latest}
    for quality in (
        QuoteDateQuality.CONFIRMED,
        QuoteDateQuality.REFERENCE_BACKFILL,
        QuoteDateQuality.FILE_DATE_INFERRED,
    ):
        if quality in qualities:
            return quality
    return QuoteDateQuality.CONFIRMED


def standard_item_evidence(
    session: Session,
    standard_item_id: int,
    *,
    price_version_id: int,
    after_id: int | None,
    limit: int,
) -> tuple[
    StandardPriceVersion,
    list[StandardEvidenceRow],
    int | None,
    StandardBuildProvenance | None,
]:
    price = session.get(StandardPriceVersion, price_version_id)
    if price is None or price.standard_item_id != standard_item_id:
        raise StandardExplorerNotFound("standard item price not found")

    statement = (
        select(
            StandardPriceObservation.raw_item_id,
            CleanDecision.unit_price,
            DocumentMetadataVersion.supplier_name,
            CleanDecision.maker_norm,
            DocumentMetadataVersion.quote_date,
            DocumentMetadataVersion.evidence_json,
            CleanDecision.reason_evidence_json,
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
            quote_date_quality=(
                None if row[4] is None else _quote_date_quality(row[5])
            ),
            document_id=row[7],
            logical_name=row[8],
            variant_id=row[9],
            path=row[10],
            sheet=row[11],
            page=row[12],
            row=row[13],
            cells=_evidence_cells(row[14], row[6]),
        )
        for row in result
    ]
    next_cursor = rows[-1].raw_item_id if has_more else None
    return price, rows, next_cursor, latest_build_provenance(session)
