"""Read models for the procurement command-center dashboard and price trends."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from statistics import median

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.analysis.target_price import latest_cpi_series
from app.catalog.models import (
    DocumentMetadataVersion,
    ItemMembershipDecision,
    MembershipStatus,
    StandardItem,
    StandardItemVersion,
    StandardPriceObservation,
    StandardPriceVersion,
)
from app.cleansing.models import CleanDecision, CleanStatus
from app.cleansing.review_cases import (
    current_review_queue_query,
    summarize_review_cases,
)
from app.documents.models import SourceDocument, SourceVariant
from app.procurement.families import item_family_projection
from app.procurement.models import (
    ProcurementPriceAlert,
)
from app.parsing.projection import current_raw_item_ids
from app.standard_database.models import (
    QuoteDocumentPurpose,
    QuoteDocumentRole,
    StandardBuildStatus,
    StandardDatabaseBuildProjection,
    StandardDatabaseBuildRun,
    StandardOperationalStatus,
)


_DEMO_PERIODS = tuple(f"2026-{month:02d}" for month in range(1, 13))
_INDICATORS = (
    ("USD_KRW", "원/달러", "환율", "index", (100, 101, 100, 103, 105, 104, 107, 106, 108, 107, 109, 110)),
    ("COPPER", "전기동", "원자재", "index", (100, 102, 105, 104, 108, 110, 109, 112, 114, 113, 115, 117)),
    ("STEEL", "냉연강판", "원자재", "index", (100, 99, 101, 102, 103, 102, 104, 105, 106, 106, 107, 108)),
    ("WAGE", "제조 임율", "임율", "index", (100, 100, 101, 101, 102, 102, 103, 103, 104, 104, 105, 105)),
    ("SEMICON", "반도체 수급", "시황", "index", (100, 98, 96, 95, 97, 99, 101, 103, 102, 104, 105, 106)),
)


def dashboard_overview(session: Session, *, today: date | None = None) -> dict[str, object]:
    today = today or date.today()
    total_standard = session.scalar(select(func.count(StandardItem.id))) or 0
    latest_build_id = session.scalar(
        select(StandardDatabaseBuildRun.id)
        .where(StandardDatabaseBuildRun.status == StandardBuildStatus.SUCCEEDED)
        .order_by(StandardDatabaseBuildRun.id.desc())
        .limit(1)
    )
    active = 0
    rebuild_required = 0
    no_evidence = 0
    if latest_build_id is not None:
        statuses = dict(
            session.execute(
                select(
                    StandardDatabaseBuildProjection.operational_status,
                    func.count(StandardDatabaseBuildProjection.id),
                )
                .where(StandardDatabaseBuildProjection.build_run_id == latest_build_id)
                .group_by(StandardDatabaseBuildProjection.operational_status)
            ).all()
        )
        active = statuses.get(StandardOperationalStatus.ACTIVE, 0)
        rebuild_required = statuses.get(StandardOperationalStatus.REBUILD_REQUIRED, 0)
        no_evidence = statuses.get(StandardOperationalStatus.NO_ELIGIBLE_EVIDENCE, 0)

    latest_clean = _latest_ids(CleanDecision, "clean_id")
    current_raw = current_raw_item_ids()
    cleaning_todo, grouped_reason_counts = summarize_review_cases(
        session.execute(
            current_review_queue_query().with_only_columns(
                CleanDecision.raw_item_id,
                SourceVariant.id,
                CleanDecision.reason_code,
                maintain_column_froms=True,
            )
        ).all()
    )
    reason_counts = [
        {"reason_code": reason, "count": count}
        for reason, count in grouped_reason_counts.most_common(6)
    ]
    unmatched = _unmatched_included_count(session, latest_clean, current_raw)
    family_projection = item_family_projection(session)
    family_classified = sum(int(family["item_count"]) for family in family_projection)
    families = [
        {
            "code": family["code"],
            "name": family["name"],
            "item_count": family["item_count"],
            "observation_count": family["observation_count"],
            "share_percent": (
                str((Decimal(int(family["item_count"])) * Decimal("100") / Decimal(family_classified)).quantize(Decimal("0.1")))
                if family_classified
                else "0.0"
            ),
        }
        for family in family_projection
    ]
    monthly = _monthly_performance(session, today)
    alerts = _recent_alerts(session)
    return {
        "as_of": today.isoformat(),
        "catalog": {
            "total_standard_items": total_standard,
            # Kept for older clients. The retired 14-category layer is no
            # longer calculated; item families are the sole classification.
            "categorized_items": family_classified,
            "family_classified_items": family_classified,
            "active_price_items": active,
            "rebuild_required_items": rebuild_required,
            "no_evidence_items": no_evidence,
            "unmatched_included_items": unmatched,
            "cleansing_todo_items": cleaning_todo,
            "latest_build_run_id": latest_build_id,
        },
        "categories": [],
        "families": families,
        "cleansing_todo": {"count": cleaning_todo, "top_reasons": reason_counts},
        "monthly_performance": monthly,
        "indicators": _indicator_payloads(session),
        "alerts": alerts,
    }


def price_trend(session: Session, standard_item_id: int) -> dict[str, object]:
    version = session.scalar(
        select(StandardItemVersion)
        .where(StandardItemVersion.standard_item_id == standard_item_id)
        .order_by(StandardItemVersion.id.desc())
        .limit(1)
    )
    if version is None:
        raise LookupError("표준 품목을 찾을 수 없습니다.")
    current_price_id = session.scalar(
        select(StandardPriceVersion.id)
        .where(StandardPriceVersion.standard_item_id == standard_item_id)
        .order_by(StandardPriceVersion.id.desc())
        .limit(1)
    )
    rows = [] if current_price_id is None else session.execute(
        select(
            StandardPriceObservation.raw_item_id,
            CleanDecision.unit_price,
            DocumentMetadataVersion.quote_date,
            SourceDocument.logical_name,
        )
        .join(CleanDecision, CleanDecision.id == StandardPriceObservation.clean_decision_id)
        .outerjoin(DocumentMetadataVersion, DocumentMetadataVersion.id == StandardPriceObservation.metadata_version_id)
        .join(SourceDocument, SourceDocument.id == DocumentMetadataVersion.source_document_id, isouter=True)
        .join(
            StandardPriceVersion,
            StandardPriceVersion.id == StandardPriceObservation.standard_price_version_id,
        )
        .where(StandardPriceObservation.standard_price_version_id == current_price_id)
        .order_by(StandardPriceObservation.id.desc())
    ).all()
    unique: dict[int, tuple[Decimal, date | None, str | None]] = {}
    for raw_id, unit_price, quote_date, logical_name in rows:
        unique.setdefault(raw_id, (unit_price, quote_date, logical_name))
    by_year: dict[int, list[Decimal]] = defaultdict(list)
    undated = 0
    for unit_price, quote_date, _ in unique.values():
        if quote_date is None:
            undated += 1
        else:
            by_year[quote_date.year].append(unit_price)
    points = [
        {
            "year": year,
            "minimum": min(values),
            "median": Decimal(str(median(values))),
            "maximum": max(values),
            "observation_count": len(values),
        }
        for year, values in sorted(by_year.items())
    ]
    return {
        "standard_item_id": standard_item_id,
        "name": version.canonical_name,
        "spec": version.canonical_spec,
        "points": points,
        "undated_observation_count": undated,
        "note": "견적일이 확인된 독립 원본 단가만 연도별로 표시합니다.",
    }


def _latest_ids(model: type, label: str):
    return (
        select(model.raw_item_id, func.max(model.id).label(label))
        .group_by(model.raw_item_id)
        .subquery()
    )


def _unmatched_included_count(
    session: Session,
    latest_clean: object,
    current_raw: object,
) -> int:
    latest_membership = _latest_ids(ItemMembershipDecision, "membership_id")
    return session.scalar(
        select(func.count(CleanDecision.id))
        .join(latest_clean, latest_clean.c.clean_id == CleanDecision.id)
        .join(current_raw, current_raw.c.raw_item_id == CleanDecision.raw_item_id)
        .outerjoin(latest_membership, latest_membership.c.raw_item_id == CleanDecision.raw_item_id)
        .outerjoin(ItemMembershipDecision, ItemMembershipDecision.id == latest_membership.c.membership_id)
        .where(
            CleanDecision.status == CleanStatus.INCLUDED,
            (ItemMembershipDecision.id.is_(None))
            | (ItemMembershipDecision.status != MembershipStatus.MATCHED),
        )
    ) or 0


def _monthly_performance(session: Session, today: date) -> dict[str, object]:
    # Activation appends a HISTORICAL_REFERENCE role, but the document must
    # remain part of received-quote performance.  Count documents that have
    # ever been received as INCOMING_BID, rather than only their latest role.
    incoming_documents = (
        select(QuoteDocumentRole.document_id)
        .where(QuoteDocumentRole.purpose == QuoteDocumentPurpose.INCOMING_BID)
        .distinct()
        .subquery("dashboard_incoming_documents")
    )
    latest_metadata = (
        select(DocumentMetadataVersion.source_document_id, func.max(DocumentMetadataVersion.id).label("metadata_id"))
        .group_by(DocumentMetadataVersion.source_document_id)
        .subquery("dashboard_latest_metadata")
    )
    dates = session.execute(
        select(DocumentMetadataVersion.quote_date, SourceDocument.created_at)
        .join(incoming_documents, incoming_documents.c.document_id == SourceDocument.id)
        .outerjoin(latest_metadata, latest_metadata.c.source_document_id == SourceDocument.id)
        .outerjoin(DocumentMetadataVersion, DocumentMetadataVersion.id == latest_metadata.c.metadata_id)
    ).all()
    counts: Counter[int] = Counter()
    for quote_date, created_at in dates:
        value = quote_date or (created_at.date() if isinstance(created_at, datetime) else None)
        if value is not None and value.year == today.year:
            counts[value.month] += 1
    completed_months = max(today.month - 1, 1)
    baseline = [counts[month] for month in range(1, completed_months + 1)]
    forecast = int(round(sum(baseline) / len(baseline))) if baseline else 0
    series = [
        {
            "month": month,
            "label": f"{month}월",
            "count": counts[month] if month <= today.month else forecast,
            "kind": "ACTUAL" if month <= today.month else "FORECAST",
        }
        for month in range(1, 13)
    ]
    return {
        "year": today.year,
        "current_month": today.month,
        "series": series,
        "forecast_method": "완료 월의 월평균 접수 건수",
        "source_status": "OPERATIONAL",
    }


def _indicator_payloads(session: Session) -> list[dict[str, object]]:
    payloads = [
        {
            "code": code,
            "name": name,
            "group": group,
            "unit": unit,
            "source_status": "DEMO",
            "source_label": "시연 인덱스 · 공식 데이터 연동 전",
            "points": [
                {"period": period, "value": value}
                for period, value in zip(_DEMO_PERIODS, values, strict=True)
            ],
        }
        for code, name, group, unit, values in _INDICATORS
    ]
    cpi_run, cpi_points = latest_cpi_series(session)
    if cpi_run is not None and cpi_points:
        payloads.append(
            {
                "code": "CPI_ALL",
                "name": "소비자물가 총지수 등락률",
                "group": "시황",
                "unit": "%",
                "source_status": "OFFICIAL_CACHE",
                "source_label": "KOSIS 확정 연간 자료",
                "source_url": cpi_run.source_url,
                "points": [
                    {"period": period, "value": value}
                    for period, value in sorted(cpi_points.items())[-8:]
                ],
            }
        )
    return payloads


def _recent_alerts(session: Session) -> list[dict[str, object]]:
    # Alert persistence was introduced after the first demo database. Keep the
    # dashboard readable against that immutable snapshot until it is migrated.
    if not inspect(session.get_bind()).has_table(ProcurementPriceAlert.__tablename__):
        return []
    latest_versions = (
        select(StandardItemVersion.standard_item_id, func.max(StandardItemVersion.id).label("version_id"))
        .group_by(StandardItemVersion.standard_item_id)
        .subquery("dashboard_alert_latest_version")
    )
    rows = session.execute(
        select(ProcurementPriceAlert, StandardItemVersion.canonical_name)
        .join(latest_versions, latest_versions.c.standard_item_id == ProcurementPriceAlert.standard_item_id)
        .join(StandardItemVersion, StandardItemVersion.id == latest_versions.c.version_id)
        .order_by(ProcurementPriceAlert.id.desc())
        .limit(8)
    ).all()
    return [
        {
            "id": alert.id,
            "item_name": name,
            "severity": alert.severity,
            "direction": alert.direction,
            "current_unit_price": alert.current_unit_price,
            "reference_unit_price": alert.reference_unit_price,
            "difference_percent": alert.difference_percent,
            "message": alert.message,
            "created_at": alert.created_at.isoformat(),
        }
        for alert, name in rows
    ]
