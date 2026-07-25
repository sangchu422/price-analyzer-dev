from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cleansing.models import CleanDecision, CleanStatus
from app.cleansing.rules import evaluate, normalize_text, parse_number
from app.cleansing.service import apply_rules, current_decision
from app.db.immutability import ImmutableEvidenceError
from app.db.types import EXACT_DECIMAL_MAX
from app.documents.models import SourceDocument


@pytest.mark.parametrize(
    ("item_name", "unit_price", "expected_reason"),
    [
        ("", "1000", "MISSING_ITEM_NAME"),
        ("SERVO MOTOR", "0", "INVALID_UNIT_PRICE"),
        ("SERVO MOTOR", "-1", "INVALID_UNIT_PRICE"),
        ("SERVO MOTOR", "not a price", "INVALID_UNIT_PRICE"),
    ],
)
def test_automatic_exclusion_priority(
    make_raw,
    item_name: str,
    unit_price: str,
    expected_reason: str,
) -> None:
    result = evaluate(
        make_raw(item_name=item_name, unit_price=unit_price)
    )

    assert result.status is CleanStatus.EXCLUDED
    assert result.reason_code == expected_reason


def test_missing_name_has_priority_over_invalid_price(make_raw) -> None:
    result = evaluate(make_raw(item_name=" ", unit_price="0"))

    assert result.reason_code == "MISSING_ITEM_NAME"


@pytest.mark.parametrize(
    "item_name",
    [
        "합계",
        " 합 계 : ",
        "[소계]",
        "총-계",
        "일반관리비",
        "일반 관리비",
        "관리비.",
        "인 건 비",
        "(경비)",
        "이 윤",
        "(이윤)",
        "노 무 비",
        "부 가 세",
        "부가 가치세",
        "(V.A.T)",
        "공급 가액",
        "총 액",
        "총 합계",
        "합계 금액",
        "합계 액",
        "운반 비",
        "운송 비",
        "배송 비",
        "설치 비",
        "시공 비",
    ],
)
def test_summary_and_fee_variants_are_excluded(
    make_raw,
    item_name: str,
) -> None:
    result = evaluate(make_raw(item_name=item_name, unit_price="100000"))

    assert result.status is CleanStatus.EXCLUDED
    assert result.reason_code == "SUMMARY_OR_FEE_LINE"


def test_invalid_price_has_priority_over_summary_line(make_raw) -> None:
    result = evaluate(make_raw(item_name="합계", unit_price="0"))

    assert result.reason_code == "INVALID_UNIT_PRICE"


@pytest.mark.parametrize(
    "item_name",
    [
        "이윤조정기",
        "노무비계산기",
        "경비행기",
        "부가세표시기",
        "VAT SENSOR",
        "공급가액계산기",
        "총액계",
        "합계금액표",
        "운반비용",
        "설치비계산기",
    ],
)
def test_summary_terms_do_not_match_legitimate_item_substrings(
    make_raw,
    item_name: str,
) -> None:
    result = evaluate(make_raw(item_name=item_name))

    assert result.status is CleanStatus.INCLUDED
    assert result.reason_code == "VALID"


@pytest.mark.parametrize(
    "item_name",
    [
        "12345",
        "1,234",
        " 100.25 ",
        "9223372036854775808",
        "9" * 65,
        "1.0000001",
    ],
)
def test_numeric_only_item_name_requires_structural_review(
    make_raw,
    item_name: str,
) -> None:
    result = evaluate(make_raw(item_name=item_name))

    assert result.status is CleanStatus.REVIEW_REQUIRED
    assert result.reason_code == "COLUMN_SHIFT_SUSPECTED"
    assert "item_name" in result.reason_detail


@pytest.mark.parametrize(
    "item_name",
    ["6204 BEARING", "3M TAPE", "A100", "SENSOR 100"],
)
def test_alphanumeric_item_names_are_not_column_shift_false_positives(
    make_raw,
    item_name: str,
) -> None:
    result = evaluate(make_raw(item_name=item_name))

    assert result.status is CleanStatus.INCLUDED


@pytest.mark.parametrize(
    "unit",
    ["SERVO MOTOR", "BEARING", "베어링 6204", "모터"],
)
def test_item_like_unit_requires_structural_review(
    make_raw,
    unit: str,
) -> None:
    result = evaluate(make_raw(unit=unit))

    assert result.status is CleanStatus.REVIEW_REQUIRED
    assert result.reason_code == "COLUMN_SHIFT_SUSPECTED"
    assert "unit" in result.reason_detail


@pytest.mark.parametrize(
    "unit",
    [
        "EA",
        "SET",
        "식",
        "대",
        "M",
        "KG",
        "개",
        "BOX",
        "ROLL",
        "M2",
        "m³",
        "인/일",
    ],
)
def test_normal_units_do_not_trigger_structural_review(
    make_raw,
    unit: str,
) -> None:
    result = evaluate(make_raw(unit=unit))

    assert result.status is CleanStatus.INCLUDED


