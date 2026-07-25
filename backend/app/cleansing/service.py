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
        .order_by(
            CleanDecision.decided_at.desc(),
            CleanDecision.id.desc(),
        )
        .limit(1)
    )


def apply_group_outlier_rules(session: Session) -> list[CleanDecision]:
    """Append review decisions for MAD outliers in exact normalized groups."""
    latest_by_item: dict[int, CleanDecision] = {}
    for decision in session.scalars(
        select(CleanDecision).order_by(
            CleanDecision.decided_at,
            CleanDecision.id,
        )
    ):
        latest_by_item[decision.raw_item_id] = decision

    grouped: dict[
        tuple[str, str, str],
        list[tuple[int, Decimal]],
    ] = defaultdict(list)
    for decision in latest_by_item.values():
        if (
            decision.status is CleanStatus.INCLUDED
            and decision.item_name_norm
            and decision.spec_norm
            and decision.unit_norm
            and decision.unit_price is not None
        ):
            group_key = (
                decision.item_name_norm,
                decision.spec_norm,
                decision.unit_norm,
            )
            grouped[group_key].append(
                (decision.raw_item_id, decision.unit_price)
            )

    flagged_context: dict[int, tuple[tuple[str, str, str], int]] = {}
    for group_key, rows in grouped.items():
        for raw_item_id in mad_outlier_ids(rows):
            flagged_context[raw_item_id] = (group_key, len(rows))

    created: list[CleanDecision] = []
    with session.begin_nested():
        for raw_item_id in sorted(flagged_context):
            baseline = latest_by_item[raw_item_id]
            group_key, group_size = flagged_context[raw_item_id]
            decision = CleanDecision(
                raw_item_id=raw_item_id,
                status=CleanStatus.REVIEW_REQUIRED,
                reason_code="UNIT_PRICE_MAD_OUTLIER",
                reason_detail=(
                    "unit price is a group-local MAD outlier; "
                    f"baseline_decision_id={baseline.id}; "
                    f"group={group_key!r}; observations={group_size}"
                ),
                item_name_norm=baseline.item_name_norm,
                spec_norm=baseline.spec_norm,
                unit_norm=baseline.unit_norm,
                maker_norm=baseline.maker_norm,
                quantity=baseline.quantity,
                unit_price=baseline.unit_price,
                amount=baseline.amount,
                rule_version=OUTLIER_RULE_VERSION,
            )
            session.add(decision)
            created.append(decision)
        session.flush()
    return created


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
