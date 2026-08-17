"""Append-only full-corpus cleansing reassessment and audit report."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.cleansing.models import CleanDecision
from app.cleansing.rules import RULE_VERSION
from app.cleansing.service import apply_group_outlier_rules, apply_rules, current_decision
from app.core.config import settings
from app.db.sqlite import configure_sqlite
from app.parsing.models import (
    CleansingReassessmentEntry,
    CleansingReassessmentRun,
    SourceParseOutput,
    SourceParseRun,
)
from app.parsing.projection import current_raw_item_ids
from app.quotes.models import RawQuoteItem
from app.documents.models import SourceVariant

REASSESSMENT_VERSION = "clean-v2-audit-v6"


def run_reassessment(session: Session, report_dir: Path) -> dict[str, object]:
    current_raw = current_raw_item_ids()
    raw_ids = tuple(session.scalars(select(current_raw.c.raw_item_id).order_by(current_raw.c.raw_item_id)))
    fingerprint = hashlib.sha256(
        (REASSESSMENT_VERSION + ":" + RULE_VERSION + ":" + ",".join(map(str, raw_ids))).encode("utf-8")
    ).hexdigest()
    existing = session.scalar(
        select(CleansingReassessmentRun).where(
            CleansingReassessmentRun.input_fingerprint == fingerprint,
            CleansingReassessmentRun.rule_version == RULE_VERSION,
        )
    )
    if existing is not None:
        return json.loads(existing.counts_json)

    previous = {raw_id: current_decision(session, raw_id) for raw_id in raw_ids}
    for raw, variant in session.execute(
        select(RawQuoteItem, SourceVariant)
        .join(current_raw, current_raw.c.raw_item_id == RawQuoteItem.id)
        .join(SourceVariant, SourceVariant.id == RawQuoteItem.source_variant_id)
        .order_by(RawQuoteItem.id)
    ):
        raw.__dict__["source_variant"] = variant
        apply_rules(session, raw)
    apply_group_outlier_rules(session)
    final = {raw_id: current_decision(session, raw_id) for raw_id in raw_ids}

    # Explain every review decision that was superseded by a newer parser run.
    # Matching uses only immutable source coordinates, never normalized values.
    current_set = set(raw_ids)
    current_by_location: dict[tuple[object, ...], list[int]] = {}
    current_by_semantic_source: dict[tuple[object, ...], list[int]] = {}
    for raw in session.scalars(
        select(RawQuoteItem).join(current_raw, current_raw.c.raw_item_id == RawQuoteItem.id)
    ):
        current_by_location.setdefault(_source_location_key(raw), []).append(raw.id)
        current_by_semantic_source.setdefault(_semantic_source_key(raw), []).append(raw.id)

    ranked_decisions = select(
        CleanDecision.raw_item_id,
        CleanDecision.id.label("decision_id"),
        func.row_number().over(
            partition_by=CleanDecision.raw_item_id,
            order_by=CleanDecision.id.desc(),
        ).label("rank"),
    ).where(CleanDecision.rule_version != RULE_VERSION).subquery()
    latest_decision_ids = select(
        ranked_decisions.c.raw_item_id,
        ranked_decisions.c.decision_id,
    ).where(ranked_decisions.c.rank == 1).subquery()
    baseline_reviews: list[tuple[RawQuoteItem, CleanDecision]] = []
    for old_raw, old_decision in session.execute(
        select(RawQuoteItem, CleanDecision)
        .join(latest_decision_ids, latest_decision_ids.c.raw_item_id == RawQuoteItem.id)
        .join(CleanDecision, CleanDecision.id == latest_decision_ids.c.decision_id)
        .join(SourceParseOutput, SourceParseOutput.raw_item_id == RawQuoteItem.id)
        .join(SourceParseRun, SourceParseRun.id == SourceParseOutput.parse_run_id)
        .where(CleanDecision.status == "REVIEW_REQUIRED")
        .where(SourceParseRun.parser_version == "reader-v1")
    ):
        baseline_reviews.append((old_raw, old_decision))

    rows: list[dict[str, object]] = []
    changed = 0
    for raw_id in raw_ids:
        old = previous[raw_id]
        new = final[raw_id]
        if new is None:
            continue
        disposition = _disposition(old, new)
        changed += int(old is None or old.id != new.id)
        rows.append({
            "raw_item_id": raw_id,
            "previous_decision_id": None if old is None else old.id,
            "previous_status": None if old is None else old.status.value,
            "previous_reason": None if old is None else old.reason_code,
            "new_decision_id": new.id,
            "new_status": new.status.value,
            "new_reason": new.reason_code,
            "disposition": disposition,
            "successor_raw_item_id": raw_id,
        })

    operational_rows = list(rows)
    row_by_raw_id = {int(row["raw_item_id"]): row for row in rows}
    baseline_current = 0
    superseded_explained = 0
    superseded_unmatched = 0
    for old_raw, old in baseline_reviews:
        if old_raw.id in current_set:
            baseline_current += 1
            row_by_raw_id[old_raw.id]["baseline_review_decision_id"] = old.id
            row_by_raw_id[old_raw.id]["baseline_review_reason"] = old.reason_code
            continue
        successors = current_by_location.get(_source_location_key(old_raw), [])
        if len(successors) != 1:
            successors = current_by_semantic_source.get(_semantic_source_key(old_raw), [])
        successor_id = successors[-1] if len(successors) == 1 else None
        new = final.get(successor_id) if successor_id is not None else None
        if new is None:
            superseded_unmatched += 1
            rows.append({
                "raw_item_id": old_raw.id,
                "previous_decision_id": old.id,
                "previous_status": old.status.value,
                "previous_reason": old.reason_code,
                "new_decision_id": old.id,
                "new_status": old.status.value,
                "new_reason": old.reason_code,
                "disposition": "SUPERSEDED_SOURCE_LOCATION_UNMATCHED",
                "successor_raw_item_id": None,
            })
            continue
        superseded_explained += 1
        rows.append({
            "raw_item_id": old_raw.id,
            "previous_decision_id": old.id,
            "previous_status": old.status.value,
            "previous_reason": old.reason_code,
            "new_decision_id": new.id,
            "new_status": new.status.value,
            "new_reason": new.reason_code,
            "disposition": "SUPERSEDED_BY_REPARSE",
            "successor_raw_item_id": successor_id,
        })

    counts = {
        "rule_version": RULE_VERSION,
        "input_fingerprint": fingerprint,
        "raw_item_count": len(raw_ids),
        "changed_count": changed,
        "review_required_count": sum(row["new_status"] == "REVIEW_REQUIRED" for row in operational_rows),
        "included_count": sum(row["new_status"] == "INCLUDED" for row in operational_rows),
        "excluded_count": sum(row["new_status"] == "EXCLUDED" for row in operational_rows),
        "baseline_review_count": len(baseline_reviews),
        "baseline_review_retained_raw_count": baseline_current,
        "superseded_review_count": len(baseline_reviews) - baseline_current,
        "superseded_review_explained_count": superseded_explained,
        "superseded_review_unmatched_count": superseded_unmatched,
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / f"cleansing-reassessment-{RULE_VERSION}.json"
    csv_path = report_dir / f"cleansing-reassessment-{RULE_VERSION}.csv"
    json_path.write_text(json.dumps({"summary": counts, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        fieldnames = list(dict.fromkeys(key for row in rows for key in row)) if rows else ["raw_item_id"]
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    run = CleansingReassessmentRun(
        input_fingerprint=fingerprint,
        rule_version=RULE_VERSION,
        status="SUCCEEDED",
        counts_json=json.dumps(counts, ensure_ascii=False, sort_keys=True),
        report_path=str(json_path),
    )
    session.add(run)
    session.flush()
    session.add_all(
        CleansingReassessmentEntry(
            run_id=run.id,
            raw_item_id=int(row["raw_item_id"]),
            previous_decision_id=row["previous_decision_id"],
            new_decision_id=int(row["new_decision_id"]),
            disposition=str(row["disposition"]),
            evidence_json=json.dumps(row, ensure_ascii=False, sort_keys=True),
        )
        for row in rows
    )
    return counts


def _disposition(old: CleanDecision | None, new: CleanDecision) -> str:
    if old is None:
        return "NEW_BASELINE"
    if old.status != new.status:
        return f"{old.status.value}_TO_{new.status.value}"
    if old.reason_code != new.reason_code:
        return "REASON_CORRECTED"
    return "CONFIRMED"


def _source_location_key(raw: RawQuoteItem) -> tuple[object, ...]:
    return (
        raw.source_variant_id,
        raw.source_sheet,
        raw.source_page,
        raw.source_row,
        raw.source_cells,
    )


def _semantic_source_key(raw: RawQuoteItem) -> tuple[object, ...]:
    return (
        raw.source_variant_id,
        raw.item_name_raw,
        raw.spec_raw,
        raw.unit_raw,
        raw.quantity_raw,
        raw.unit_price_raw,
        raw.amount_raw,
    )


def main() -> int:
    engine = configure_sqlite(create_engine(f"sqlite:///{settings.database_path.as_posix()}"))
    try:
        with Session(engine) as session:
            counts = run_reassessment(session, settings.project_root / "backend/.local/reports")
            session.commit()
        print(json.dumps(counts, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
