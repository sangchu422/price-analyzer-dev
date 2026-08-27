"""Read models for the procurement command-center dashboard and price trends."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
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
from app.procurement.indicators import indicator_cache_payloads
from app.procurement.models import (
    ProcurementPriceAlert,
)
from app.parsing.projection import current_raw_item_ids
from app.quotes.models import RawQuoteItem
from app.standard_database.models import (
    QuoteDocumentPurpose,
    QuoteDocumentRole,
    StandardBuildStatus,
    StandardDatabaseBuildProjection,
    StandardDatabaseBuildRun,
    StandardOperationalStatus,
)


_MONTHLY_COUNTS = {
    2025: {
        "equipment_purchase": (57, 192, 171, 142, 84, 201, 166, 128, 108, 103, 130, 128),
        "integrated_purchase": (473, 555, 624, 700, 578, 596, 661, 583, 787, 473, 687, 710),
    },
    2026: {
        "equipment_purchase": (116, 129, 146, 96, 138, 263, 159, 81, 106, 63, 90, 102),
        "integrated_purchase": (543, 664, 750, 737, 640, 772, 814, 474, 245, 148, 226, 214),
    },
}

_INDICATOR_IMPACT_RULES: dict[str, tuple[set[str], str, str]] = {
    "USD_KRW": (
        {"ROBOT", "REDUCER", "MOTOR", "VISION_CAMERA", "SENSOR", "MEASUREMENT", "PLC_HMI", "INVERTER_DRIVE", "POWER", "CONTROLLER", "IT_EQUIPMENT", "NETWORK_SOFTWARE"},
        "수입 부품과 외화 결제 비중",
        "환율 상승 시 수입 부품의 원화 구매 부담이 커질 수 있습니다.",
    ),
    "COPPER": (
        {"CABLE_WIRE", "CONNECTOR_TERMINAL", "MOTOR", "POWER", "CONTROL_PANEL", "RELAY_SWITCH", "BREAKER"},
        "동·도체 원재료",
        "전기동 가격은 전선·권선·단자류의 재료비에 직접 영향을 줄 수 있습니다.",
    ),
    "STEEL": (
        {"CONTROL_PANEL", "CONVEYOR", "TRANSFER_LIFT", "PALLET_CARRIER", "ROLLER_CHAIN", "JIG_FIXTURE", "MOLD_TOOL", "FRAME_BASE", "COVER_GUARD", "PLATE_BRACKET", "SHAFT_COUPLING", "FABRICATION", "PIPE_DUCT", "TANK_COOLING"},
        "강재·판재 원재료",
        "강판과 구조재 비중이 높은 제작품은 소재 가격 변동의 영향을 받을 수 있습니다.",
    ),
    "WAGE": (
        {"LABOR_INSTALL", "DESIGN_PROGRAM", "JIG_FIXTURE", "MOLD_TOOL", "FRAME_BASE", "COVER_GUARD", "PLATE_BRACKET", "FABRICATION", "PIPE_DUCT", "CONVEYOR", "TRANSFER_LIFT"},
        "가공·조립·설치 노무비",
        "제작과 설치 공수가 큰 품목은 제조 임율 상승의 영향을 받을 수 있습니다.",
    ),
    "SEMICON": (
        {"ROBOT", "VISION_CAMERA", "SENSOR", "MEASUREMENT", "PLC_HMI", "INVERTER_DRIVE", "POWER", "CONTROLLER", "IT_EQUIPMENT", "NETWORK_SOFTWARE"},
        "전자부품 공급 부담",
        "반도체 수급 부담이 커지면 제어·센서·컴퓨팅 부품의 조달 조건이 악화될 수 있습니다.",
    ),
}


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
            "display_name": _display_family_name(str(family["name"])),
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
    standardization = _standardization_metrics(session)
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
            **standardization,
        },
        "categories": [],
        "families": families,
        "cleansing_todo": {"count": cleaning_todo, "top_reasons": reason_counts},
        "monthly_performance": monthly,
        "indicators": _indicator_payloads(session, family_projection),
        "alerts": alerts,
    }


def family_indicator_impacts(family_code: str) -> list[dict[str, str]]:
    impacts: list[dict[str, str]] = []
    for indicator_code, (high_codes, cost_driver, rationale) in _INDICATOR_IMPACT_RULES.items():
        if family_code in high_codes:
            strength = "HIGH"
        elif family_code.startswith("OTHER_"):
            strength = "LOW"
        else:
            continue
        impacts.append(
            {
                "indicator_code": indicator_code,
                "direction": "COST_PRESSURE",
                "strength": strength,
                "cost_driver": cost_driver,
                "rationale": rationale if strength == "HIGH" else "직접 연관은 낮지만 공통 구매비와 공급 조건을 통해 간접 영향을 받을 수 있습니다.",
                "basis": "RULE_BASED",
            }
        )
    if not impacts:
        impacts.append(
            {
                "indicator_code": "WAGE",
                "direction": "COST_PRESSURE",
                "strength": "LOW",
                "cost_driver": "공통 제조·취급 비용",
                "rationale": "직접 연관은 낮지만 공통 제조비와 취급비를 통해 간접 영향을 받을 수 있습니다.",
                "basis": "RULE_BASED",
            }
        )
    return impacts


def _standardization_metrics(session: Session) -> dict[str, object]:
    latest_role = (
        select(QuoteDocumentRole.document_id, func.max(QuoteDocumentRole.id).label("role_id"))
        .group_by(QuoteDocumentRole.document_id)
        .subquery("dashboard_latest_document_role")
    )
    latest_clean = _latest_ids(CleanDecision, "clean_id")
    latest_membership = _latest_ids(ItemMembershipDecision, "membership_id")
    current_raw = current_raw_item_ids()
    base = (
        select(
            CleanDecision.status,
            ItemMembershipDecision.status.label("membership_status"),
        )
        .select_from(current_raw)
        .join(RawQuoteItem, RawQuoteItem.id == current_raw.c.raw_item_id)
        .join(SourceVariant, SourceVariant.id == RawQuoteItem.source_variant_id)
        .join(latest_role, latest_role.c.document_id == SourceVariant.document_id)
        .join(QuoteDocumentRole, QuoteDocumentRole.id == latest_role.c.role_id)
        .join(latest_clean, latest_clean.c.raw_item_id == RawQuoteItem.id)
        .join(CleanDecision, CleanDecision.id == latest_clean.c.clean_id)
        .outerjoin(latest_membership, latest_membership.c.raw_item_id == RawQuoteItem.id)
        .outerjoin(ItemMembershipDecision, ItemMembershipDecision.id == latest_membership.c.membership_id)
        .where(QuoteDocumentRole.purpose == QuoteDocumentPurpose.HISTORICAL_REFERENCE)
        .subquery("dashboard_standardization_rows")
    )
    eligible = session.scalar(
        select(func.count()).select_from(base).where(base.c.status.in_([CleanStatus.INCLUDED, CleanStatus.REVIEW_REQUIRED]))
    ) or 0
    standardized = session.scalar(
        select(func.count()).select_from(base).where(
            base.c.status == CleanStatus.INCLUDED,
            base.c.membership_status == MembershipStatus.MATCHED,
        )
    ) or 0
    document_count = session.scalar(
        select(func.count()).select_from(latest_role)
        .join(QuoteDocumentRole, QuoteDocumentRole.id == latest_role.c.role_id)
        .where(QuoteDocumentRole.purpose == QuoteDocumentPurpose.HISTORICAL_REFERENCE)
    ) or 0
    pending = max(eligible - standardized, 0)
    percent = Decimal("0") if eligible == 0 else (
        Decimal(standardized) * Decimal("100") / Decimal(eligible)
    ).quantize(Decimal("0.1"))
    return {
        "historical_quote_document_count": document_count,
        "eligible_item_count": eligible,
        "standardized_item_count": standardized,
        "unstandardized_item_count": pending,
        "standardization_percent": str(percent),
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
    del session  # Supplied departmental KPI counts are independent of parsed rows.
    by_year: dict[int, list[dict[str, object]]] = {}
    for year, values in _MONTHLY_COUNTS.items():
        by_year[year] = []
        for month in range(1, 13):
            equipment = values["equipment_purchase"][month - 1]
            integrated = values["integrated_purchase"][month - 1]
            by_year[year].append(
                {
                    "month": month,
                    "label": f"{month}월",
                    "equipment_purchase": equipment,
                    "integrated_purchase": integrated,
                    "total": equipment + integrated,
                    "kind": "FORECAST" if year == 2026 and month >= 9 else "ACTUAL",
                }
            )
    return {
        "default_year": 2026,
        "available_years": [2025, 2026],
        "current_month": today.month,
        "series_by_year": by_year,
        "forecast_method": "2026년 9~12월은 2025년 경향을 반영한 예상 건수",
        "source_status": "DEPARTMENT_SUPPLIED",
    }


def _indicator_payloads(
    session: Session,
    families: list[dict[str, object]],
) -> list[dict[str, object]]:
    payloads = indicator_cache_payloads(session)
    family_lookup = {str(family["code"]): family for family in families}
    for payload in payloads:
        affected = []
        for family_code, family in family_lookup.items():
            impact = next(
                (
                    item
                    for item in family_indicator_impacts(family_code)
                    if item["indicator_code"] == payload["code"]
                ),
                None,
            )
            if impact is not None:
                affected.append(
                    {
                        "family_code": family_code,
                        "family_name": _display_family_name(str(family["name"])),
                        **impact,
                    }
                )
        payload["affected_families"] = sorted(
            affected,
            key=lambda item: (item["strength"] != "HIGH", item["family_name"]),
        )
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
                "latest_period": cpi_run.latest_period,
                "synced_at": cpi_run.fetched_at.isoformat(),
                "error_detail": None,
                "points": [
                    {"period": period, "value": value}
                    for period, value in sorted(cpi_points.items())[-8:]
                ],
                "affected_families": [],
            }
        )
    return payloads


def _display_family_name(name: str) -> str:
    value = name.strip()
    return value[:-1].rstrip() if value.endswith("류") else value


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
