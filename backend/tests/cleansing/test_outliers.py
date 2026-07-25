from datetime import datetime
from decimal import Decimal

import pytest
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


@pytest.mark.parametrize(
    "candidate",
    [Decimal("101"), Decimal("100.000001")],
)
def test_zero_mad_ignores_small_absolute_or_relative_deltas(
    candidate: Decimal,
) -> None:
    assert mad_outlier_ids(
        [
            (1, Decimal("100")),
            (2, Decimal("100")),
            (3, candidate),
        ]
    ) == set()


def test_zero_mad_flags_only_meaningful_absolute_and_relative_delta() -> None:
    assert mad_outlier_ids(
        [
            (1, Decimal("100")),
            (2, Decimal("100")),
            (3, Decimal("150")),
        ]
    ) == {3}


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
    assert "median=" in latest.reason_detail
    assert "mad=" in latest.reason_detail
    assert "decision_ids=" in latest.reason_detail
    assert "rule=outlier-mad-v1" in latest.reason_detail
    assert "gate=delta>1 and relative>0.20" in latest.reason_detail
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


def test_manual_included_decision_is_never_superseded_by_outlier_rule(
    session: Session,
    make_raw,
) -> None:
    rows = [
        make_raw(
            item_name="Contactor",
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
    manual = CleanDecision(
        raw_item=rows[2],
        status=CleanStatus.INCLUDED,
        reason_code="MANUAL_REVIEW",
        item_name_norm="CONTACTOR",
        spec_norm="24 V",
        unit_norm="EA",
        quantity=Decimal("1"),
        unit_price=Decimal("1000"),
        amount=Decimal("1000"),
        rule_version="manual-v1",
        decided_by="reviewer",
        decided_at=datetime(2000, 1, 1),
    )
    session.add(manual)
    session.flush()

    created = apply_group_outlier_rules(session)

    assert created == []
    assert current_decision(session, rows[2].id) is manual


def test_outlier_baseline_uses_latest_insert_not_future_display_time(
    session: Session,
    make_raw,
) -> None:
    rows = [
        make_raw(
            item_name="Timer",
            spec="24 V",
            unit="EA",
            quantity="1",
            unit_price="100",
            amount="100",
            source_row=index,
        )
        for index in range(1, 4)
    ]
    future_old = CleanDecision(
        raw_item=rows[2],
        status=CleanStatus.INCLUDED,
        reason_code="VALID",
        item_name_norm="TIMER",
        spec_norm="24 V",
        unit_norm="EA",
        quantity=Decimal("1"),
        unit_price=Decimal("1000"),
        amount=Decimal("1000"),
        rule_version="clean-v0",
        decided_at=datetime(2099, 1, 1),
    )
    session.add(future_old)
    session.flush()
    for row in rows[:2]:
        apply_rules(session, row)
    latest_target = CleanDecision(
        raw_item=rows[2],
        status=CleanStatus.INCLUDED,
        reason_code="VALID",
        item_name_norm="TIMER",
        spec_norm="24 V",
        unit_norm="EA",
        quantity=Decimal("1"),
        unit_price=Decimal("100"),
        amount=Decimal("100"),
        rule_version="clean-v1",
        decided_at=datetime(2000, 1, 1),
    )
    session.add(latest_target)
    session.flush()

    assert future_old.id < latest_target.id
    assert apply_group_outlier_rules(session) == []
    assert current_decision(session, rows[2].id) is latest_target


def test_outlier_version_bump_appends_new_flagged_history(
    session: Session,
    make_raw,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        make_raw(
            item_name="Breaker",
            spec="30 A",
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
    [first] = apply_group_outlier_rules(session)
    monkeypatch.setattr(
        "app.cleansing.service.OUTLIER_RULE_VERSION",
        "outlier-mad-v2",
    )

    [second] = apply_group_outlier_rules(session)

    assert first.raw_item_id == second.raw_item_id == rows[2].id
    assert first.rule_version == "outlier-mad-v1"
    assert second.rule_version == "outlier-mad-v2"
    assert second.id > first.id
    assert current_decision(session, rows[2].id) is second


def test_changed_outlier_group_reason_appends_auditable_history(
    session: Session,
    make_raw,
) -> None:
    rows = [
        make_raw(
            item_name="Breaker",
            spec="50 A",
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
    [first] = apply_group_outlier_rules(session)
    added = make_raw(
        item_name="Breaker",
        spec="50 A",
        unit="EA",
        quantity="1",
        unit_price="100",
        amount="100",
        source_row=4,
    )
    apply_rules(session, added)

    [second] = apply_group_outlier_rules(session)

    assert first.raw_item_id == second.raw_item_id == rows[2].id
    assert first.rule_version == second.rule_version
    assert "observations=3" in first.reason_detail
    assert "observations=4" in second.reason_detail
    assert second.id > first.id


def test_outlier_version_bump_appends_recovery_when_no_longer_flagged(
    session: Session,
    make_raw,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        make_raw(
            item_name="Terminal",
            spec="10 P",
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
    [outlier] = apply_group_outlier_rules(session)
    for index in (4, 5):
        row = make_raw(
            item_name="Terminal",
            spec="10 P",
            unit="EA",
            quantity="1",
            unit_price="1000",
            amount="1000",
            source_row=index,
        )
        apply_rules(session, row)
    monkeypatch.setattr(
        "app.cleansing.service.OUTLIER_RULE_VERSION",
        "outlier-mad-v2",
    )

    created = apply_group_outlier_rules(session)
    recovered = current_decision(session, rows[2].id)

    assert outlier.raw_item_id == rows[2].id
    assert recovered is not None
    assert recovered.status is CleanStatus.INCLUDED
    assert recovered.reason_code == "VALID"
    assert recovered.rule_version == "outlier-mad-v2"
    assert "no longer" in recovered.reason_detail
    assert recovered in created


def test_same_outlier_version_retry_is_idempotent_after_version_bump(
    session: Session,
    make_raw,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        make_raw(
            item_name="Connector",
            spec="12 P",
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
    apply_group_outlier_rules(session)
    monkeypatch.setattr(
        "app.cleansing.service.OUTLIER_RULE_VERSION",
        "outlier-mad-v2",
    )

    first = apply_group_outlier_rules(session)
    second = apply_group_outlier_rules(session)

    assert len(first) == 1
    assert second == []
    assert session.scalar(select(func.count(CleanDecision.id))) == 5
