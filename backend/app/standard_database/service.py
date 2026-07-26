"""Build the local standard database from current historical quote evidence."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.catalog.models import (
    DocumentMetadataVersion,
    MembershipStatus,
    StandardItemVersion,
)
from app.catalog.service import (
    append_membership_decision,
    append_standard_item_version,
    create_standard_item,
    current_membership,
)
from app.cleansing.models import CleanDecision, CleanStatus
from app.db.time import utc_now
from app.db.types import EXACT_DECIMAL_MAX
from app.documents.models import SourceDocument, SourceVariant
from app.matching.normalization import normalize_search_text
from app.pricing.service import (
    CALCULATION_VERSION,
    approve_standard_price,
    calculate_standard_price,
    current_standard_price_version,
)
from app.quotes.models import RawQuoteItem
from app.standard_database.models import (
    QuoteDocumentPurpose,
    QuoteDocumentRole,
    StandardBuildStatus,
    StandardDatabaseBuildRun,
)


RULE_VERSION = "STANDARD_DB_EXACT_V1"
NORMALIZATION_VERSION = "match-v1"
BUILD_ACTOR = "LOCAL_STANDARD_DB_BUILD"


@dataclass(frozen=True)
class EligibleHistoricalRow:
    raw_item_id: int
    clean_decision_id: int
    document_role_id: int
    source_document_id: int
    source_document_name: str
    source_variant_id: int
    source_variant_path: str
    source_variant_sha256: str
    source_sheet: str | None
    source_page: int | None
    source_row: int | None
    normalized_name: str
    normalized_spec: str
    normalized_unit: str
    maker: str | None
    unit_price: Decimal
    metadata_version_id: int | None
    supplier_name: str | None
    quote_date: date | None


@dataclass(frozen=True)
class StandardDatabaseBuildIssue:
    code: str
    detail: str
    raw_item_id: int | None = None
    evidence: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class StandardDatabaseBuildResult:
    run_id: int
    standard_item_count: int
    observation_count: int
    single_observation_count: int
    created_count: int
    reused_count: int
    changed_count: int
    unit_conflict_count: int
    exclusions: tuple[StandardDatabaseBuildIssue, ...]
    conflicts: tuple[StandardDatabaseBuildIssue, ...]


def _latest_id(parent_column, id_column):
    return (
        select(parent_column.label("parent_id"), func.max(id_column).label("id"))
        .group_by(parent_column)
        .subquery()
    )


def _safe_positive_price(value: Decimal | None) -> bool:
    return (
        value is not None
        and value.is_finite()
        and value > 0
        and value <= EXACT_DECIMAL_MAX
        and value.as_tuple().exponent >= -6
    )


def _load_historical_rows(
    session: Session,
) -> tuple[
    tuple[EligibleHistoricalRow, ...],
    tuple[StandardDatabaseBuildIssue, ...],
]:
    latest_role = _latest_id(
        QuoteDocumentRole.document_id,
        QuoteDocumentRole.id,
    )
    latest_clean = _latest_id(CleanDecision.raw_item_id, CleanDecision.id)
    latest_metadata = _latest_id(
        DocumentMetadataVersion.source_document_id,
        DocumentMetadataVersion.id,
    )
    statement = (
        select(
            RawQuoteItem,
            CleanDecision,
            QuoteDocumentRole,
            SourceVariant,
            SourceDocument,
            DocumentMetadataVersion,
        )
        .join(
            latest_clean,
            latest_clean.c.parent_id == RawQuoteItem.id,
        )
        .join(CleanDecision, CleanDecision.id == latest_clean.c.id)
        .join(SourceVariant, SourceVariant.id == RawQuoteItem.source_variant_id)
        .join(SourceDocument, SourceDocument.id == SourceVariant.document_id)
        .join(
            latest_role,
            latest_role.c.parent_id == SourceDocument.id,
        )
        .join(QuoteDocumentRole, QuoteDocumentRole.id == latest_role.c.id)
        .outerjoin(
            latest_metadata,
            latest_metadata.c.parent_id == SourceDocument.id,
        )
        .outerjoin(
            DocumentMetadataVersion,
            DocumentMetadataVersion.id == latest_metadata.c.id,
        )
        .where(CleanDecision.status == CleanStatus.INCLUDED)
        .where(
            QuoteDocumentRole.purpose
            == QuoteDocumentPurpose.HISTORICAL_REFERENCE
        )
        .order_by(RawQuoteItem.id)
    )
    rows: list[EligibleHistoricalRow] = []
    exclusions: list[StandardDatabaseBuildIssue] = []
    for raw, clean, role, variant, document, metadata in session.execute(
        statement
    ):
        if not _safe_positive_price(clean.unit_price):
            exclusions.append(
                StandardDatabaseBuildIssue(
                    code="MISSING_OR_INVALID_PRICE",
                    detail="current included decision has no valid unit price",
                    raw_item_id=raw.id,
                    evidence=(("clean_decision_id", str(clean.id)),),
                )
            )
        evidence_price = (
            clean.unit_price
            if clean.unit_price is not None
            else Decimal("NaN")
        )
        rows.append(
            EligibleHistoricalRow(
                raw_item_id=raw.id,
                clean_decision_id=clean.id,
                document_role_id=role.id,
                source_document_id=document.id,
                source_document_name=document.logical_name,
                source_variant_id=variant.id,
                source_variant_path=variant.path,
                source_variant_sha256=variant.sha256,
                source_sheet=raw.source_sheet,
                source_page=raw.source_page,
                source_row=raw.source_row,
                normalized_name=normalize_search_text(
                    clean.item_name_norm
                ),
                normalized_spec=normalize_search_text(clean.spec_norm),
                normalized_unit=normalize_search_text(clean.unit_norm),
                maker=(
                    normalize_search_text(clean.maker_norm) or None
                ),
                unit_price=evidence_price,
                metadata_version_id=(
                    None if metadata is None else metadata.id
                ),
                supplier_name=(
                    None if metadata is None else metadata.supplier_name
                ),
                quote_date=None if metadata is None else metadata.quote_date,
            )
        )
    return tuple(rows), tuple(exclusions)


def eligible_historical_rows(
    session: Session,
) -> tuple[EligibleHistoricalRow, ...]:
    """Return only current INCLUDED rows from current historical documents."""

    with session.no_autoflush:
        rows, _ = _load_historical_rows(session)
    return rows


def _group_key(row: EligibleHistoricalRow) -> tuple[str, str, str]:
    return (
        row.normalized_name,
        row.normalized_spec,
        row.normalized_unit,
    )


def _aliases(version: StandardItemVersion) -> list[str]:
    try:
        value = json.loads(version.aliases_json)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(value, list):
        return []
    return [alias for alias in value if isinstance(alias, str)]


def _current_versions_by_key(
    session: Session,
) -> dict[tuple[str, str, str], list[StandardItemVersion]]:
    latest = (
        select(
            StandardItemVersion.standard_item_id,
            func.max(StandardItemVersion.id).label("id"),
        )
        .group_by(StandardItemVersion.standard_item_id)
        .subquery()
    )
    result: dict[
        tuple[str, str, str], list[StandardItemVersion]
    ] = defaultdict(list)
    for version in session.scalars(
        select(StandardItemVersion)
        .join(latest, latest.c.id == StandardItemVersion.id)
        .order_by(StandardItemVersion.standard_item_id)
    ):
        key = (
            normalize_search_text(version.canonical_name),
            normalize_search_text(version.canonical_spec),
            normalize_search_text(version.canonical_unit),
        )
        result[key].append(version)
    return dict(result)


def _unit_conflicts(
    groups: dict[tuple[str, str, str], list[EligibleHistoricalRow]],
) -> list[StandardDatabaseBuildIssue]:
    units_by_identity: dict[tuple[str, str], set[str]] = defaultdict(set)
    for name, spec, unit in groups:
        units_by_identity[(name, spec)].add(unit)
    return [
        StandardDatabaseBuildIssue(
            code="UNIT_CONFLICT",
            detail="same normalized name/spec has multiple normalized units",
            evidence=(
                ("normalized_name", name),
                ("normalized_spec", spec),
                ("normalized_units", ",".join(sorted(units))),
            ),
        )
        for (name, spec), units in sorted(units_by_identity.items())
        if len(units) > 1
    ]


def _counts_payload(result: StandardDatabaseBuildResult) -> dict[str, object]:
    return {
        "standard_item_count": result.standard_item_count,
        "observation_count": result.observation_count,
        "single_observation_count": result.single_observation_count,
        "created_count": result.created_count,
        "reused_count": result.reused_count,
        "changed_count": result.changed_count,
        "unit_conflict_count": result.unit_conflict_count,
        "exclusions": [asdict(issue) for issue in result.exclusions],
        "conflicts": [asdict(issue) for issue in result.conflicts],
        "normalization_version": NORMALIZATION_VERSION,
        "calculation_version": CALCULATION_VERSION,
    }


def build_standard_database(
    session: Session,
) -> StandardDatabaseBuildResult:
    """Append standards, memberships, and captured prices in caller scope."""

    from app.standard_database.fingerprint import standard_build_fingerprint

    with session.no_autoflush:
        evidence_rows, initial_exclusions = _load_historical_rows(session)
    fingerprint = standard_build_fingerprint(evidence_rows)
    run = StandardDatabaseBuildRun(
        input_fingerprint=fingerprint,
        rule_version=RULE_VERSION,
    )
    session.add(run)
    session.flush()

    exclusions = list(initial_exclusions)
    groups: dict[
        tuple[str, str, str], list[EligibleHistoricalRow]
    ] = defaultdict(list)
    for row in evidence_rows:
        if not _safe_positive_price(row.unit_price):
            continue
        if not row.normalized_name:
            exclusions.append(
                StandardDatabaseBuildIssue(
                    code="EMPTY_NORMALIZED_NAME",
                    detail="current included decision has an empty name",
                    raw_item_id=row.raw_item_id,
                    evidence=(
                        ("clean_decision_id", str(row.clean_decision_id)),
                    ),
                )
            )
            continue
        groups[_group_key(row)].append(row)

    conflicts = _unit_conflicts(groups)
    current_versions = _current_versions_by_key(session)
    created_count = 0
    reused_count = 0
    changed_count = 0
    standard_item_count = 0
    observation_count = 0
    single_observation_count = 0

    try:
        for key in sorted(groups):
            name, spec, unit = key
            versions = current_versions.get(key, [])
            if len(versions) > 1:
                conflicts.append(
                    StandardDatabaseBuildIssue(
                        code="DUPLICATE_STANDARD_KEY",
                        detail=(
                            "multiple current standard items share the exact "
                            "normalized key"
                        ),
                        evidence=(
                            (
                                "standard_item_ids",
                                ",".join(
                                    str(row.standard_item_id)
                                    for row in versions
                                ),
                            ),
                        ),
                    )
                )
                continue
            desired = (name, spec or None, unit or None)
            if versions:
                version = versions[0]
                item = version.standard_item
                reused_count += 1
                current_canonical = (
                    version.canonical_name,
                    version.canonical_spec,
                    version.canonical_unit,
                )
                if current_canonical != desired:
                    version = append_standard_item_version(
                        session,
                        standard_item_id=version.standard_item_id,
                        expected_current_version_id=version.id,
                        canonical_name=desired[0],
                        canonical_spec=desired[1],
                        canonical_unit=desired[2],
                        aliases=_aliases(version),
                        created_by=BUILD_ACTOR,
                        reason_detail=RULE_VERSION,
                    )
                    changed_count += 1
            else:
                item, version = create_standard_item(
                    session,
                    canonical_name=desired[0],
                    canonical_spec=desired[1],
                    canonical_unit=desired[2],
                    aliases=[],
                    created_by=BUILD_ACTOR,
                    reason_detail=RULE_VERSION,
                )
                created_count += 1
                current_versions[key] = [version]

            for row in groups[key]:
                membership = current_membership(session, row.raw_item_id)
                if (
                    membership is not None
                    and membership.status is MembershipStatus.MATCHED
                    and membership.standard_item_id == item.id
                ):
                    continue
                if (
                    membership is not None
                    and membership.status is MembershipStatus.MATCHED
                    and membership.standard_item_id != item.id
                ):
                    conflicts.append(
                        StandardDatabaseBuildIssue(
                            code="MEMBERSHIP_TARGET_CONFLICT",
                            detail=(
                                "current MATCHED membership targets another "
                                "standard item"
                            ),
                            raw_item_id=row.raw_item_id,
                            evidence=(
                                (
                                    "membership_decision_id",
                                    str(membership.id),
                                ),
                                (
                                    "standard_item_id",
                                    str(membership.standard_item_id),
                                ),
                            ),
                        )
                    )
                    continue
                append_membership_decision(
                    session,
                    raw_item_id=row.raw_item_id,
                    standard_item_id=item.id,
                    status=MembershipStatus.MATCHED,
                    expected_current_decision_id=(
                        None if membership is None else membership.id
                    ),
                    candidate_score=None,
                    method=RULE_VERSION,
                    evidence={
                        "build_run_id": run.id,
                        "rule_version": RULE_VERSION,
                        "normalization_version": NORMALIZATION_VERSION,
                    },
                    decided_by=BUILD_ACTOR,
                    reason_detail=RULE_VERSION,
                )

            draft = calculate_standard_price(session, item.id)
            current_price = current_standard_price_version(session, item.id)
            if (
                current_price is None
                or current_price.draft_fingerprint != draft.fingerprint
            ):
                approve_standard_price(
                    session,
                    item.id,
                    expected_fingerprint=draft.fingerprint,
                    expected_current_version_id=(
                        None if current_price is None else current_price.id
                    ),
                    approved_by=BUILD_ACTOR,
                )
            standard_item_count += 1
            observation_count += draft.observation_count
            if draft.observation_count == 1:
                single_observation_count += 1

        result = StandardDatabaseBuildResult(
            run_id=run.id,
            standard_item_count=standard_item_count,
            observation_count=observation_count,
            single_observation_count=single_observation_count,
            created_count=created_count,
            reused_count=reused_count,
            changed_count=changed_count,
            unit_conflict_count=sum(
                issue.code == "UNIT_CONFLICT" for issue in conflicts
            ),
            exclusions=tuple(exclusions),
            conflicts=tuple(conflicts),
        )
        run.status = StandardBuildStatus.SUCCEEDED
        run.counts_json = json.dumps(
            _counts_payload(result),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        run.finished_at = utc_now()
        session.flush()
        return result
    except Exception as error:
        run.status = StandardBuildStatus.FAILED
        run.error_detail = str(error)
        run.finished_at = utc_now()
        session.flush()
        raise