def test_invalid_price_has_priority_over_structural_anomaly(make_raw) -> None:
    result = evaluate(make_raw(item_name="12345", unit_price="0"))

    assert result.status is CleanStatus.EXCLUDED
    assert result.reason_code == "INVALID_UNIT_PRICE"


def test_amount_mismatch_requires_review(make_raw) -> None:
    result = evaluate(
        make_raw(
            item_name="BEARING",
            quantity="2",
            unit_price="1000",
            amount="9000",
        )
    )

    assert result.status is CleanStatus.REVIEW_REQUIRED
    assert result.reason_code == "AMOUNT_MISMATCH"


def test_amount_tolerance_is_exact_and_inclusive(make_raw) -> None:
    inside = evaluate(
        make_raw(
            source_row=1,
            quantity="3",
            unit_price="333.333333",
            amount="1000",
        )
    )
    outside = evaluate(
        make_raw(
            source_row=2,
            quantity="3",
            unit_price="333.333333",
            amount="1011",
        )
    )

    assert inside.status is CleanStatus.INCLUDED
    assert inside.amount == Decimal("1000")
    assert outside.status is CleanStatus.REVIEW_REQUIRED


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("₩ 1,234.50", Decimal("1234.50")),
        ("1 234 원", Decimal("1234")),
        ("KRW\u00a01,234", Decimal("1234")),
        ("$ 12.25", Decimal("12.25")),
        ("(1,234.5)", Decimal("-1234.5")),
        (" + 1,000 ", Decimal("1000")),
    ],
)
def test_common_quote_numbers_are_parsed_exactly(
    raw: str,
    expected: Decimal,
) -> None:
    assert parse_number(raw).value == expected


@pytest.mark.parametrize(
    "raw",
    ["1,2,3", "12.3.4", "1e3", "약 1000", "1000-2000", "(1000", "NaN"],
)
def test_ambiguous_numbers_are_not_invented(raw: str) -> None:
    parsed = parse_number(raw)

    assert parsed.value is None
    assert parsed.supplied


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("quantity", "두 개", "INVALID_QUANTITY"),
        ("amount", "약 2,000", "INVALID_AMOUNT"),
    ],
)
def test_ambiguous_non_price_numbers_require_review(
    make_raw,
    field: str,
    value: str,
    reason: str,
) -> None:
    kwargs = {field: value}
    result = evaluate(make_raw(**kwargs))

    assert result.status is CleanStatus.REVIEW_REQUIRED
    assert result.reason_code == reason


def test_normalization_is_deterministic_without_semantic_guessing(make_raw) -> None:
    result = evaluate(
        make_raw(
            item_name="  servo　 motor  ",
            spec=" AB -  10 / 20 ",
            unit=" ea ",
            maker=" Acme  Corp. ",
        )
    )

    assert result.item_name_norm == "SERVO MOTOR"
    assert result.spec_norm == "AB-10/20"
    assert result.unit_norm == "EA"
    assert result.maker_norm == "ACME CORP."
    assert normalize_text("ＡＢＣ　motor") == "ABC MOTOR"


def test_apply_rules_persists_exact_decimals_without_committing(
    session: Session,
    make_raw,
) -> None:
    raw = make_raw(
        quantity="3",
        unit_price="₩ 333.333333",
        amount="1000",
    )

    decision = apply_rules(session, raw)

    assert decision.quantity == Decimal("3")
    assert decision.unit_price == Decimal("333.333333")
    assert decision.amount == Decimal("1000")
    assert session.in_transaction()
    session.rollback()
    assert session.scalar(select(func.count(CleanDecision.id))) == 0


def test_exact_decimal_max_boundary_persists(
    session: Session,
    make_raw,
) -> None:
    raw = make_raw(
        quantity="1",
        unit_price=str(EXACT_DECIMAL_MAX),
        amount=str(EXACT_DECIMAL_MAX),
    )

    decision = apply_rules(session, raw)

    assert decision.status is CleanStatus.INCLUDED
    assert decision.unit_price == EXACT_DECIMAL_MAX
    assert decision.amount == EXACT_DECIMAL_MAX


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("unit_price", "9223372036854.775808"),
        ("quantity", "9223372036854.775808"),
        ("amount", "9223372036854.775808"),
        ("unit_price", "1.0000001"),
        ("amount", "9" * 10_000),
    ],
)
def test_unsupported_exact_decimal_values_require_review_without_overflow(
    session: Session,
    make_raw,
    field: str,
    value: str,
) -> None:
    raw = make_raw(**{field: value})

    decision = apply_rules(session, raw)

    assert decision.status is CleanStatus.REVIEW_REQUIRED
    assert decision.reason_code == "NUMERIC_OUT_OF_RANGE"
    assert field in decision.reason_detail
    assert getattr(decision, field) is None
    session.flush()


def test_pathological_negative_unit_price_keeps_invalid_price_priority(
    session: Session,
    make_raw,
) -> None:
    raw = make_raw(unit_price="-" + ("9" * 10_000))

    decision = apply_rules(session, raw)

    assert decision.status is CleanStatus.EXCLUDED
    assert decision.reason_code == "INVALID_UNIT_PRICE"
    assert decision.unit_price is None
    session.flush()


