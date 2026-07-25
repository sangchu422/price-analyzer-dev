"""Append-only cleansing decisions and their current projection."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cleansing.models import CleanDecision, CleanStatus
from app.cleansing.rules import (
    OUTLIER_RULE_VERSION,
    RULE_VERSION,
    Evaluation,
    ZERO_MAD_MIN_ABSOLUTE_DELTA,
    ZERO_MAD_MIN_RELATIVE_DELTA,
    decimal_median,
    evaluate,
    mad_outlier_ids,
)
from app.quotes.models import RawQuoteItem


def apply_rules(session: Session, raw_item: RawQuoteItem) -> CleanDecision:
    """Append one deterministic decision, leaving commit to the caller.

    Repeating the same rule version against the same immutable raw item is
    idempotent. A newer manual decision is never silently superseded.
    """
    result = evaluate(raw_item)
    latest = current_decision(session, raw_item.id)
    if latest is not None and latest.decided_by != "SYSTEM":
        return latest
    prior_match = _matching_rule_decision(session, raw_item.id, result)
    if prior_match is not None:
        return prior_match

    with session.begin_nested():
        decision = _decision_from_evaluation(raw_item, result)
        session.add(decision)
        session.flush()
    return decision


def current_decision(
    session: Session,
    raw_item_id: int,
) -> CleanDecision | None:
    """Project the latest append-only decision by insertion chronology."""
    return session.scalar(
        select(CleanDecision)
        .where(CleanDecision.raw_item_id == raw_item_id)
        .order_by(CleanDecision.id.desc())
        .limit(1)
    )


def apply_group_outlier_rules(session: Session) -> list[CleanDecision]:
    """Append review decisions for MAD outliers in exact normalized groups."""
    history: list[CleanDecision] = list(
        session.scalars(
            select(CleanDecision).order_by(
                CleanDecision.id,
            )
        )
    )
    latest_by_item: dict[int, CleanDecision] = {}
    baseline_by_item: dict[int, CleanDecision] = {}
    for decision in history:
        latest_by_item[decision.raw_item_id] = decision
        if (
            decision.decided_by == "SYSTEM"
            and decision.rule_version.startswith("clean-")
        ):
            baseline_by_item[decision.raw_item_id] = decision

    grouped: dict[
        tuple[str, str, str],
        list[tuple[int, Decimal]],
    ] = defaultdict(list)
    eligible_baselines: dict[int, CleanDecision] = {}
    for raw_item_id, baseline in baseline_by_item.items():
        latest = latest_by_item[raw_item_id]
        if (
            latest.decided_by != "SYSTEM"
            or baseline.status is not CleanStatus.INCLUDED
            or not baseline.item_name_norm
            or not baseline.spec_norm
            or not baseline.unit_norm
            or baseline.unit_price is None
        ):
            continue
        eligible_baselines[raw_item_id] = baseline
        group_key = (
            baseline.item_name_norm,
            baseline.spec_norm,
            baseline.unit_norm,
        )
        grouped[group_key].append(
            (raw_item_id, baseline.unit_price)
        )

    flagged_context: dict[
        int,
        tuple[
            tuple[str, str, str],
            list[tuple[int, Decimal]],
            Decimal,
            Decimal,
        ],
    ] = {}
    for group_key, rows in grouped.items():
        rows.sort(key=lambda row: row[0])
        values = sorted(value for _, value in rows)
        median = decimal_median(values)
        mad = decimal_median(
            sorted(abs(value - median) for value in values)
        )
        for raw_item_id in mad_outlier_ids(rows):
            flagged_context[raw_item_id] = (
                group_key,
                rows,
                median,
                mad,
            )

    created: list[CleanDecision] = []
    with session.begin_nested():
        for raw_item_id in sorted(eligible_baselines):
            baseline = eligible_baselines[raw_item_id]
            latest = latest_by_item[raw_item_id]
            if raw_item_id in flagged_context:
                group_key, rows, median, mad = flagged_context[
                    raw_item_id
                ]
                reason_detail = _outlier_reason_detail(
                    baseline=baseline,
                    group_key=group_key,
                    rows=rows,
                    baselines=eligible_baselines,
                    median=median,
                    mad=mad,
                )
                if (
                    latest.status is CleanStatus.REVIEW_REQUIRED
                    and latest.reason_code == "UNIT_PRICE_MAD_OUTLIER"
                    and latest.reason_detail == reason_detail
                    and latest.rule_version == OUTLIER_RULE_VERSION
                ):
                    continue
                decision = _outlier_decision(
                    baseline,
                    status=CleanStatus.REVIEW_REQUIRED,
                    reason_code="UNIT_PRICE_MAD_OUTLIER",
                    reason_detail=reason_detail,
                )
            elif (
                latest.decided_by == "SYSTEM"
                and latest.reason_code == "UNIT_PRICE_MAD_OUTLIER"
            ):
                decision = _outlier_decision(
                    baseline,
                    status=baseline.status,
                    reason_code=baseline.reason_code,
                    reason_detail=(
                        "unit price is no longer a group-local MAD "
                        f"outlier; baseline_decision_id={baseline.id}; "
                        f"previous_outlier_decision_id={latest.id}"
                    ),
                )
            else:
                continue
            session.add(decision)
            created.append(decision)
        session.flush()
    return created


def _outlier_decision(
    baseline: CleanDecision,
    *,
    status: CleanStatus,
    reason_code: str,
    reason_detail: str,
) -> CleanDecision:
    return CleanDecision(
        raw_item_id=baseline.raw_item_id,
        status=status,
        reason_code=reason_code,
        reason_detail=reason_detail,
        item_name_norm=baseline.item_name_norm,
        spec_norm=baseline.spec_norm,
        unit_norm=baseline.unit_norm,
        maker_norm=baseline.maker_norm,
        quantity=baseline.quantity,
        unit_price=baseline.unit_price,
        amount=baseline.amount,
        rule_version=OUTLIER_RULE_VERSION,
    )


def _outlier_reason_detail(
    *,
    baseline: CleanDecision,
    group_key: tuple[str, str, str],
    rows: list[tuple[int, Decimal]],
    baselines: dict[int, CleanDecision],
    median: Decimal,
    mad: Decimal,
) -> str:
    decision_ids = [baselines[row_id].id for row_id, _ in rows]
    shown_ids = decision_ids[:50]
    omitted = len(decision_ids) - len(shown_ids)
    ids_snapshot = (
        f"{shown_ids!r}"
        if omitted == 0
        else f"{shown_ids!r}...(+{omitted})"
    )
    bounded_group = tuple(_bounded_text(value, 80) for value in group_key)
    gate = (
        "delta>"
        f"{ZERO_MAD_MIN_ABSOLUTE_DELTA}"
        " and relative>"
        f"{ZERO_MAD_MIN_RELATIVE_DELTA}"
        if mad == 0
        else "modified_z>3.5"
    )
    return (
        "unit price is a group-local MAD outlier; "
        f"rule={OUTLIER_RULE_VERSION}; "
        f"baseline_decision_id={baseline.id}; "
        f"group={bounded_group!r}; observations={len(rows)}; "
        f"decision_ids={ids_snapshot}; median={median}; mad={mad}; "
        f"gate={gate}"
    )


def _bounded_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _decision_from_evaluation(
    raw_item: RawQuoteItem,
    result: Evaluation,
) -> CleanDecision:
    return CleanDecision(
        raw_item=raw_item,
        status=result.status,
        reason_code=result.reason_code,
        reason_detail=result.reason_detail,
        item_name_norm=result.item_name_norm,
        spec_norm=result.spec_norm,
        unit_norm=result.unit_norm,
        maker_norm=result.maker_norm,
        quantity=result.quantity,
        unit_price=result.unit_price,
        amount=result.amount,
        rule_version=RULE_VERSION,
    )


def _matches_evaluation(
    decision: CleanDecision,
    result: Evaluation,
    rule_version: str,
) -> bool:
    return (
        decision.rule_version == rule_version
        and decision.status is result.status
        and decision.reason_code == result.reason_code
        and decision.reason_detail == result.reason_detail
        and decision.item_name_norm == result.item_name_norm
        and decision.spec_norm == result.spec_norm
        and decision.unit_norm == result.unit_norm
        and decision.maker_norm == result.maker_norm
        and decision.quantity == result.quantity
        and decision.unit_price == result.unit_price
        and decision.amount == result.amount
    )


def _matching_rule_decision(
    session: Session,
    raw_item_id: int,
    result: Evaluation,
) -> CleanDecision | None:
    candidates = session.scalars(
        select(CleanDecision)
        .where(
            CleanDecision.raw_item_id == raw_item_id,
            CleanDecision.rule_version == RULE_VERSION,
            CleanDecision.decided_by == "SYSTEM",
        )
        .order_by(CleanDecision.id.desc())
    )
    return next(
        (
            decision
            for decision in candidates
            if _matches_evaluation(decision, result, RULE_VERSION)
        ),
        None,
    )
