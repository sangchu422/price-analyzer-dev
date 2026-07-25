"""Human-controlled standard-item catalog projections and mutations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.catalog.models import (
    DocumentMetadataVersion,
    ItemMembershipDecision,
    MembershipStatus,
    StandardItem,
    StandardItemVersion,
)
from app.cleansing.models import CleanDecision, CleanStatus
from app.documents.models import SourceDocument, SourceVariant
from app.matching.candidates import (
    CandidateItem,
    CandidateScore,
    MatchQuery,
    rank_candidates,
)
from app.quotes.models import RawQuoteItem


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _validate_human_audit(actor: str, reason: str) -> tuple[str, str]:
    normalized_actor = actor.strip()
    normalized_reason = reason.strip()
    if not normalized_actor or normalized_actor.casefold() == "system":
        raise ValueError("manual catalog changes require a human actor")
    if not normalized_reason:
        raise ValueError("manual catalog changes require an audit reason")
    return normalized_actor, normalized_reason


class CatalogNotFound(LookupError):
    pass


class CatalogConflict(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        current_id: int | None = None,
        current_key: str = "current_id",
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.current_id = current_id
        self.current_key = current_key


@dataclass(frozen=True)
class CatalogCandidate:
    standard_item_id: int
    version_id: int
    canonical_name: str
    canonical_spec: str | None
    canonical_unit: str | None
    aliases: tuple[str, ...]
    score: CandidateScore
    unit_compatible: bool = True
    model_tokens_compatible: bool = True


@dataclass(frozen=True)
class CandidateResult:
    match_status: Literal["CANDIDATE", "NO_MATCH"]
    raw_item: RawQuoteItem
    current_cleansing_decision: CleanDecision
    source_variant: SourceVariant
    source_document: SourceDocument
    candidates: tuple[CatalogCandidate, ...]


def _latest_clean_decision(
    session: Session,
    raw_item_id: int,
) -> CleanDecision:
    decision = session.scalar(
        select(CleanDecision)
        .where(CleanDecision.raw_item_id == raw_item_id)
        .order_by(CleanDecision.id.desc())
        .limit(1)
    )
    if decision is None:
        raise CatalogConflict(
            "RAW_ITEM_NOT_INCLUDED",
            "raw item has no cleansing decision",
        )
    return decision


def _require_included(
    session: Session,
    raw_item_id: int,
) -> tuple[RawQuoteItem, CleanDecision]:
    raw_item = session.get(RawQuoteItem, raw_item_id)
    if raw_item is None:
        raise CatalogNotFound("raw quote item not found")
    decision = _latest_clean_decision(session, raw_item_id)
    if decision.status != CleanStatus.INCLUDED:
        raise CatalogConflict(
            "RAW_ITEM_NOT_INCLUDED",
            "only a currently INCLUDED raw item can be grouped",
            current_id=decision.id,
            current_key="current_decision_id",
        )
    return raw_item, decision


def current_standard_item_version(
    session: Session,
    standard_item_id: int,
) -> StandardItemVersion | None:
    return session.scalar(
        select(StandardItemVersion)
        .where(StandardItemVersion.standard_item_id == standard_item_id)
        .order_by(StandardItemVersion.id.desc())
        .limit(1)
    )


def current_document_metadata(
    session: Session,
    document_id: int,
) -> DocumentMetadataVersion | None:
    return session.scalar(
        select(DocumentMetadataVersion)
        .where(DocumentMetadataVersion.source_document_id == document_id)
        .order_by(DocumentMetadataVersion.id.desc())
        .limit(1)
    )


def current_membership(
    session: Session,
    raw_item_id: int,
) -> ItemMembershipDecision | None:
    return session.scalar(
        select(ItemMembershipDecision)
        .where(ItemMembershipDecision.raw_item_id == raw_item_id)
        .order_by(ItemMembershipDecision.id.desc())
        .limit(1)
    )


def _parse_aliases(value: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(alias for alias in parsed if isinstance(alias, str))


def candidate_matches(
    session: Session,
    raw_item_id: int,
    *,
    top_n: int = 10,
) -> CandidateResult:
    """Return replaceable evidence and never create a membership row."""

    raw_item, clean = _require_included(session, raw_item_id)
    latest_ids = (
        select(
            StandardItemVersion.standard_item_id,
            func.max(StandardItemVersion.id).label("version_id"),
        )
        .group_by(StandardItemVersion.standard_item_id)
        .subquery()
    )
    versions = list(
        session.scalars(
            select(StandardItemVersion)
            .join(latest_ids, latest_ids.c.version_id == StandardItemVersion.id)
            .order_by(StandardItemVersion.standard_item_id)
        )
    )
    by_id = {version.standard_item_id: version for version in versions}
    scores = rank_candidates(
        query=MatchQuery(
            name=clean.item_name_norm or raw_item.item_name_raw or "",
            spec=clean.spec_norm,
            unit=clean.unit_norm,
        ),
        items=[
            CandidateItem(
                standard_item_id=version.standard_item_id,
                name=version.canonical_name,
                spec=version.canonical_spec,
                unit=version.canonical_unit,
                aliases=_parse_aliases(version.aliases_json),
            )
            for version in versions
        ],
        top_n=top_n,
    )
    candidates = tuple(
        CatalogCandidate(
            standard_item_id=score.standard_item_id,
            version_id=by_id[score.standard_item_id].id,
            canonical_name=by_id[score.standard_item_id].canonical_name,
            canonical_spec=by_id[score.standard_item_id].canonical_spec,
            canonical_unit=by_id[score.standard_item_id].canonical_unit,
            aliases=_parse_aliases(
                by_id[score.standard_item_id].aliases_json
            ),
            score=score,
        )
        for score in scores
    )
    variant = raw_item.source_variant
    return CandidateResult(
        match_status="CANDIDATE" if candidates else "NO_MATCH",
        raw_item=raw_item,
        current_cleansing_decision=clean,
        source_variant=variant,
        source_document=variant.document,
        candidates=candidates,
    )


def append_membership_decision(
    session: Session,
    *,
    raw_item_id: int,
    standard_item_id: int | None,
    status: MembershipStatus,
    expected_current_decision_id: int | None,
    candidate_score: Decimal | None,
    method: str,
    evidence: dict[str, Any],
    decided_by: str,
    reason_detail: str,
) -> ItemMembershipDecision:
    decided_by, reason_detail = _validate_human_audit(
        decided_by,
        reason_detail,
    )
    raw_item, _ = _require_included(session, raw_item_id)
    current = current_membership(session, raw_item_id)
    current_id = current.id if current is not None else None
    if current_id != expected_current_decision_id:
        raise CatalogConflict(
            "STALE_CATALOG_DECISION",
            "catalog decision changed; refresh and retry",
            current_id=current_id,
            current_key="current_decision_id",
        )
    if status == MembershipStatus.MATCHED:
        if standard_item_id is None:
            raise ValueError("MATCHED requires a standard item")
        if session.get(StandardItem, standard_item_id) is None:
            raise CatalogNotFound("standard item not found")
    elif standard_item_id is not None:
        raise ValueError("REJECTED must not target a standard item")
    audited_evidence = dict(evidence)
    audited_evidence["reason_detail"] = reason_detail
    row = ItemMembershipDecision(
        raw_item=raw_item,
        standard_item_id=standard_item_id,
        status=status,
        candidate_score=candidate_score,
        method=method,
        evidence_json=json.dumps(
            audited_evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        supersedes_decision_id=current_id,
        decided_by=decided_by,
    )
    session.add(row)
    session.flush()
    return row


def create_standard_item(
    session: Session,
    *,
    canonical_name: str,
    canonical_spec: str | None,
    canonical_unit: str | None,
    aliases: list[str],
    created_by: str,
    reason_detail: str,
) -> tuple[StandardItem, StandardItemVersion]:
    created_by, reason_detail = _validate_human_audit(
        created_by,
        reason_detail,
    )
    item = StandardItem()
    version = StandardItemVersion(
        standard_item=item,
        version_number=1,
        canonical_name=canonical_name,
        canonical_spec=canonical_spec,
        canonical_unit=canonical_unit,
        aliases_json=json.dumps(
            aliases,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        created_by=created_by,
        change_reason=reason_detail,
    )
    session.add_all([item, version])
    session.flush()
    return item, version


def append_standard_item_version(
    session: Session,
    *,
    standard_item_id: int,
    expected_current_version_id: int,
    canonical_name: str,
    canonical_spec: str | None,
    canonical_unit: str | None,
    aliases: list[str],
    created_by: str,
    reason_detail: str,
) -> StandardItemVersion:
    created_by, reason_detail = _validate_human_audit(
        created_by,
        reason_detail,
    )
    if session.get(StandardItem, standard_item_id) is None:
        raise CatalogNotFound("standard item not found")
    current = current_standard_item_version(session, standard_item_id)
    current_id = current.id if current is not None else None
    if current is None or current_id != expected_current_version_id:
        raise CatalogConflict(
            "STALE_CATALOG_DECISION",
            "standard item version changed; refresh and retry",
            current_id=current_id,
            current_key="current_version_id",
        )
    row = StandardItemVersion(
        standard_item_id=standard_item_id,
        version_number=current.version_number + 1,
        canonical_name=canonical_name,
        canonical_spec=canonical_spec,
        canonical_unit=canonical_unit,
        aliases_json=json.dumps(
            aliases,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        created_by=created_by,
        change_reason=reason_detail,
    )
    session.add(row)
    session.flush()
    return row


def append_document_metadata(
    session: Session,
    *,
    document_id: int,
    supplier_name: str | None,
    quote_date: date | None,
    project_name: str | None,
    expected_current_version_id: int | None,
    decided_by: str,
    reason_detail: str,
) -> DocumentMetadataVersion:
    decided_by, reason_detail = _validate_human_audit(
        decided_by,
        reason_detail,
    )
    if session.get(SourceDocument, document_id) is None:
        raise CatalogNotFound("source document not found")
    current = current_document_metadata(session, document_id)
    current_id = current.id if current is not None else None
    if current_id != expected_current_version_id:
        raise CatalogConflict(
            "STALE_CATALOG_DECISION",
            "document metadata changed; refresh and retry",
            current_id=current_id,
            current_key="current_version_id",
        )
    row = DocumentMetadataVersion(
        source_document_id=document_id,
        version_number=1 if current is None else current.version_number + 1,
        supplier_name=supplier_name,
        quote_date=quote_date,
        project_name=project_name,
        decided_by=decided_by,
        reason_detail=reason_detail,
    )
    session.add(row)
    session.flush()
    return row


def unmatched_included(
    session: Session,
    *,
    after_id: int | None,
    limit: int,
    search: str | None,
) -> tuple[list[tuple[RawQuoteItem, CleanDecision]], int | None]:
    latest_clean = (
        select(
            CleanDecision.raw_item_id,
            func.max(CleanDecision.id).label("decision_id"),
        )
        .group_by(CleanDecision.raw_item_id)
        .subquery()
    )
    latest_membership = (
        select(
            ItemMembershipDecision.raw_item_id,
            func.max(ItemMembershipDecision.id).label("decision_id"),
        )
        .group_by(ItemMembershipDecision.raw_item_id)
        .subquery()
    )
    current_membership = ItemMembershipDecision.__table__.alias(
        "current_membership"
    )
    query = (
        select(RawQuoteItem, CleanDecision)
        .join(latest_clean, latest_clean.c.raw_item_id == RawQuoteItem.id)
        .join(CleanDecision, CleanDecision.id == latest_clean.c.decision_id)
        .outerjoin(
            latest_membership,
            latest_membership.c.raw_item_id == RawQuoteItem.id,
        )
        .outerjoin(
            current_membership,
            current_membership.c.id == latest_membership.c.decision_id,
        )
        .where(CleanDecision.status == CleanStatus.INCLUDED)
        .where(
            or_(
                current_membership.c.id.is_(None),
                current_membership.c.status == MembershipStatus.REJECTED,
            )
        )
    )
    if after_id is not None:
        query = query.where(RawQuoteItem.id > after_id)
    normalized_search = (search or "").strip()
    if normalized_search:
        pattern = f"%{_escape_like(normalized_search.casefold())}%"
        query = query.where(
            or_(
                func.lower(CleanDecision.item_name_norm).like(
                    pattern,
                    escape="\\",
                ),
                func.lower(CleanDecision.spec_norm).like(
                    pattern,
                    escape="\\",
                ),
            )
        )
    rows = session.execute(
        query.order_by(RawQuoteItem.id).limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    page = [(raw, clean) for raw, clean in rows[:limit]]
    return page, page[-1][0].id if has_more and page else None


def standard_item_members(
    session: Session,
    standard_item_id: int,
) -> list[tuple[RawQuoteItem, CleanDecision, ItemMembershipDecision]]:
    if session.get(StandardItem, standard_item_id) is None:
        raise CatalogNotFound("standard item not found")
    latest_membership = (
        select(
            ItemMembershipDecision.raw_item_id,
            func.max(ItemMembershipDecision.id).label("decision_id"),
        )
        .group_by(ItemMembershipDecision.raw_item_id)
        .subquery()
    )
    latest_clean = (
        select(
            CleanDecision.raw_item_id,
            func.max(CleanDecision.id).label("decision_id"),
        )
        .group_by(CleanDecision.raw_item_id)
        .subquery()
    )
    return [
        (raw, clean, membership)
        for raw, clean, membership in session.execute(
            select(RawQuoteItem, CleanDecision, ItemMembershipDecision)
            .join(
                latest_membership,
                latest_membership.c.raw_item_id == RawQuoteItem.id,
            )
            .join(
                ItemMembershipDecision,
                ItemMembershipDecision.id
                == latest_membership.c.decision_id,
            )
            .join(latest_clean, latest_clean.c.raw_item_id == RawQuoteItem.id)
            .join(CleanDecision, CleanDecision.id == latest_clean.c.decision_id)
            .where(
                ItemMembershipDecision.standard_item_id == standard_item_id,
                ItemMembershipDecision.status == MembershipStatus.MATCHED,
            )
            .order_by(RawQuoteItem.id)
        )
    ]
