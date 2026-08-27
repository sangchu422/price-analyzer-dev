"""Operational item-family projection for the executive demo.

Families are a navigational and analytical layer over immutable exact standard
items. They deliberately never rewrite membership or standard prices.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog.models import (
    DocumentMetadataVersion,
    StandardItemVersion,
    StandardPriceObservation,
    StandardPriceVersion,
)
from app.cleansing.models import CleanDecision
from app.documents.models import SourceDocument, SourceVariant
from app.matching.normalization import normalize_search_text
from app.quotes.models import RawQuoteItem
from app.standard_database.models import (
    StandardBuildStatus,
    StandardDatabaseBuildProjection,
    StandardDatabaseBuildRun,
    StandardOperationalStatus,
)


FAMILY_RULE_VERSION = "item-family-keyword-v1"


@dataclass(frozen=True)
class FamilyMatch:
    code: str
    name: str
    matched_term: str | None


_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("CABLE_WIRE", "케이블·전선류", ("cable", "wire", "harness", "케이블", "전선", "하네스")),
    ("CONNECTOR_TERMINAL", "커넥터·단자류", ("connector", "terminal", "bus bar", "커넥터", "단자", "부스바")),
    ("ROBOT", "로봇류", ("robot", "로봇")),
    ("REDUCER", "감속기류", ("reducer", "reduction gear", "gear head", "감속기", "감속")),
    ("MOTOR", "모터류", ("servo motor", "motor", "geared motor", "모터")),
    ("LINEAR_MOTION", "리니어·가이드류", ("linear guide", "lm guide", "ball screw", "guide rail", "actuator", "가이드", "볼스크류")),
    ("VISION_CAMERA", "비전·카메라류", ("vision", "camera", "scanner", "barcode", "비전", "카메라", "스캐너", "바코드")),
    ("SENSOR", "센서류", ("photo sensor", "proximity", "sensor", "encoder", "limit switch", "센서", "엔코더", "근접")),
    ("MEASUREMENT", "계측기류", ("gauge", "meter", "tester", "load cell", "torque", "probe", "scale", "계측", "측정", "검사기", "시험기")),
    ("PLC_HMI", "PLC·HMI류", ("plc", "hmi", "touch panel", "programmable controller")),
    ("INVERTER_DRIVE", "인버터·드라이브류", ("inverter", "invertor", "servo drive", "servo amp", "amplifier", "인버터", "드라이브")),
    ("RELAY_SWITCH", "릴레이·스위치류", ("relay", "switch", "contactor", "리미트", "릴레이", "스위치")),
    ("BREAKER", "차단기류", ("breaker", "mccb", "elcb", "nfb", "차단기")),
    ("POWER", "전원장치류", ("power supply", "smps", "ups", "avr", "transformer", "battery", "전원", "변압기", "배터리")),
    ("CONTROL_PANEL", "제어반·판넬류", ("control panel", "electric panel", "control box", "panel", "판넬", "제어반", "콘트롤박스")),
    ("CONTROLLER", "제어기류", ("controller", "control unit", "control card", "제어기", "컨트롤러")),
    ("VALVE", "밸브류", ("solenoid valve", "valve", "밸브")),
    ("CYLINDER", "실린더류", ("air cylinder", "hydraulic cylinder", "cylinder", "실린더")),
    ("PUMP_VACUUM", "펌프·진공류", ("vacuum", "pump", "ejector", "펌프", "진공")),
    ("PNEUMATIC_PART", "공압·유압부품류", ("regulator", "manifold", "fitting", "pneumatic", "hydraulic", "공압", "유압", "피팅")),
    ("CONVEYOR", "컨베이어류", ("conveyor", "컨베이어")),
    ("TRANSFER_LIFT", "이송·리프트류", ("transfer", "loader", "unloader", "shuttle", "feeder", "lifter", "lift", "hoist", "이송", "리프트")),
    ("PALLET_CARRIER", "팔레트·캐리어류", ("pallet", "carrier", "hanger", "stocker", "팔레트", "캐리어")),
    ("ROLLER_CHAIN", "롤러·체인류", ("roller", "chain", "sprocket", "롤러", "체인", "스프로켓")),
    ("BEARING_BUSH", "베어링·부시류", ("bearing", "bush", "베어링", "부시")),
    ("FASTENER", "체결부품류", ("bolt", "nut", "screw", "washer", "pin", "볼트", "너트", "스크류", "와셔")),
    ("JIG_FIXTURE", "지그·치구류", ("jig", "fixture", "chuck", "gripper", "clamp", "지그", "치구", "척", "클램프")),
    ("MOLD_TOOL", "금형·공구류", ("mold", "die", "tooling", "collet", "금형", "공구")),
    ("FRAME_BASE", "프레임·베이스류", ("frame", "base frame", "machine bed", "bed", "프레임", "베이스", "구조물")),
    ("COVER_GUARD", "커버·안전가드류", ("safety cover", "cover", "guard", "fence", "커버", "가드", "펜스", "방호")),
    ("PLATE_BRACKET", "플레이트·브라켓류", ("plate", "bracket", "block", "support", "플레이트", "브라켓", "블록", "서포트")),
    ("SHAFT_COUPLING", "샤프트·커플링류", ("shaft", "coupling", "joint", "pulley", "샤프트", "커플링", "조인트", "풀리")),
    ("FABRICATION", "가공·제작품류", ("machining", "fabrication", "manufacturing", "제작품", "가공", "제작")),
    ("PIPE_DUCT", "배관·덕트류", ("pipe", "duct", "hose", "nozzle", "배관", "덕트", "호스", "노즐")),
    ("FILTER_EXHAUST", "필터·집진류", ("filter", "exhaust", "silencer", "dust collector", "필터", "집진", "배기")),
    ("TANK_COOLING", "탱크·냉각장치류", ("tank", "chiller", "cooler", "heater", "coolant", "탱크", "칠러", "냉각", "히터")),
    ("FAN_BLOWER", "팬·블로워류", ("fan", "blower", "팬", "블로워")),
    ("IT_EQUIPMENT", "컴퓨터·서버류", ("industrial pc", "computer", "server", "monitor", "컴퓨터", "서버", "모니터")),
    ("NETWORK_SOFTWARE", "네트워크·소프트웨어류", ("network", "ethernet", "software", "program license", "네트워크", "소프트웨어")),
    ("LABOR_INSTALL", "노무·설치비류", ("labor", "installation", "install work", "commission", "노무", "설치", "시운전", "인건비", "공사비")),
    ("DESIGN_PROGRAM", "설계·프로그램비류", ("design work", "engineer", "programming", "설계", "프로그램")),
    ("CONSUMABLE", "소모품류", ("tape", "grease", "o ring", "packing", "소모품", "테이프", "그리스", "오링", "패킹")),
)

_CATEGORY_NAMES = {
    "DRIVE_MOTION": "구동·모션 기타류",
    "SENSOR_MEASUREMENT": "센서·계측 기타류",
    "ELECTRICAL_CONTROL": "전장·제어 기타류",
    "PNEUMATIC_HYDRAULIC": "공압·유압 기타류",
    "MATERIAL_HANDLING": "이송·물류 기타류",
    "MECHANICAL_FABRICATION": "기계·제작 기타류",
    "TOOLING_FIXTURE": "치공구·금형 기타류",
    "UTILITY_ENVIRONMENT": "유틸리티·환경 기타류",
    "CABLE_CONNECTOR": "케이블·커넥터 기타류",
    "FASTENER_CONSUMABLE": "체결·소모품 기타류",
    "SAFETY": "안전·보호 기타류",
    "IT_NETWORK": "IT·네트워크 기타류",
    "LABOR_SERVICE": "노무·설치 기타류",
    "GENERAL_COMPONENT": "공통 설비·부품류",
}


def classify_item_family(
    name: str | None,
    spec: str | None,
    category_code: str | None,
) -> FamilyMatch:
    text = normalize_search_text(f"{name or ''} {spec or ''}")
    for code, label, terms in _RULES:
        for term in terms:
            normalized_term = normalize_search_text(term)
            if normalized_term and normalized_term in text:
                return FamilyMatch(code, label, term)
    fallback = category_code or "GENERAL_COMPONENT"
    return FamilyMatch(
        f"OTHER_{fallback}",
        _CATEGORY_NAMES.get(fallback, "공통 설비·부품류"),
        None,
    )


def _price_payload(values: list[Decimal]) -> dict[str, str] | None:
    if not values:
        return None
    return {
        "minimum": str(min(values)),
        "median": str(Decimal(str(median(values)))),
        "average": str((sum(values) / Decimal(len(values))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "maximum": str(max(values)),
    }


def item_family_projection(session: Session) -> list[dict[str, object]]:
    run_id = session.scalar(
        select(StandardDatabaseBuildRun.id)
        .where(StandardDatabaseBuildRun.status == StandardBuildStatus.SUCCEEDED)
        .order_by(StandardDatabaseBuildRun.finished_at.desc(), StandardDatabaseBuildRun.id.desc())
        .limit(1)
    )
    if run_id is None:
        return []
    item_rows = session.execute(
        select(
            StandardDatabaseBuildProjection.standard_item_id,
            StandardItemVersion.canonical_name,
            StandardItemVersion.canonical_spec,
            StandardItemVersion.canonical_unit,
            StandardPriceVersion.minimum_price,
            StandardPriceVersion.median_price,
            StandardPriceVersion.average_price,
            StandardPriceVersion.maximum_price,
            StandardPriceVersion.observation_count,
        )
        .join(StandardItemVersion, StandardItemVersion.id == StandardDatabaseBuildProjection.standard_item_version_id)
        .join(StandardPriceVersion, StandardPriceVersion.id == StandardDatabaseBuildProjection.standard_price_version_id)
        .where(
            StandardDatabaseBuildProjection.build_run_id == run_id,
            StandardDatabaseBuildProjection.operational_status == StandardOperationalStatus.ACTIVE,
        )
    ).all()

    families: dict[str, dict[str, object]] = {}
    item_to_family: dict[int, str] = {}
    member_context: dict[int, dict[str, object]] = {}
    for row in item_rows:
        match = classify_item_family(row.canonical_name, row.canonical_spec, None)
        item_to_family[row.standard_item_id] = match.code
        family = families.setdefault(match.code, {
            "code": match.code,
            "name": match.name,
            "rule_version": FAMILY_RULE_VERSION,
            "category_codes": set(),
            "category_names": set(),
            "members": [],
            "prices": [],
            "suppliers": set(),
            "dates": [],
            "years": defaultdict(list),
            "undated_observation_count": 0,
        })
        family["members"].append({
            "standard_item_id": row.standard_item_id,
            "name": row.canonical_name,
            "spec": row.canonical_spec,
            "unit": row.canonical_unit,
            "observation_count": row.observation_count,
            "price": {
                "minimum": str(row.minimum_price),
                "median": str(row.median_price),
                "average": str(row.average_price),
                "maximum": str(row.maximum_price),
            },
        })
        member_context[row.standard_item_id] = {
            "makers": set(),
            "suppliers": set(),
            "dates": [],
            "undated_observation_count": 0,
            "observations": [],
        }

    observations = session.execute(
        select(
            StandardDatabaseBuildProjection.standard_item_id,
            StandardPriceObservation.raw_item_id,
            CleanDecision.unit_price,
            CleanDecision.maker_norm,
            DocumentMetadataVersion.quote_date,
            DocumentMetadataVersion.supplier_name,
            RawQuoteItem.source_sheet,
            RawQuoteItem.source_row,
            SourceVariant.id.label("source_variant_id"),
            SourceDocument.logical_name,
        )
        .join(StandardPriceObservation, StandardPriceObservation.standard_price_version_id == StandardDatabaseBuildProjection.standard_price_version_id)
        .join(CleanDecision, CleanDecision.id == StandardPriceObservation.clean_decision_id)
        .outerjoin(DocumentMetadataVersion, DocumentMetadataVersion.id == StandardPriceObservation.metadata_version_id)
        .join(RawQuoteItem, RawQuoteItem.id == StandardPriceObservation.raw_item_id)
        .join(SourceVariant, SourceVariant.id == RawQuoteItem.source_variant_id)
        .join(SourceDocument, SourceDocument.id == SourceVariant.document_id)
        .where(
            StandardDatabaseBuildProjection.build_run_id == run_id,
            StandardDatabaseBuildProjection.operational_status == StandardOperationalStatus.ACTIVE,
        )
    ).all()
    seen_raw_ids: set[int] = set()
    for row in observations:
        if row.raw_item_id in seen_raw_ids or row.standard_item_id not in item_to_family:
            continue
        seen_raw_ids.add(row.raw_item_id)
        family = families[item_to_family[row.standard_item_id]]
        context = member_context[row.standard_item_id]
        family["prices"].append(row.unit_price)
        if row.maker_norm:
            context["makers"].add(row.maker_norm.strip())
        if row.supplier_name:
            family["suppliers"].add(normalize_search_text(row.supplier_name))
            context["suppliers"].add(row.supplier_name.strip())
        if row.quote_date is None:
            family["undated_observation_count"] += 1
            context["undated_observation_count"] += 1
        else:
            family["dates"].append(row.quote_date)
            family["years"][row.quote_date.year].append(row.unit_price)
            context["dates"].append(row.quote_date)
        context["observations"].append({
            "raw_item_id": row.raw_item_id,
            "unit_price": str(row.unit_price),
            "maker": row.maker_norm,
            "supplier": row.supplier_name,
            "quote_date": None if row.quote_date is None else row.quote_date.isoformat(),
            "source_variant_id": row.source_variant_id,
            "source_logical_name": row.logical_name,
            "source_sheet": row.source_sheet,
            "source_row": row.source_row,
        })

    output: list[dict[str, object]] = []
    for family in families.values():
        members = []
        for member in family["members"]:
            context = member_context[member["standard_item_id"]]
            member_dates: list[date] = context["dates"]
            members.append({
                **member,
                "maker_summary": sorted(context["makers"], key=normalize_search_text),
                "supplier_summary": sorted(context["suppliers"], key=normalize_search_text),
                "quote_date_start": min(member_dates).isoformat() if member_dates else None,
                "quote_date_end": max(member_dates).isoformat() if member_dates else None,
                "undated_observation_count": context["undated_observation_count"],
                "observations": context["observations"],
            })
        members.sort(key=lambda row: (normalize_search_text(row["name"]), normalize_search_text(row["spec"])))
        dates: list[date] = family["dates"]
        trend = [
            {"year": year, **(_price_payload(values) or {}), "observation_count": len(values)}
            for year, values in sorted(family["years"].items())
        ]
        output.append({
            "code": family["code"],
            "name": family["name"],
            "rule_version": family["rule_version"],
            "category_codes": sorted(family["category_codes"]),
            "category_names": sorted(family["category_names"]),
            "item_count": len(members),
            "observation_count": len(family["prices"]),
            "supplier_count": len(family["suppliers"]),
            "year_count": len(family["years"]),
            "quote_date_start": min(dates).isoformat() if dates else None,
            "quote_date_end": max(dates).isoformat() if dates else None,
            "undated_observation_count": family["undated_observation_count"],
            "price": _price_payload(family["prices"]),
            "trend": trend,
            "members": members,
        })
    return sorted(output, key=lambda family: (-family["observation_count"], family["name"]))


def list_item_families(
    session: Session,
    *,
    search: str | None = None,
    category_code: str | None = None,
) -> list[dict[str, object]]:
    families = item_family_projection(session)
    needle = normalize_search_text(search)
    result = []
    for family in families:
        if category_code and category_code not in family["category_codes"]:
            continue
        if needle:
            haystack = normalize_search_text(" ".join([
                family["name"],
                *[member["name"] or "" for member in family["members"]],
                *[member["spec"] or "" for member in family["members"]],
            ]))
            if needle not in haystack:
                continue
        result.append(family)
    return result


def get_item_family(session: Session, code: str) -> dict[str, object]:
    for family in item_family_projection(session):
        if family["code"] == code:
            return family
    raise LookupError("품목을 찾을 수 없습니다.")
