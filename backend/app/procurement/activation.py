"""Approve an incoming quote into the historical standard-price evidence chain."""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.models import QuoteAnalysisLineResult, QuoteAnalysisRun
from app.catalog.models import (
    ItemMembershipDecision,
    MembershipStatus,
    StandardPriceVersion,
)
from app.catalog.service import (
    append_membership_decision,
    create_standard_item,
    current_membership,
)
from app.cleansing.models import CleanDecision, CleanStatus
from app.matching.normalization import normalize_search_text
from app.pricing.service import (
    NoEligiblePriceObservations,
    approve_standard_price,
    calculate_standard_price,
    current_standard_price_version,
)
from app.procurement.categories import assign_current_category
from app.procurement.models import (
    AlertNotificationDelivery,
    ProcurementPriceAlert,
    QuoteCatalogActivationEntry,
    QuoteCatalogActivationRun,
)
from app.quotes.models import RawQuoteItem
from app.standard_database.models import (
    QuoteDocumentPurpose,
    QuoteDocumentRole,
)


ALERT_THRESHOLD_PERCENT = Decimal("20")


class ActivationNotFound(LookupError):
    pass


@dataclass(frozen=True)
class _EntryDraft:
    raw_item_id: int
    action: str
    standard_item_id: int | None
    standard_price_version_id: int | None
    evidence: dict[str, object]


@dataclass(frozen=True)
class _AlertDraft:
    document_id: int
    raw_item_id: int
    standard_item_id: int
    severity: str
    direction: str
    current_unit_price: Decimal
    reference_unit_price: Decimal
    difference_percent: Decimal
    message: str


