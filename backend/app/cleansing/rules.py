"""Deterministic, evidence-preserving quote cleansing rules."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
import re
import unicodedata

from app.cleansing.models import CleanStatus
from app.db.types import (
    EXACT_DECIMAL_FRACTIONAL_DIGITS,
    EXACT_DECIMAL_MAX,
)


RULE_VERSION = "clean-v1"
OUTLIER_RULE_VERSION = "outlier-mad-v1"
_MAD_SCALE = Decimal("0.6745")
_MAD_THRESHOLD = Decimal("3.5")
ZERO_MAD_MIN_ABSOLUTE_DELTA = Decimal("1")
ZERO_MAD_MIN_RELATIVE_DELTA = Decimal("0.20")
_MAX_NUMERIC_DIGITS = 64
_CURRENCY_EDGE = re.compile(
    r"^(?:(?:KRW|WON)\s*|[₩￦$]\s*)|"
    r"(?:\s*(?:KRW|WON|원)|\s*[₩￦$])$",
    re.IGNORECASE,
)
_PLAIN_NUMBER = re.compile(
    r"^[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)"
    r"(?:\.[0-9]+)?$"
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
    "부가세",
    "부가가치세",
    "VAT",
    "공급가액",
    "총액",
    "총합계",
    "합계금액",
    "합계액",
    "운반비",
    "운송비",
    "배송비",
    "설치비",
    "시공비",
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
    issue: str | None = None
    nonpositive_hint: bool = False


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
        return ParsedNumber(None, True, "INVALID_FORMAT")

    previous = None
    while normalized != previous:
        previous = normalized
        normalized = _CURRENCY_EDGE.sub("", normalized).strip()
    normalized = re.sub(r"^([+-])\s+", r"\1", normalized)
    normalized = re.sub(r"(?<=\d)\s+(?=\d)", "", normalized)
    normalized = normalized.replace("\u00a0", "")
    if not _PLAIN_NUMBER.fullmatch(normalized):
        return ParsedNumber(None, True, "INVALID_FORMAT")
    compact = normalized.replace(",", "")
    digit_count = sum(character.isdigit() for character in compact)
    has_nonzero_digit = any(
        character in "123456789"
        for character in compact
    )
    nonpositive_hint = (
        compact.startswith("-")
        or negative_parentheses
        or not has_nonzero_digit
    )
    if digit_count > _MAX_NUMERIC_DIGITS:
        return ParsedNumber(
            None,
            True,
            "TOO_MANY_DIGITS",
            nonpositive_hint,
        )
    try:
        parsed = Decimal(compact)
    except InvalidOperation:
        return ParsedNumber(None, True, "INVALID_FORMAT")
    if negative_parentheses:
        parsed = -parsed
    if not parsed.is_finite():
        return ParsedNumber(None, True, "INVALID_FORMAT")
    if parsed <= 0:
        if (
            parsed.as_tuple().exponent
            < -EXACT_DECIMAL_FRACTIONAL_DIGITS
        ):
            return ParsedNumber(
                None,
                True,
                "EXCESSIVE_SCALE",
                True,
            )
        if abs(parsed) > EXACT_DECIMAL_MAX:
            return ParsedNumber(
                None,
                True,
                "OUT_OF_RANGE",
                True,
            )
        return ParsedNumber(parsed, True, nonpositive_hint=True)
    if (
        parsed.as_tuple().exponent
        < -EXACT_DECIMAL_FRACTIONAL_DIGITS
    ):
        return ParsedNumber(None, True, "EXCESSIVE_SCALE")
    if parsed > EXACT_DECIMAL_MAX:
        return ParsedNumber(None, True, "OUT_OF_RANGE")
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
    if (
        not unit_price.supplied
        or unit_price.issue == "INVALID_FORMAT"
        or unit_price.nonpositive_hint
        or (
            unit_price.value is not None
            and unit_price.value <= 0
        )
    ):
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
    numeric_issue = _numeric_storage_issue(
        unit_price=unit_price,
        quantity=quantity,
        amount=amount,
    )
    if numeric_issue is not None:
        field, issue = numeric_issue
        return Evaluation(
            CleanStatus.REVIEW_REQUIRED,
            "NUMERIC_OUT_OF_RANGE",
            (
                f"{field} cannot be represented by ExactDecimal; "
                f"constraint={issue}; max={EXACT_DECIMAL_MAX}; "
                f"scale={EXACT_DECIMAL_FRACTIONAL_DIGITS}"
            ),
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
        and _amount_mismatch(
            quantity.value,
            unit_price.value,
            amount.value,
        )
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
    """Return robust price outliers within one normalized positive-price group.

    A zero MAD is common with repeated quote prices. Such a group only flags
    a value when its delta is strictly greater than both one price unit and
    twenty percent of the positive median, preventing rounding noise from
    becoming a review item.
    """
    if len(rows) < 3:
        return set()
    values = sorted(value for _, value in rows)
    median = decimal_median(values)
    deviations = sorted(abs(value - median) for value in values)
    mad = decimal_median(deviations)
    if mad == 0:
        if median <= 0:
            return set()
        return {
            row_id
            for row_id, value in rows
            if (
                abs(value - median)
                > ZERO_MAD_MIN_ABSOLUTE_DELTA
                and abs(value - median) / median
                > ZERO_MAD_MIN_RELATIVE_DELTA
            )
        }
    return {
        row_id
        for row_id, value in rows
        if _MAD_SCALE * abs(value - median) / mad > _MAD_THRESHOLD
    }


def decimal_median(values: list[Decimal]) -> Decimal:
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


def _numeric_storage_issue(
    **numbers: ParsedNumber,
) -> tuple[str, str] | None:
    storage_issues = {
        "TOO_MANY_DIGITS",
        "EXCESSIVE_SCALE",
        "OUT_OF_RANGE",
    }
    return next(
        (
            (field, parsed.issue)
            for field, parsed in numbers.items()
            if parsed.issue in storage_issues
        ),
        None,
    )


def _amount_mismatch(
    quantity: Decimal,
    unit_price: Decimal,
    amount: Decimal,
) -> bool:
    with localcontext() as context:
        context.prec = 64
        expected = quantity * unit_price
        tolerance = max(
            Decimal("1"),
            abs(amount) * Decimal("0.01"),
        )
        return abs(expected - amount) > tolerance


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
