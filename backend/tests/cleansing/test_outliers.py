from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cleansing.models import CleanDecision, CleanStatus
from app.cleansing.rules import mad_outlier_ids
from app.cleansing.service import (
    apply_group_outlier_rules,
    apply_rules,
    current_decision,
)


def test_fewer_than_three_observations_are_never_flagged() -> None:
    assert mad_outlier_ids(
        [(1, Decimal("10")), (2, Decimal("10000"))]
    ) == set()


def test_zero_mad_flags_only_values_different_from_median() -> None:
    assert mad_outlier_ids(
        [
            (1, Decimal("100")),
            (2, Decimal("100")),
            (3, Decimal("100")),
            (4, Decimal("1000")),
        ]
    ) == {4}


def test_nonzero_mad_uses_robust_threshold() -> None:
    assert mad_outlier_ids(
        [
            (1, Decimal("98")),
            (2, Decimal("100")),
            (3, Decimal("101")),
            (4, Decimal("102")),
            (5, Decimal("1000")),
        ]
    ) == {5}


def test_outliers_are_group_local_and_append_review_decisions(
    session: Session,
    make_raw,
) -> None:
    motor_rows = [
        make_raw(
            item_name="Motor",
            spec="200 W",
            unit="EA",
            unit_price=str(price),
            amount=str(price),
            quantity="1",
            source_row=index,
        )
        for index, price in enumerate((100, 100, 1000), start=1)
    ]
    bearing_rows = [
        make_raw(
            item_name="Bearing",
            spec="6204",
            unit="EA",
            unit_price=str(price),
            amount=str(price),
            quantity="1",
            source_row=index + 10,
        )
        for index, price in enumerate((5, 5000), start=1)
    ]
    for row in motor_rows + bearing_rows:
        apply_rules(session, row)

    created = apply_group_outlier_rules(session)

    assert [decision.raw_item_id for decision in created] == [
        motor_rows[2].id
    ]
    latest = current_decision(session, motor_rows[2].id)
    assert latest is not None
    assert latest.status is CleanStatus.REVIEW_REQUIRED
    assert latest.reason_code == "UNIT_PRICE_MAD_OUTLIER"
    assert current_decision(
        session,
        bearing_rows[1].id,
    ).status is CleanStatus.INCLUDED
    assert session.scalar(select(func.count(CleanDecision.id))) == 6


def test_outlier_group_includes_unit_to_avoid_invalid_comparisons(
    session: Session,
    make_raw,
) -> None:
    rows = [
        make_raw(
            item_name="Cable",
            spec="CV 2.5",
            unit=unit,
            unit_price=str(price),
            amount=str(price),
            quantity="1",
            source_row=index,
        )
        for index, (unit, price) in enumerate(
            (("M", 10), ("M", 10), ("ROLL", 10000)),
            start=1,
        )
    ]
    for row in rows:
        apply_rules(session, row)

    assert apply_group_outlier_rules(session) == []


def test_missing_spec_is_not_used_for_automatic_outlier_grouping(
    session: Session,
    make_raw,
) -> None:
    rows = [
        make_raw(
            item_name="Generic part",
            spec=None,
            unit="EA",
            unit_price=str(price),
            amount=str(price),
            quantity="1",
            source_row=index,
        )
        for index, price in enumerate((10, 10, 10000), start=1)
    ]
    for row in rows:
        apply_rules(session, row)

    assert apply_group_outlier_rules(session) == []


def test_repeated_outlier_run_does_not_duplicate_audit_decisions(
    session: Session,
    make_raw,
) -> None:
    rows = [
        make_raw(
            item_name="Relay",
            spec="24 V",
            unit="EA",
            quantity="1",
            unit_price=str(price),
            amount=str(price),
            source_row=index,
        )
        for index, price in enumerate((100, 100, 1000), start=1)
    ]
    for row in rows:
        apply_rules(session, row)

    first = apply_group_outlier_rules(session)
    second = apply_group_outlier_rules(session)

    assert len(first) == 1
    assert second == []
    assert session.scalar(select(func.count(CleanDecision.id))) == 4


def test_repeating_full_rule_pipeline_keeps_existing_outlier_current(
    session: Session,
    make_raw,
) -> None:
    rows = [
        make_raw(
            item_name="Fuse",
            spec="10 A",
            unit="EA",
            quantity="1",
            unit_price=str(price),
            amount=str(price),
            source_row=index,
        )
        for index, price in enumerate((10, 10, 1000), start=1)
    ]
    base_decisions = [apply_rules(session, row) for row in rows]
    [outlier] = apply_group_outlier_rules(session)

    repeated = [apply_rules(session, row) for row in rows]
    repeated_outliers = apply_group_outlier_rules(session)

    assert repeated == base_decisions
    assert repeated_outliers == []
    assert current_decision(session, rows[2].id) is outlier
    assert session.scalar(select(func.count(CleanDecision.id))) == 4