def activate_analysis_run(
    session: Session,
    analysis_run_id: int,
    *,
    activated_by: str,
    reason_detail: str,
) -> QuoteCatalogActivationRun:
    existing = session.scalar(
        select(QuoteCatalogActivationRun).where(
            QuoteCatalogActivationRun.analysis_run_id == analysis_run_id
        )
    )
    if existing is not None:
        return existing
    actor = activated_by.strip()
    reason = reason_detail.strip()
    if not actor or not reason:
        raise ValueError("표준 DB 반영 담당자와 사유를 입력해 주세요.")
    analysis_run = session.get(QuoteAnalysisRun, analysis_run_id)
    if analysis_run is None:
        raise ActivationNotFound("분석 실행 이력을 찾을 수 없습니다.")
    _promote_document_role(
        session,
        document_id=analysis_run.document_id,
        actor=actor,
        reason=reason,
    )
    line_results = list(
        session.scalars(
            select(QuoteAnalysisLineResult)
            .where(QuoteAnalysisLineResult.analysis_run_id == analysis_run_id)
            .order_by(QuoteAnalysisLineResult.raw_item_id)
        )
    )
    entries: list[_EntryDraft] = []
    prepared_entries: list[_EntryDraft] = []
    alerts: list[_AlertDraft] = []
    counts: Counter[str] = Counter()
    newly_created_by_key: dict[tuple[str, str, str], int] = {}
    previous_prices: dict[int, StandardPriceVersion | None] = {}
    for line in line_results:
        raw = session.get(RawQuoteItem, line.raw_item_id)
        clean = _latest_clean(session, line.raw_item_id)
        if raw is None or clean is None or clean.status is not CleanStatus.INCLUDED:
            entries.append(
                _EntryDraft(
                    line.raw_item_id,
                    "SKIPPED_REVIEW",
                    None,
                    None,
                    {"reason": "정제 완료 품목만 표준 DB에 반영합니다."},
                )
            )
            counts["skipped_review"] += 1
            continue

        membership = current_membership(session, line.raw_item_id)
        standard_item_id = (
            membership.standard_item_id
            if membership is not None
            and membership.status is MembershipStatus.MATCHED
            and membership.standard_item_id is not None
            else None
        )
        action = "MATCHED_EXISTING"
        if standard_item_id is None and line.standard_price_version_id is not None:
            matched_price = session.get(StandardPriceVersion, line.standard_price_version_id)
            standard_item_id = None if matched_price is None else matched_price.standard_item_id
        if standard_item_id is None:
            exact_key = _clean_key(clean, raw)
            standard_item_id = newly_created_by_key.get(exact_key)
            if standard_item_id is None:
                item, _ = create_standard_item(
                    session,
                    canonical_name=clean.item_name_norm or raw.item_name_raw or f"품목 #{raw.id}",
                    canonical_spec=clean.spec_norm,
                    canonical_unit=clean.unit_norm,
                    aliases=[],
                    created_by=actor,
                    reason_detail="신규 견적 승인 반영",
                )
                standard_item_id = item.id
                newly_created_by_key[exact_key] = standard_item_id
                action = "CREATED_STANDARD_ITEM"
                counts["created_standard_items"] += 1
            else:
                action = "MATCHED_ACTIVATION_ITEM"
                counts["matched_activation_item_rows"] += 1
        else:
            counts["matched_existing_rows"] += 1

        if membership is None or membership.status is not MembershipStatus.MATCHED:
            append_membership_decision(
                session,
                raw_item_id=line.raw_item_id,
                standard_item_id=standard_item_id,
                status=MembershipStatus.MATCHED,
                expected_current_decision_id=None if membership is None else membership.id,
                candidate_score=Decimal("1") if line.standard_price_version_id is not None else None,
                method="QUOTE_ACTIVATION_EXACT" if line.standard_price_version_id is not None else "QUOTE_ACTIVATION_NEW",
                evidence={"analysis_run_id": analysis_run_id},
                decided_by=actor,
                reason_detail=reason,
            )
        if standard_item_id not in previous_prices:
            previous_prices[standard_item_id] = current_standard_price_version(
                session,
                standard_item_id,
            )
        previous_price = previous_prices[standard_item_id]
        alert = _price_alert_draft(
            document_id=analysis_run.document_id,
            raw_item=raw,
            clean=clean,
            standard_item_id=standard_item_id,
            previous_price=previous_price,
        )
        if alert is not None:
            alerts.append(alert)
            counts["alerts"] += 1
        prepared_entries.append(
            _EntryDraft(
                raw_item_id=line.raw_item_id,
                action=action,
                standard_item_id=standard_item_id,
                standard_price_version_id=None,
                evidence={"analysis_run_id": analysis_run_id},
            )
        )

    price_versions: dict[int, int] = {}
    price_pending: dict[int, str] = {}
    for standard_item_id in sorted(previous_prices):
        previous_price = previous_prices[standard_item_id]
        try:
            draft = calculate_standard_price(session, standard_item_id)
            approved = approve_standard_price(
                session,
                standard_item_id,
                expected_fingerprint=draft.fingerprint,
                expected_current_version_id=None if previous_price is None else previous_price.id,
                approved_by=actor,
            )
            price_versions[standard_item_id] = approved.id
            counts["price_versions_created"] += 1
        except NoEligiblePriceObservations as exc:
            price_pending[standard_item_id] = str(exc)
            counts["price_pending"] += 1
        assign_current_category(session, standard_item_id, assigned_by=actor)

    for entry in prepared_entries:
        evidence = dict(entry.evidence)
        if entry.standard_item_id in price_pending:
            evidence["price_pending_reason"] = price_pending[entry.standard_item_id]
        entries.append(
            _EntryDraft(
                raw_item_id=entry.raw_item_id,
                action=entry.action,
                standard_item_id=entry.standard_item_id,
                standard_price_version_id=price_versions.get(entry.standard_item_id),
                evidence=evidence,
            )
        )
    counts["included_rows"] = len(prepared_entries)

    run = QuoteCatalogActivationRun(
        analysis_run_id=analysis_run_id,
        document_id=analysis_run.document_id,
        status="SUCCEEDED",
        counts_json=json.dumps(counts, ensure_ascii=False, sort_keys=True),
        activated_by=actor,
        reason_detail=reason,
    )
    session.add(run)
    session.flush()
    entries.sort(key=lambda entry: entry.raw_item_id)
    session.add_all(
        QuoteCatalogActivationEntry(
            activation_run_id=run.id,
            raw_item_id=entry.raw_item_id,
            action=entry.action,
            standard_item_id=entry.standard_item_id,
            standard_price_version_id=entry.standard_price_version_id,
            evidence_json=json.dumps(entry.evidence, ensure_ascii=False, separators=(",", ":")),
        )
        for entry in entries
    )
    session.flush()
    session.add_all(
        ProcurementPriceAlert(
            activation_run_id=run.id,
            **alert.__dict__,
        )
        for alert in alerts
    )
    session.flush()
    return run


def activation_payload(session: Session, run: QuoteCatalogActivationRun) -> dict[str, object]:
    entries = list(
        session.scalars(
            select(QuoteCatalogActivationEntry)
            .where(QuoteCatalogActivationEntry.activation_run_id == run.id)
            .order_by(QuoteCatalogActivationEntry.id)
        )
    )
    alerts = list(
        session.scalars(
            select(ProcurementPriceAlert)
            .where(ProcurementPriceAlert.activation_run_id == run.id)
            .order_by(ProcurementPriceAlert.id)
        )
    )
    return {
        "activation_run_id": run.id,
        "analysis_run_id": run.analysis_run_id,
        "document_id": run.document_id,
        "status": run.status,
        "counts": json.loads(run.counts_json),
        "activated_by": run.activated_by,
        "created_at": run.created_at.isoformat(),
        "entries": [
            {
                "raw_item_id": entry.raw_item_id,
                "action": entry.action,
                "standard_item_id": entry.standard_item_id,
                "standard_price_version_id": entry.standard_price_version_id,
            }
            for entry in entries
        ],
        "alerts": [
            {
                "id": alert.id,
                "raw_item_id": alert.raw_item_id,
                "standard_item_id": alert.standard_item_id,
                "severity": alert.severity,
                "direction": alert.direction,
                "current_unit_price": alert.current_unit_price,
                "reference_unit_price": alert.reference_unit_price,
                "difference_percent": alert.difference_percent,
                "message": alert.message,
            }
            for alert in alerts
        ],
    }


