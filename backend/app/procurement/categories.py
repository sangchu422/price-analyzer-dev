"""Deterministic, deliberately broad category assignment for the prototype."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.catalog.models import StandardItemVersion
from app.matching.normalization import normalize_search_text
from app.procurement.models import ItemCategory, StandardItemCategoryAssignment


CATEGORY_RULE_VERSION = "category-keyword-v2"

_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("LABOR_SERVICE", ("labor", "installation", "install work", "commission", "design work", "engineer", "programming", "노무", "설치", "시운전", "설계", "인건비", "공사비")),
    ("SENSOR_MEASUREMENT", ("sensor", "encoder", "gauge", "meter", "tester", "vision", "camera", "scanner", "barcode", "scale", "load cell", "torque", "probe", "calibration", "센서", "계측", "시험", "검사", "스캐너", "측정")),
    ("IT_NETWORK", ("computer", "server", "network", "ethernet", "software", "monitor", "cpu", "industrial pc", "컴퓨터", "네트워크", "서버", "소프트웨어")),
    ("SAFETY", ("safety", "cover", "guard", "fence", "light curtain", "door lock", "안전", "커버", "가드", "펜스", "방호")),
    ("CABLE_CONNECTOR", ("cable", "wire", "harness", "connector", "terminal", "bus bar", "케이블", "전선", "하네스", "커넥터", "단자")),
    ("ELECTRICAL_CONTROL", ("plc", "relay", "inverter", "invertor", "breaker", "panel", "controller", "control", "smps", "ups", "avr", "amplifier", "electric", "power supply", "switch", "buzzer", "transformer", "board", "card", "hmi", "nfb", "mccb", "elcb", "전장", "제어", "차단기", "판넬", "인버터", "전원")),
    ("PNEUMATIC_HYDRAULIC", ("valve", "pneumatic", "hydraulic", "regulator", "pump", "fitting", "vacuum", "cylinder", "solenoid", "manifold", "공압", "유압", "밸브", "펌프", "실린더")),
    ("MATERIAL_HANDLING", ("conveyor", "loader", "unloader", "shuttle", "feeder", "transfer", "pallet", "stocker", "chain", "sprocket", "chute", "lift", "hoist", "hanger", "carrier", "roller", "stopper", "agv", "컨베이어", "이송", "적재", "팔레트")),
    ("DRIVE_MOTION", ("motor", "servo", "gear", "reducer", "actuator", "robot", "linear motion", "ball screw", "모터", "감속", "구동", "로봇")),
    ("FASTENER_CONSUMABLE", ("bolt", "nut", "screw", "bearing", "washer", "tape", "grease", "o ring", "packing", "볼트", "너트", "베어링", "소모품", "와셔")),
    ("UTILITY_ENVIRONMENT", ("duct", "pipe", "filter", "tank", "fan", "heater", "cooler", "chiller", "blower", "nozzle", "exhaust", "coolant", "hose", "silencer", "집진", "덕트", "배관", "필터", "탱크", "냉각")),
    ("TOOLING_FIXTURE", ("jig", "fixture", "chuck", "gripper", "clamp", "anvil", "collet", "die", "mold", "tooling", "지그", "치구", "금형", "척", "클램프")),
    ("MECHANICAL_FABRICATION", ("frame", "bracket", "plate", "shaft", "machin", "fabricat", "base", "bed", "rack", "block", "belt", "bush", "joint", "table", "guide", "spring", "profile", "angle", "box", "support", "post", "bar", "pin", "ring", "steel", "sus", "가공", "제작", "프레임", "구조물", "베이스", "장치")),
)


@dataclass(frozen=True)
class CategoryMatch:
    code: str
    confidence: Decimal
    matched_terms: tuple[str, ...]


def classify_category(name: str | None, spec: str | None) -> CategoryMatch:
    text = normalize_search_text(f"{name or ''} {spec or ''}")
    for code, terms in _RULES:
        matched = tuple(term for term in terms if normalize_search_text(term) in text)
        if matched:
            return CategoryMatch(code, Decimal("88"), matched)
    # The source corpus is industrial-equipment heavy. Ambiguous rows are kept
    # separate at item level and receive only a low-confidence navigation tag;
    # this never merges prices or evidence.
    return CategoryMatch("GENERAL_COMPONENT", Decimal("25"), ())


def assign_current_category(
    session: Session,
    standard_item_id: int,
    *,
    assigned_by: str,
) -> StandardItemCategoryAssignment:
    version = session.scalar(
        select(StandardItemVersion)
        .where(StandardItemVersion.standard_item_id == standard_item_id)
        .order_by(StandardItemVersion.id.desc())
        .limit(1)
    )
    if version is None:
        raise ValueError("표준 품목 버전을 찾을 수 없습니다.")
    current = session.scalar(
        select(StandardItemCategoryAssignment)
        .where(StandardItemCategoryAssignment.standard_item_id == standard_item_id)
        .order_by(StandardItemCategoryAssignment.id.desc())
        .limit(1)
    )
    match = classify_category(version.canonical_name, version.canonical_spec)
    category = session.scalar(select(ItemCategory).where(ItemCategory.code == match.code))
    if category is None:
        raise ValueError("품목 분류 기준이 초기화되지 않았습니다.")
    if current is not None and current.category_id == category.id:
        return current
    assignment = StandardItemCategoryAssignment(
        standard_item_id=standard_item_id,
        category_id=category.id,
        confidence=match.confidence,
        method=CATEGORY_RULE_VERSION,
        evidence_json=json.dumps(
            {
                "standard_item_version_id": version.id,
                "matched_terms": match.matched_terms,
                "fallback": not match.matched_terms,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        supersedes_assignment_id=None if current is None else current.id,
        assigned_by=assigned_by,
    )
    session.add(assignment)
    session.flush()
    return assignment


def current_category_subquery(*, name: str = "current_category_assignment"):
    latest = (
        select(
            StandardItemCategoryAssignment.standard_item_id,
            func.max(StandardItemCategoryAssignment.id).label("assignment_id"),
        )
        .group_by(StandardItemCategoryAssignment.standard_item_id)
        .subquery(f"{name}_latest")
    )
    return (
        select(
            StandardItemCategoryAssignment.standard_item_id,
            StandardItemCategoryAssignment.category_id,
            StandardItemCategoryAssignment.confidence,
            StandardItemCategoryAssignment.method,
        )
        .join(latest, latest.c.assignment_id == StandardItemCategoryAssignment.id)
        .subquery(name)
    )