@pytest.mark.parametrize(
    "unit_price",
    [
        "-9223372036854.775808",
        "-0.0000001",
        "0" * 10_000,
    ],
)
def test_unsupported_negative_price_stays_invalid_without_overflow(
    session: Session,
    make_raw,
    unit_price: str,
) -> None:
    decision = apply_rules(
        session,
        make_raw(unit_price=unit_price),
    )

    assert decision.status is CleanStatus.EXCLUDED
    assert decision.reason_code == "INVALID_UNIT_PRICE"
    assert decision.unit_price is None
    session.flush()


def test_repeated_rule_run_is_idempotent_but_changed_raw_version_appends(
    session: Session,
    make_raw,
) -> None:
    raw = make_raw()

    first = apply_rules(session, raw)
    second = apply_rules(session, raw)

    assert second is first
    assert session.scalar(select(func.count(CleanDecision.id))) == 1
    first.rule_version = "attempted-mutation"
    with pytest.raises(ImmutableEvidenceError):
        session.flush()


def test_current_decision_returns_latest_chronological_row(
    session: Session,
    make_raw,
) -> None:
    raw = make_raw()
    automatic = apply_rules(session, raw)
    manual = CleanDecision(
        raw_item=raw,
        status=CleanStatus.REVIEW_REQUIRED,
        reason_code="MANUAL_REVIEW",
        rule_version="manual-v1",
        decided_by="reviewer",
    )
    session.add(manual)
    session.flush()

    assert current_decision(session, raw.id) is manual
    assert apply_rules(session, raw) is manual
    assert session.scalar(select(func.count(CleanDecision.id))) == 2
    assert automatic.id < manual.id


def test_current_decision_uses_insertion_order_not_backdated_display_time(
    session: Session,
    make_raw,
) -> None:
    raw = make_raw()
    apply_rules(session, raw)
    manual = CleanDecision(
        raw_item=raw,
        status=CleanStatus.EXCLUDED,
        reason_code="MANUAL_REVIEW",
        rule_version="manual-v1",
        decided_by="reviewer",
        decided_at=datetime(2000, 1, 1),
    )
    session.add(manual)
    session.flush()

    assert current_decision(session, raw.id) is manual


def test_future_dated_older_decision_cannot_shadow_later_insertion(
    session: Session,
    make_raw,
) -> None:
    raw = make_raw()
    future = CleanDecision(
        raw_item=raw,
        status=CleanStatus.REVIEW_REQUIRED,
        reason_code="FUTURE_IMPORT",
        rule_version="manual-v0",
        decided_by="reviewer",
        decided_at=datetime(2099, 1, 1),
    )
    session.add(future)
    session.flush()
    later = CleanDecision(
        raw_item=raw,
        status=CleanStatus.INCLUDED,
        reason_code="MANUAL_REVIEW",
        rule_version="manual-v1",
        decided_by="reviewer",
        decided_at=datetime(2020, 1, 1),
    )
    session.add(later)
    session.flush()

    assert future.id < later.id
    assert current_decision(session, raw.id) is later


def test_new_rule_version_appends_without_rewriting_prior_history(
    session: Session,
    make_raw,
) -> None:
    raw = make_raw()
    prior = CleanDecision(
        raw_item=raw,
        status=CleanStatus.INCLUDED,
        reason_code="VALID",
        rule_version="clean-v0",
    )
    session.add(prior)
    session.flush()

    current = apply_rules(session, raw)

    assert current.id != prior.id
    assert current.rule_version == "clean-v1"
    assert session.scalar(select(func.count(CleanDecision.id))) == 2


def test_automatic_rerun_never_supersedes_manual_decision(
    session: Session,
    make_raw,
) -> None:
    raw = make_raw()
    apply_rules(session, raw)
    manual = CleanDecision(
        raw_item=raw,
        status=CleanStatus.EXCLUDED,
        reason_code="MANUAL_REVIEW",
        rule_version="manual-v1",
        decided_by="reviewer",
    )
    session.add(manual)
    session.flush()

    result = apply_rules(session, raw)

    assert result is manual
    assert current_decision(session, raw.id) is manual
    assert session.scalar(select(func.count(CleanDecision.id))) == 2


def test_apply_rules_failure_rolls_back_only_its_savepoint(
    session: Session,
    make_raw,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = make_raw()
    from app.cleansing import service

    unrelated = SourceDocument(logical_name="unrelated-pending")
    session.add(unrelated)
    original_flush = session.flush

    def fail_when_decision_added(*args, **kwargs):
        if any(
            isinstance(value, CleanDecision)
            for value in session.new
        ):
            raise RuntimeError("simulated persistence failure")
        return original_flush(*args, **kwargs)

    monkeypatch.setattr(session, "flush", fail_when_decision_added)

    with pytest.raises(RuntimeError, match="simulated"):
        service.apply_rules(session, raw)

    assert session.in_transaction()
    assert unrelated in session
    assert session.scalar(select(func.count(CleanDecision.id))) == 0
