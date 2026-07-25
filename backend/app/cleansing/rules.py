"""Deterministic, evidence-preserving quote cleansing rules."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
import unicodedata

from app.cleansing.models import CleanStatus


RULE_VERSION = "clean-v1"
OUTLIER_RULE_VERSION = "outlier-mad-v1"
_MAD_SCALE = Decimal("0.6745")
_MAD_THRESHOLD = Decimal("3.5")
_CURRENCY_EDGE = re.compile(
    r"^(?:(?:KRW|WON)\s*|[₩￦$]\s*)|"
    r"(?:\s*(?:KRW|WON|원)|\s*[₩￦$])$",
    re.IGNORECASE,
)
_PLAIN_NUMBER = re.compile(
    r"^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?$"
)
_SUMMARY_NAMES = {
    "합계",
    "소계",
    "총계",
    "일반관리비",
    "관리비",
    "인건비",
    "경비",
    "이윤",
    "노무비",
}
_KNOWN_UNITS = {
    "EA",
    "PC",
    "PCS",
    "SET",
    "LOT",
    "BOX",
    "PACK",
    "ROLL",
    "UNIT",
    "M",
    "MM",
    "CM",
    "KM",
    "M2",
    "M3",
    "G",
    "KG",
    "T",
    "ML",
    "L",
    "H",
    "HR",
    "DAY",
    "MONTH",
    "MAN DAY",
    "PERSON DAY",
    "개",
    "식",
    "대",
    "장",
    "조",
    "본",
    "병",
    "통",
    "회",
    "인/일",
}
_ITEMISH_UNIT_TERMS = {
    "MOTOR",
    "SERVO",
    "BEARING",
    "SENSOR",
    "CABLE",
    "SWITCH",
    "VALVE",
    "PUMP",
    "PANEL",
    "모터",
    "베어링",
    "센서",
    "케이블",
    "스위치",
    "밸브",
    "펌프",
    "패널",
}


@dataclass(frozen=True)
class ParsedNumber:
    value: Decimal | None
    supplied: bool


@dataclass(frozen=True)
class Evaluation:
    status: CleanStatus
    reason_code: str
    reason_detail: str | None
    item_name_norm: str | None
    spec_norm: str | None
    unit_norm: str | None
    maker_norm: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None


def normalize_text(value: str | None) -> str:
    """Normalize presentation differences without inferring semantics."""
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"\s*([/\-])\s*", r"\1", normalized)
    return normalized.upper()


def parse_number(value: str | None) -> ParsedNumber:
    """Parse common quote number forms without accepting ambiguous text."""
    if value is None:
        return ParsedNumber(None, False)
    normalized = unicodedata.normalize("NFKC", str(value)).strip()
    if not normalized:
        return ParsedNumber(None, False)

    negative_parentheses = (
        normalized.startswith("(") and normalized.endswith(")")
    )
    if negative_parentheses:
        normalized = normalized[1:-1].strip()
    elif normalized.startswith("(") or normalized.endswith(")"):
        return ParsedNumber(None, True)

    previous = None
    while normalized != previous:
        previous = normalized
        normalized = _CURRENCY_EDGE.sub("", normalized).strip()
    normalized = re.sub(r"^([+-])\s+", r"\1", normalized)
    normalized = re.sub(r"(?<=\d)\s+(?=\d)", "", normalized)
    normalized = normalized.replace("\u00a0", "")
    if not _PLAIN_NUMBER.fullmatch(normalized):
        return ParsedNumber(None, True)
    try:
        parsed = Decimal(normalized.replace(",", ""))
    except InvalidOperation:
        return ParsedNumber(None, True)
    if not parsed.is_finite() or parsed.as_tuple().exponent < -6:
        return ParsedNumber(None, True)
    if negative_parentheses:
        parsed = -parsed
    return ParsedNumber(parsed, True)


def evaluate(raw: object) -> Evaluation:
    name = normalize_text(getattr(raw, "item_name_raw", None))
    spec = normalize_text(getattr(raw, "spec_raw", None))
    unit = normalize_text(getattr(raw, "unit_raw", None))
    maker = normalize_text(getattr(raw, "maker_raw", None))
    quantity = parse_number(getattr(raw, "quantity_raw", None))
    unit_price = parse_number(getattr(raw, "unit_price_raw", None))
    amount = parse_number(getattr(raw, "amount_raw", None))
    common = {
        "item_name_norm": name or None,
        "spec_norm": spec or None,
        "unit_norm": unit or None,
        "maker_norm": maker or None,
        "quantity": quantity.value,
        "unit_price": unit_price.value,
        "amount": amount.value,
    }

    if not name:
        return Evaluation(
            CleanStatus.EXCLUDED,
            "MISSING_ITEM_NAME",
            None,
            **common,
        )
    if unit_price.value is None or unit_price.value <= 0:
        return Evaluation(
            CleanStatus.EXCLUDED,
            "INVALID_UNIT_PRICE",
            _invalid_detail("unit_price", unit_price),
            **common,
        )
    if _is_summary_or_fee(name):
        return Evaluation(
            CleanStatus.EXCLUDED,
            "SUMMARY_OR_FEE_LINE",
            None,
            **common,
        )
    structural_detail = _structural_anomaly(name, unit)
    if structural_detail is not None:
        return Evaluation(
            CleanStatus.REVIEW_REQUIRED,
            "COLUMN_SHIFT_SUSPECTED",
            structural_detail,
            **common,
        )
    if quantity.supplied and (
        quantity.value is None or quantity.value <= 0
    ):
        return Evaluation(
            CleanStatus.REVIEW_REQUIRED,
            "INVALID_QUANTITY",
            _invalid_detail("quantity", quantity),
            **common,
        )
    if amount.supplied and (amount.value is None or amount.value <= 0):
        return Evaluation(
            CleanStatus.REVIEW_REQUIRED,
            "INVALID_AMOUNT",
            _invalid_detail("amount", amount),
            **common,
        )
    if (
        quantity.value is not None
        and amount.value is not None
        and abs(quantity.value * unit_price.value - amount.value)
        > max(Decimal("1"), abs(amount.value) * Decimal("0.01"))
    ):
        return Evaluation(
            CleanStatus.REVIEW_REQUIRED,
            "AMOUNT_MISMATCH",
            (
                f"{quantity.value} * {unit_price.value} "
                f"!= {amount.value}"
            ),
            **common,
        )
    return Evaluation(CleanStatus.INCLUDED, "VALID", None, **common)


def mad_outlier_ids(rows: list[tuple[int, Decimal]]) -> set[int]:
    """Return robust price outliers within one already-normalized group."""
    if len(rows) < 3:
        return set()
    values = sorted(value for _, value in rows)
    median = _median(values)
    deviations = sorted(abs(value - median) for value in values)
    mad = _median(deviations)
    if mad == 0:
        return {
            row_id
            for row_id, value in rows
            if value != median
        }
    return {
        row_id
        for row_id, value in rows
        if _MAD_SCALE * abs(value - median) / mad > _MAD_THRESHOLD
    }


def _median(values: list[Decimal]) -> Decimal:
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return (values[midpoint - 1] + values[midpoint]) / Decimal("2")


def _is_summary_or_fee(normalized_name: str) -> bool:
    collapsed = "".join(
        character
        for character in normalized_name
        if not (
            character.isspace()
            or unicodedata.category(character).startswith(("P", "S"))
        )
    )
    return collapsed in _SUMMARY_NAMES


def _invalid_detail(field: str, parsed: ParsedNumber) -> str:
    state = "unparseable" if parsed.supplied else "missing"
    return f"{field} is {state} or nonpositive"


def _structural_anomaly(name: str, unit: str) -> str | None:
    parsed_name = parse_number(name)
    if parsed_name.value is not None:
        return "item_name contains only a numeric value"
    if not unit or unit in _KNOWN_UNITS:
        return None
    unit_terms = set(re.findall(r"[A-Z]+|[가-힣]+", unit))
    matched_terms = sorted(unit_terms & _ITEMISH_UNIT_TERMS)
    if matched_terms:
        return (
            "unit resembles an item-name column; "
            f"matched_terms={matched_terms!r}"
        )
    return None
