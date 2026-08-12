"""Append-only cleansing decisions and their current projection."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from decimal import Decimal
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cleansing.calculation import spreadsheet_amount_evidence
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
from app.parsing.projection import current_raw_item_ids


def apply_rules(session: Session, raw_item: RawQuoteItem) -> CleanDecision:
    """Append one deterministic decision, leaving commit to the caller.

    Repeating the same rule version against the same immutable raw item is
    idempotent. A newer manual decision is never silently superseded.
    """
    result = evaluate(raw_item)
    calculation_evidence = None
    if result.reason_code == "AMOUNT_MISMATCH":
        calculation_evidence = spreadsheet_amount_evidence(raw_item)
        if calculation_evidence and calculation_evidence.get("matches") is True:
            result = replace(
                result,
                status=CleanStatus.INCLUDED,
                reason_code="VALID_MULTIFACTOR_AMOUNT",
                reason_detail="source formula confirms all amount factors",
            )
    if _requires_ocr_review(raw_item):
        result = replace(
            result,
            status=CleanStatus.REVIEW_REQUIRED,
            reason_code="OCR_SOURCE_REVIEW_REQUIRED",
            reason_detail=(
                "OCR extraction is a candidate and requires source "
                "review before inclusion"
            ),
        )
    elif _requires_parser_review(raw_item, result):
        result = replace(
            result,
            status=CleanStatus.REVIEW_REQUIRED,
            reason_code="PARSER_SOURCE_REVIEW_REQUIRED",
            reason_detail=(
                "layout-derived extraction requires source review before "
                "inclusion"
            ),
        )
    latest = current_decision(session, raw_item.id)
    if latest is not None and latest.decided_by != "SYSTEM":
        return latest
    prior_match = _matching_rule_decision(session, raw_item.id, result)
    if prior_match is not None:
        return prior_match

    with session.begin_nested():
        decision = _decision_from_evaluation(
            raw_item,
            result,
            reason_evidence=calculation_evidence,
        )
        session.add(decision)
        session.flush()
    return decision


def _requires_ocr_review(raw_item: RawQuoteItem) -> bool:
    try:
        warnings = json.loads(raw_item.parse_warnings_json)
    except (TypeError, ValueError):
        return False
    if not isinstance(warnings, list):
        return False
    confidence = next(
        (
            int(value.rsplit("_", 1)[1])
            for value in warnings
            if isinstance(value, str)
            and value.startswith("EXTRACTION_CONFIDENCE_")
            and value.rsplit("_", 1)[1].isdigit()
        ),
        None,
    )
    return bool(
        {"OCR_SOURCE", "OCR_REVIEW_REQUIRED"}.intersection(warnings)
    ) and (confidence is None or confidence < 90)


def _requires_parser_review(
    raw_item: RawQuoteItem,
    result: Evaluation,
) -> bool:
    try:
        warnings = json.loads(raw_item.parse_warnings_json)
    except (TypeError, ValueError):
        return False
    if not isinstance(warnings, list) or "PARSER_SOURCE_REVIEW_REQUIRED" not in warnings:
        return False
    warning_set = {value for value in warnings if isinstance(value, str)}
    risky = {
        "PDF_LEGACY_LINE",
        "PDF_LAYOUT_TEXT",
        "DERIVED_UNIT_PRICE",
        "SOURCE_SPEC_BLANK",
        "SPEC_COLUMN_NOT_FOUND",
    }
    high_confidence_layout = bool(
        {"PDF_COORDINATE_TABLE", "CJK_HEADER_MAPPING"}.intersection(warning_set)
    )
    return not (
        result.status is CleanStatus.INCLUDED
        and high_confidence_layout
        and not risky.intersection(warning_set)
    )


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

    current_raw = current_raw_item_ids()
    current_ids = set(session.scalars(select(current_raw.c.raw_item_id)))
    grouped: dict[
        tuple[str, str, str],
        list[tuple[int, Decimal]],
    ] = defaultdict(list)
    eligible_baselines: dict[int, CleanDecision] = {}
    for raw_item_id, baseline in baseline_by_item.items():
        if raw_item_id not in current_ids:
            continue
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
                reason_evidence_json = _outlier_reason_evidence(
                    baseline=baseline,
                    rows=rows,
                    baselines=eligible_baselines,
                    median=median,
                    mad=mad,
                )
                if (
                    latest.status is CleanStatus.REVIEW_REQUIRED
                    and latest.reason_code == "UNIT_PRICE_MAD_OUTLIER"
                    and latest.reason_detail == reason_detail
                    and latest.reason_evidence_json == reason_evidence_json
                    and latest.rule_version == OUTLIER_RULE_VERSION
                ):
                    continue
                decision = _outlier_decision(
                    baseline,
                    status=CleanStatus.REVIEW_REQUIRED,
                    reason_code="UNIT_PRICE_MAD_OUTLIER",
                    reason_detail=reason_detail,
                    reason_evidence_json=reason_evidence_json,
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
    reason_evidence_json: str = "{}",
) -> CleanDecision:
    return CleanDecision(
        raw_item_id=baseline.raw_item_id,
        status=status,
        reason_code=reason_code,
        reason_detail=reason_detail,
        reason_evidence_json=reason_evidence_json,
        item_name_norm=baseline.item_name_norm,
        spec_norm=baseline.spec_norm,
        unit_norm=baseline.unit_norm,
        maker_norm=baseline.maker_norm,
        quantity=baseline.quantity,
        unit_price=baseline.unit_price,
        amount=baseline.amount,
        rule_version=OUTLIER_RULE_VERSION,
    )


def _outlier_reason_evidence(
    *,
    baseline: CleanDecision,
    rows: list[tuple[int, Decimal]],
    baselines: dict[int, CleanDecision],
    median: Decimal,
    mad: Decimal,
) -> str:
    current_price = baseline.unit_price
    variance = (
        None
        if current_price is None or median == 0
        else ((current_price - median) / median * Decimal("100"))
    )
    payload = {
        "kind": "UNIT_PRICE_DISTRIBUTION",
        "current_unit_price": None if current_price is None else str(current_price),
        "median_unit_price": str(median),
        "variance_percent": None if variance is None else str(variance.quantize(Decimal("0.1"))),
        "observation_count": len(rows),
        "mad": str(mad),
        "observations": [
            {
                "raw_item_id": raw_item_id,
                "clean_decision_id": baselines[raw_item_id].id,
                "unit_price": str(unit_price),
            }
            for raw_item_id, unit_price in rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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
    *,
    reason_evidence: dict[str, object] | None = None,
) -> CleanDecision:
    return CleanDecision(
        raw_item=raw_item,
        status=result.status,
        reason_code=result.reason_code,
        reason_detail=result.reason_detail,
        reason_evidence_json=json.dumps(
            reason_evidence or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
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
