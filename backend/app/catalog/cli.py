"""Local catalog migration commands with conservative automatic rules."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.catalog.models import (
    ItemMembershipDecision,
    MembershipStatus,
    StandardItem,
    StandardItemVersion,
)
from app.catalog.service import catalog_fingerprint
from app.cleansing.models import CleanDecision, CleanStatus
from app.core.config import Settings, settings as default_settings
from app.documents.models import SourceDocument, SourceVariant
from app.embeddings.base import (
    EmbeddingContractNotConfiguredError,
    EmbeddingUnavailableError,
)
from app.embeddings.hchat import build_embedding_client
from app.embeddings.index import IndexMetadata, save_index
from app.embeddings.mock import DeterministicMockEmbeddingClient
from app.matching.normalization import model_tokens, normalize_search_text
from app.pricing.service import (
    calculate_standard_prices,
)
from app.quotes.models import RawQuoteItem
from app.standard_database.models import (
    QuoteDocumentPurpose,
    QuoteDocumentRole,
)


CATALOG_SEED_RULE = "EXACT_RULE_V1"
CATALOG_SEED_ACTOR = "catalog-seed-rule-v1"
NORMALIZATION_VERSION = "match-v1"


@dataclass(frozen=True)
class CatalogSeedReport:
    included_rows_eligible: int
    exact_groups_eligible: int
    exact_groups_created: int
    catalog_items_created: int
    memberships_created: int
    unmatched_rows: int
    conflicts_held_for_review: int
    rows_held_by_prior_decision: int
    rule_version: str = CATALOG_SEED_RULE
    fuzzy_auto_matches: int = 0
    embedding_auto_matches: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class EmbeddingIndexReport:
    status: str
    model: str | None
    item_count: int
    catalog_fingerprint: str
    index_file: str | None
    network_called: bool
    automatic_approval: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class StandardPriceDraftReport:
    standard_items: int
    drafts_available: int
    drafts_unavailable: int
    observations_available: int
    groups_missing_supplier_metadata: int
    groups_missing_date_metadata: int
    approved_versions_created: int
    drafts: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["drafts"] = list(self.drafts)
        return payload


@dataclass(frozen=True)
class _IncludedRow:
    raw: RawQuoteItem
    clean: CleanDecision
    key: tuple[str, str, str]


def _latest_included_rows(session: Session) -> list[_IncludedRow]:
    latest_clean = (
        select(
            CleanDecision.raw_item_id,
            func.max(CleanDecision.id).label("decision_id"),
        )
        .group_by(CleanDecision.raw_item_id)
        .subquery()
    )
    latest_role = (
        select(
            QuoteDocumentRole.document_id,
            func.max(QuoteDocumentRole.id).label("role_id"),
        )
        .group_by(QuoteDocumentRole.document_id)
        .subquery()
    )
    rows = session.execute(
        select(RawQuoteItem, CleanDecision)
        .join(
            latest_clean,
            latest_clean.c.raw_item_id == RawQuoteItem.id,
        )
        .join(
            CleanDecision,
            CleanDecision.id == latest_clean.c.decision_id,
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
        .where(CleanDecision.status == CleanStatus.INCLUDED)
        .where(
            QuoteDocumentRole.purpose
            == QuoteDocumentPurpose.HISTORICAL_REFERENCE
        )
        .order_by(RawQuoteItem.id)
    ).all()
    result: list[_IncludedRow] = []
    for raw, clean in rows:
        name = normalize_search_text(
            clean.item_name_norm or raw.item_name_raw
        )
        if not name:
            continue
        key = (
            name,
            normalize_search_text(clean.spec_norm or raw.spec_raw),
            normalize_search_text(clean.unit_norm or raw.unit_raw),
        )
        result.append(_IncludedRow(raw=raw, clean=clean, key=key))
    return result


def _current_versions(session: Session) -> list[StandardItemVersion]:
    latest = (
        select(
            StandardItemVersion.standard_item_id,
            func.max(StandardItemVersion.id).label("version_id"),
        )
        .group_by(StandardItemVersion.standard_item_id)
        .subquery()
    )
    return list(
        session.scalars(
            select(StandardItemVersion)
            .join(latest, latest.c.version_id == StandardItemVersion.id)
            .order_by(StandardItemVersion.standard_item_id)
        )
    )


def _version_key(
    version: StandardItemVersion,
) -> tuple[str, str, str]:
    return (
        normalize_search_text(version.canonical_name),
        normalize_search_text(version.canonical_spec),
        normalize_search_text(version.canonical_unit),
    )


def _group_has_conflict(rows: list[_IncludedRow]) -> bool:
    units = {row.key[2] for row in rows if row.key[2]}
    if len(units) > 1:
        return True
    token_sets = {
        model_tokens(f"{row.key[0]} {row.key[1]}") for row in rows
    }
    explicit = [set(tokens) for tokens in token_sets if tokens]
    if len(explicit) < 2:
        return False
    first = explicit[0]
    return any(
        first.isdisjoint(other) and first != other for other in explicit[1:]
    )


def _membership_row_ids(session: Session) -> set[int]:
    return set(
        session.scalars(
            select(ItemMembershipDecision.raw_item_id).distinct()
        )
    )


def _matched_row_ids(session: Session) -> set[int]:
    latest = (
        select(
            ItemMembershipDecision.raw_item_id,
            func.max(ItemMembershipDecision.id).label("decision_id"),
        )
        .group_by(ItemMembershipDecision.raw_item_id)
        .subquery()
    )
    return set(
        session.scalars(
            select(ItemMembershipDecision.raw_item_id)
            .join(
                latest,
                latest.c.decision_id == ItemMembershipDecision.id,
            )
            .where(
                ItemMembershipDecision.status == MembershipStatus.MATCHED
            )
        )
    )


def seed_exact_catalog(session: Session) -> CatalogSeedReport:
    """Create only deterministic exact groups; never use fuzzy/semantic scores."""

    included = _latest_included_rows(session)
    groups: dict[tuple[str, str, str], list[_IncludedRow]] = {}
    for row in included:
        groups.setdefault(row.key, []).append(row)
    units_by_name_spec: dict[tuple[str, str], set[str]] = {}
    for row in included:
        if row.key[2]:
            units_by_name_spec.setdefault(
                (row.key[0], row.key[1]),
                set(),
            ).add(row.key[2])
    unit_conflict_keys = {
        name_spec
        for name_spec, units in units_by_name_spec.items()
        if len(units) > 1
    }

    existing_by_key: dict[
        tuple[str, str, str], list[StandardItemVersion]
    ] = {}
    for version in _current_versions(session):
        existing_by_key.setdefault(_version_key(version), []).append(version)

    prior_decision_ids = _membership_row_ids(session)
    groups_eligible = 0
    groups_created = 0
    items_created = 0
    memberships_created = 0
    conflicts = 0
    rows_held = sum(
        row.raw.id in prior_decision_ids for row in included
    )

    for key, rows in sorted(groups.items()):
        if len(rows) < 2:
            continue
        groups_eligible += 1
        targets = existing_by_key.get(key, [])
        new_rows = [
            row for row in rows if row.raw.id not in prior_decision_ids
        ]
        if (
            key[:2] in unit_conflict_keys
            or _group_has_conflict(rows)
            or len(targets) > 1
        ):
            conflicts += len(new_rows)
            continue

        if targets:
            version = targets[0]
            item_id = version.standard_item_id
        else:
            if any(
                row.raw.id in prior_decision_ids for row in rows
            ):
                conflicts += len(new_rows)
                continue
            if len(new_rows) < 2:
                continue
            item = StandardItem()
            version = StandardItemVersion(
                standard_item=item,
                version_number=1,
                canonical_name=key[0],
                canonical_spec=key[1] or None,
                canonical_unit=key[2] or None,
                aliases_json="[]",
                created_by=CATALOG_SEED_ACTOR,
                change_reason=(
                    "현재 INCLUDED 행 2건 이상의 품명·사양·단위 완전 일치"
                ),
            )
            session.add_all([item, version])
            session.flush()
            existing_by_key[key] = [version]
            item_id = item.id
            groups_created += 1
            items_created += 1

        for row in new_rows:
            evidence = {
                "automatic": True,
                "clean_decision_id": row.clean.id,
                "exact_normalized_key": {
                    "item_name": key[0],
                    "spec": key[1] or None,
                    "unit": key[2] or None,
                },
                "group_size": len(rows),
                "normalization_version": NORMALIZATION_VERSION,
                "rule_version": CATALOG_SEED_RULE,
            }
            session.add(
                ItemMembershipDecision(
                    raw_item_id=row.raw.id,
                    standard_item_id=item_id,
                    status=MembershipStatus.MATCHED,
                    candidate_score=None,
                    method=CATALOG_SEED_RULE,
                    evidence_json=json.dumps(
                        evidence,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    decided_by=CATALOG_SEED_ACTOR,
                )
            )
            memberships_created += 1
            prior_decision_ids.add(row.raw.id)
    session.flush()
    matched = _matched_row_ids(session)
    return CatalogSeedReport(
        included_rows_eligible=len(included),
        exact_groups_eligible=groups_eligible,
        exact_groups_created=groups_created,
        catalog_items_created=items_created,
        memberships_created=memberships_created,
        unmatched_rows=sum(row.raw.id not in matched for row in included),
        conflicts_held_for_review=conflicts,
        rows_held_by_prior_decision=rows_held,
    )


def _catalog_index_rows(
    session: Session,
) -> tuple[list[int], list[str]]:
    versions = _current_versions(session)
    return (
        [version.standard_item_id for version in versions],
        [
            " ".join(
                value
                for value in (
                    version.canonical_name,
                    version.canonical_spec,
                    version.canonical_unit,
                )
                if value
            )
            for version in versions
        ],
    )


def build_catalog_embedding_index(
    session: Session,
    *,
    index_path: Path,
    mock: bool = False,
    settings: Settings = default_settings,
) -> EmbeddingIndexReport:
    """Build a replaceable index without ever creating membership decisions."""

    fingerprint = catalog_fingerprint(session)
    item_ids, texts = _catalog_index_rows(session)
    if mock:
        client = DeterministicMockEmbeddingClient()
        status = "MOCK_ONLY"
        network_called = False
    elif not settings.hchat_embedding_enabled:
        return EmbeddingIndexReport(
            status="DISABLED",
            model=None,
            item_count=len(item_ids),
            catalog_fingerprint=fingerprint,
            index_file=None,
            network_called=False,
        )
    elif settings.hchat_embedding_api_style == "custom":
        return EmbeddingIndexReport(
            status="CONTRACT_NOT_CONFIGURED",
            model=settings.hchat_embedding_model,
            item_count=len(item_ids),
            catalog_fingerprint=fingerprint,
            index_file=None,
            network_called=False,
        )
    else:
        client = build_embedding_client(settings)
        status = "AVAILABLE"
        network_called = bool(texts)

    if not texts:
        return EmbeddingIndexReport(
            status="EMPTY_CATALOG",
            model=getattr(client, "model", None),
            item_count=0,
            catalog_fingerprint=fingerprint,
            index_file=None,
            network_called=False,
        )
    try:
        batch = client.embed(texts)
    except EmbeddingContractNotConfiguredError:
        return EmbeddingIndexReport(
            status="CONTRACT_NOT_CONFIGURED",
            model=settings.hchat_embedding_model,
            item_count=len(item_ids),
            catalog_fingerprint=fingerprint,
            index_file=None,
            network_called=False,
        )
    except EmbeddingUnavailableError:
        return EmbeddingIndexReport(
            status="UNAVAILABLE",
            model=settings.hchat_embedding_model,
            item_count=len(item_ids),
            catalog_fingerprint=fingerprint,
            index_file=None,
            network_called=network_called,
        )
    metadata = IndexMetadata(
        model=batch.model,
        dimension=batch.dimension,
        item_count=len(item_ids),
        catalog_fingerprint=fingerprint,
        normalization_version=NORMALIZATION_VERSION,
        created_at=datetime.now(timezone.utc),
    )
    save_index(
        index_path,
        item_ids=np.asarray(item_ids, dtype=np.int64),
        vectors=batch.vectors,
        metadata=metadata,
    )
    return EmbeddingIndexReport(
        status=status,
        model=batch.model,
        item_count=len(item_ids),
        catalog_fingerprint=fingerprint,
        index_file=Path(index_path).name,
        network_called=network_called,
    )


def report_standard_price_drafts(
    session: Session,
) -> StandardPriceDraftReport:
    """Calculate current drafts without flushing or approving any version."""

    item_ids = list(
        session.scalars(select(StandardItem.id).order_by(StandardItem.id))
    )
    drafts: list[dict[str, object]] = []
    unavailable = 0
    observations = 0
    missing_supplier = 0
    missing_date = 0
    with session.no_autoflush:
        calculated = calculate_standard_prices(session, item_ids)
        for item_id in item_ids:
            draft = calculated.get(item_id)
            if draft is None:
                unavailable += 1
                continue
            observations += draft.observation_count
            supplier_missing = any(
                not row.supplier_name or not row.supplier_name.strip()
                for row in draft.observations
            )
            date_missing = any(
                row.quote_date is None for row in draft.observations
            )
            missing_supplier += supplier_missing
            missing_date += date_missing
            drafts.append(
                {
                    "standard_item_id": item_id,
                    "observation_count": draft.observation_count,
                    "supplier_count": draft.supplier_count,
                    "latest_quote_date": (
                        None
                        if draft.latest_quote_date is None
                        else draft.latest_quote_date.isoformat()
                    ),
                    "minimum_price": str(draft.prices.minimum),
                    "median_price": str(draft.prices.median),
                    "average_price": str(draft.prices.average),
                    "maximum_price": str(draft.prices.maximum),
                    "fingerprint": draft.fingerprint,
                    "missing_supplier_metadata": supplier_missing,
                    "missing_date_metadata": date_missing,
                }
            )
    return StandardPriceDraftReport(
        standard_items=len(item_ids),
        drafts_available=len(drafts),
        drafts_unavailable=unavailable,
        observations_available=observations,
        groups_missing_supplier_metadata=missing_supplier,
        groups_missing_date_metadata=missing_date,
        approved_versions_created=0,
        drafts=tuple(drafts),
    )
