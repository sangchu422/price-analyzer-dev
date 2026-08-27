"""Analysis history and reversible, append-only catalog inclusion decisions."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis.models import QuoteAnalysisRun
from app.catalog.models import MembershipStatus
from app.catalog.service import append_membership_decision, current_membership
from app.documents.models import SourceDocument
from app.procurement.activation import (
    ActivationNotFound,
    activate_analysis_run,
)
from app.procurement.models import (
    QuoteCatalogActivationEntry,
    QuoteCatalogActivationRun,
    QuoteCatalogStateDecision,
)
from app.standard_database.models import QuoteDocumentPurpose, QuoteDocumentRole
from app.standard_database.service import build_standard_database


class CatalogStateConflict(RuntimeError):
    def __init__(self, current_decision_id: int | None) -> None:
        super().__init__("표준 DB 반영 상태가 변경되었습니다. 새로고침 후 다시 시도해 주세요.")
        self.current_decision_id = current_decision_id


@dataclass(frozen=True)
class AnalysisHistoryPage:
    items: list[dict[str, object]]
    total: int
    next_cursor: int | None


def current_catalog_state(
    session: Session,
    document_id: int,
) -> QuoteCatalogStateDecision | None:
    return session.scalar(
        select(QuoteCatalogStateDecision)
        .where(QuoteCatalogStateDecision.document_id == document_id)
        .order_by(QuoteCatalogStateDecision.id.desc())
        .limit(1)
    )


def list_analysis_history(
    session: Session,
    *,
    limit: int,
    after_id: int | None = None,
    state: str | None = None,
) -> AnalysisHistoryPage:
    latest_state = (
        select(
            QuoteCatalogStateDecision.document_id,
            func.max(QuoteCatalogStateDecision.id).label("decision_id"),
        )
        .group_by(QuoteCatalogStateDecision.document_id)
        .subquery("latest_catalog_state")
    )
    query = (
        select(QuoteAnalysisRun, SourceDocument, QuoteCatalogStateDecision)
        .join(SourceDocument, SourceDocument.id == QuoteAnalysisRun.document_id)
        .outerjoin(latest_state, latest_state.c.document_id == QuoteAnalysisRun.document_id)
        .outerjoin(QuoteCatalogStateDecision, QuoteCatalogStateDecision.id == latest_state.c.decision_id)
        .order_by(QuoteAnalysisRun.id.desc())
    )
    if after_id is not None:
        query = query.where(QuoteAnalysisRun.id < after_id)
    if state is not None:
        if state == "NOT_INCLUDED":
            query = query.where(QuoteCatalogStateDecision.id.is_(None))
        else:
            query = query.where(QuoteCatalogStateDecision.state == state)
    rows = list(session.execute(query.limit(limit + 1)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    total_query = select(func.count(QuoteAnalysisRun.id))
    if state is not None:
        total_query = (
            total_query
            .outerjoin(latest_state, latest_state.c.document_id == QuoteAnalysisRun.document_id)
            .outerjoin(QuoteCatalogStateDecision, QuoteCatalogStateDecision.id == latest_state.c.decision_id)
        )
        total_query = total_query.where(
            QuoteCatalogStateDecision.id.is_(None)
            if state == "NOT_INCLUDED"
            else QuoteCatalogStateDecision.state == state
        )
    total = session.scalar(total_query) or 0
    return AnalysisHistoryPage(
        items=[_history_payload(run, document, decision) for run, document, decision in rows],
        total=total,
        next_cursor=rows[-1][0].id if has_more and rows else None,
    )


def set_catalog_state(
    session: Session,
    analysis_run_id: int,
    *,
    state: str,
    decided_by: str,
    reason_detail: str,
    expected_current_decision_id: int | None,
) -> QuoteCatalogStateDecision:
    if state not in {"INCLUDED", "EXCLUDED"}:
        raise ValueError("표준 DB 반영 상태를 확인해 주세요.")
    actor = decided_by.strip()
    reason = reason_detail.strip()
    if len(actor) < 1 or len(reason) < 3:
        raise ValueError("담당자와 변경 사유를 입력해 주세요.")
    run = session.get(QuoteAnalysisRun, analysis_run_id)
    if run is None:
        raise ActivationNotFound("분석 실행 이력을 찾을 수 없습니다.")
    current = current_catalog_state(session, run.document_id)
    current_id = None if current is None else current.id
    if current_id != expected_current_decision_id:
        raise CatalogStateConflict(current_id)
    current_state = "NOT_INCLUDED" if current is None else current.state
    if current_state == state:
        return current  # type: ignore[return-value]

    if state == "INCLUDED":
        activation = session.scalar(
            select(QuoteCatalogActivationRun).where(
                QuoteCatalogActivationRun.analysis_run_id == analysis_run_id
            )
        )
        if activation is None:
            activation = activate_analysis_run(
                session,
                analysis_run_id,
                activated_by=actor,
                reason_detail=reason,
            )
        else:
            _append_document_role(
                session,
                run.document_id,
                QuoteDocumentPurpose.HISTORICAL_REFERENCE,
                actor,
                f"QUOTE_REINCLUDED: {reason}",
            )
            _restore_activation_memberships(session, activation, actor, reason)
    else:
        activation = session.scalar(
            select(QuoteCatalogActivationRun).where(
                QuoteCatalogActivationRun.document_id == run.document_id
            ).order_by(QuoteCatalogActivationRun.id.desc()).limit(1)
        )
        _append_document_role(
            session,
            run.document_id,
            QuoteDocumentPurpose.INCOMING_BID,
            actor,
            f"QUOTE_EXCLUDED: {reason}",
        )
        if activation is not None:
            _exclude_activation_memberships(session, activation, actor, reason)

    # Rebuild the immutable operational projection so the current Standard DB
    # follows the latest document role while all prior prices stay auditable.
    build_standard_database(session, actor=actor)
    decision = QuoteCatalogStateDecision(
        analysis_run_id=analysis_run_id,
        document_id=run.document_id,
        state=state,
        supersedes_decision_id=current_id,
        decided_by=actor,
        reason_detail=reason,
    )
    session.add(decision)
    session.flush()
    return decision


def catalog_state_payload(decision: QuoteCatalogStateDecision) -> dict[str, object]:
    return {
        "decision_id": decision.id,
        "analysis_run_id": decision.analysis_run_id,
        "document_id": decision.document_id,
        "state": decision.state,
        "decided_by": decision.decided_by,
        "reason_detail": decision.reason_detail,
        "decided_at": decision.decided_at.isoformat(),
    }


def _history_payload(
    run: QuoteAnalysisRun,
    document: SourceDocument,
    decision: QuoteCatalogStateDecision | None,
) -> dict[str, object]:
    return {
        "run_id": run.id,
        "document_id": run.document_id,
        "file_name": document.logical_name,
        "created_by": run.created_by,
        "analyzed_at": run.created_at.isoformat(),
        "total_line_count": run.total_line_count,
        "target_available_count": run.target_available_count,
        "quote_total_amount": run.quote_total_amount,
        "target_total_amount": run.target_total_amount,
        "catalog_state": "NOT_INCLUDED" if decision is None else decision.state,
        "current_decision_id": None if decision is None else decision.id,
        "state_decided_by": None if decision is None else decision.decided_by,
        "state_decided_at": None if decision is None else decision.decided_at.isoformat(),
    }


def _append_document_role(
    session: Session,
    document_id: int,
    purpose: QuoteDocumentPurpose,
    actor: str,
    reason: str,
) -> None:
    current = session.scalar(
        select(QuoteDocumentRole)
        .where(QuoteDocumentRole.document_id == document_id)
        .order_by(QuoteDocumentRole.id.desc())
        .limit(1)
    )
    if current is not None and current.purpose is purpose:
        return
    session.add(
        QuoteDocumentRole(
            document_id=document_id,
            purpose=purpose,
            supersedes_role_id=None if current is None else current.id,
            decided_by=actor,
            reason_detail=reason,
        )
    )
    session.flush()


def _activation_entries(session: Session, activation_id: int) -> list[QuoteCatalogActivationEntry]:
    return list(
        session.scalars(
            select(QuoteCatalogActivationEntry)
            .where(QuoteCatalogActivationEntry.activation_run_id == activation_id)
            .order_by(QuoteCatalogActivationEntry.raw_item_id)
        )
    )


def _exclude_activation_memberships(
    session: Session,
    activation: QuoteCatalogActivationRun,
    actor: str,
    reason: str,
) -> None:
    for entry in _activation_entries(session, activation.id):
        current = current_membership(session, entry.raw_item_id)
        if current is None or current.status is not MembershipStatus.MATCHED:
            continue
        append_membership_decision(
            session,
            raw_item_id=entry.raw_item_id,
            standard_item_id=None,
            status=MembershipStatus.REJECTED,
            expected_current_decision_id=current.id,
            candidate_score=None,
            method="QUOTE_CATALOG_EXCLUSION",
            evidence={"activation_run_id": activation.id},
            decided_by=actor,
            reason_detail=reason,
        )


def _restore_activation_memberships(
    session: Session,
    activation: QuoteCatalogActivationRun,
    actor: str,
    reason: str,
) -> None:
    for entry in _activation_entries(session, activation.id):
        if entry.standard_item_id is None:
            continue
        current = current_membership(session, entry.raw_item_id)
        if (
            current is not None
            and current.status is MembershipStatus.MATCHED
            and current.standard_item_id == entry.standard_item_id
        ):
            continue
        append_membership_decision(
            session,
            raw_item_id=entry.raw_item_id,
            standard_item_id=entry.standard_item_id,
            status=MembershipStatus.MATCHED,
            expected_current_decision_id=None if current is None else current.id,
            candidate_score=None,
            method="QUOTE_CATALOG_REINCLUSION",
            evidence={"activation_run_id": activation.id},
            decided_by=actor,
            reason_detail=reason,
        )