def deliver_outlook_alerts(
    session: Session,
    run: QuoteCatalogActivationRun,
    *,
    recipient: str,
) -> list[AlertNotificationDelivery]:
    address = recipient.strip()
    if not address or "@" not in address or len(address) > 320:
        raise ValueError("Outlook 알림 수신 이메일을 확인해 주세요.")
    alerts = list(
        session.scalars(
            select(ProcurementPriceAlert)
            .where(ProcurementPriceAlert.activation_run_id == run.id)
            .order_by(ProcurementPriceAlert.id)
        )
    )
    deliveries: list[AlertNotificationDelivery] = []
    for alert in alerts:
        existing = session.scalar(
            select(AlertNotificationDelivery).where(
                AlertNotificationDelivery.alert_id == alert.id,
                AlertNotificationDelivery.channel == "OUTLOOK",
                AlertNotificationDelivery.recipient == address,
            )
        )
        if existing is not None:
            deliveries.append(existing)
            continue
        status, detail = _send_outlook(address, alert)
        delivery = AlertNotificationDelivery(
            alert_id=alert.id,
            channel="OUTLOOK",
            recipient=address,
            status=status,
            detail=detail,
        )
        session.add(delivery)
        deliveries.append(delivery)
    session.flush()
    return deliveries


def _promote_document_role(
    session: Session,
    *,
    document_id: int,
    actor: str,
    reason: str,
) -> None:
    current = session.scalar(
        select(QuoteDocumentRole)
        .where(QuoteDocumentRole.document_id == document_id)
        .order_by(QuoteDocumentRole.id.desc())
        .limit(1)
    )
    if current is not None and current.purpose is QuoteDocumentPurpose.HISTORICAL_REFERENCE:
        return
    session.add(
        QuoteDocumentRole(
            document_id=document_id,
            purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
            supersedes_role_id=None if current is None else current.id,
            decided_by=actor,
            reason_detail=f"QUOTE_ACTIVATION: {reason}",
        )
    )
    session.flush()


def _latest_clean(session: Session, raw_item_id: int) -> CleanDecision | None:
    return session.scalar(
        select(CleanDecision)
        .where(CleanDecision.raw_item_id == raw_item_id)
        .order_by(CleanDecision.id.desc())
        .limit(1)
    )


def _clean_key(
    clean: CleanDecision,
    raw_item: RawQuoteItem,
) -> tuple[str, str, str]:
    return (
        normalize_search_text(clean.item_name_norm or raw_item.item_name_raw),
        normalize_search_text(clean.spec_norm or raw_item.spec_raw),
        normalize_search_text(clean.unit_norm or raw_item.unit_raw),
    )


def _price_alert_draft(
    *,
    document_id: int,
    raw_item: RawQuoteItem,
    clean: CleanDecision,
    standard_item_id: int,
    previous_price: StandardPriceVersion | None,
) -> _AlertDraft | None:
    if previous_price is None or clean.unit_price is None or previous_price.median_price <= 0:
        return None
    difference = (
        (clean.unit_price - previous_price.median_price)
        / previous_price.median_price
        * Decimal("100")
    ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    if abs(difference) < ALERT_THRESHOLD_PERCENT:
        return None
    direction = "HIGH" if difference > 0 else "LOW"
    severity = "CRITICAL" if abs(difference) >= Decimal("40") else "WARNING"
    name = clean.item_name_norm or raw_item.item_name_raw or f"품목 #{raw_item.id}"
    message = (
        f"{name} 신규 단가가 기존 중앙값보다 "
        f"{'높습니다' if direction == 'HIGH' else '낮습니다'} "
        f"({difference:+.1f}%). 원본과 규격을 확인해 주세요."
    )
    return _AlertDraft(
        document_id=document_id,
        raw_item_id=raw_item.id,
        standard_item_id=standard_item_id,
        severity=severity,
        direction=direction,
        current_unit_price=clean.unit_price,
        reference_unit_price=previous_price.median_price,
        difference_percent=difference,
        message=message,
    )


def _send_outlook(recipient: str, alert: ProcurementPriceAlert) -> tuple[str, str]:
    if os.name != "nt":
        return "UNAVAILABLE", "Outlook COM 연동은 Windows에서만 사용할 수 있습니다."
    try:
        import win32com.client  # type: ignore[import-not-found]

        outlook = win32com.client.Dispatch("Outlook.Application")
        message = outlook.CreateItem(0)
        message.To = recipient
        message.Subject = "[Price Analyzer] 신규 견적 가격 변동 주의"
        message.Body = (
            f"{alert.message}\n\n"
            f"신규 개당 단가: {alert.current_unit_price:,.0f}원\n"
            f"기존 중앙값: {alert.reference_unit_price:,.0f}원\n"
            f"차이: {alert.difference_percent:+.1f}%\n"
            f"표준 품목 ID: {alert.standard_item_id}\n"
            f"원문 품목 ID: {alert.raw_item_id}\n"
        )
        message.Send()
        return "SENT", "Outlook 데스크톱을 통해 발송했습니다."
    except Exception as exc:
        return "FAILED", f"Outlook 발송 실패: {type(exc).__name__}: {exc}"
