"""Shared read model for the operational cleansing review queue.

OCR and legacy parser warnings are reviewed once per source document variant,
not once for every extracted row.  Keeping that grouping rule here prevents
the dashboard and the review screen from reporting different workloads.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import TypeAlias

from sqlalchemy import func, select

from app.cleansing.models import CleanDecision, CleanStatus
from app.documents.models import SourceDocument, SourceVariant
from app.parsing.projection import current_raw_item_ids
from app.quotes.models import RawQuoteItem


DOCUMENT_LEVEL_REASON_CODES = frozenset(
    {"OCR_SOURCE_REVIEW_REQUIRED", "PARSER_SOURCE_REVIEW_REQUIRED"}
)

ReviewCaseKey: TypeAlias = tuple[str, int, str | None]
ReviewQueueRow: TypeAlias = tuple[
    RawQuoteItem,
    CleanDecision,
    SourceVariant,
    SourceDocument,
]


def current_review_queue_query():
    """Return the unfiltered query used by the operational review queue."""

    latest_ids = (
        select(
            CleanDecision.raw_item_id,
            func.max(CleanDecision.id).label("decision_id"),
        )
        .group_by(CleanDecision.raw_item_id)
        .subquery()
    )
    current_raw = current_raw_item_ids()
    return (
        select(RawQuoteItem, CleanDecision, SourceVariant, SourceDocument)
        .join(latest_ids, latest_ids.c.raw_item_id == RawQuoteItem.id)
        .join(CleanDecision, CleanDecision.id == latest_ids.c.decision_id)
        .join(SourceVariant, SourceVariant.id == RawQuoteItem.source_variant_id)
        .join(SourceDocument, SourceDocument.id == SourceVariant.document_id)
        .join(current_raw, current_raw.c.raw_item_id == RawQuoteItem.id)
        .where(CleanDecision.status == CleanStatus.REVIEW_REQUIRED)
    )


def review_case_key(
    *,
    raw_item_id: int,
    source_variant_id: int,
    reason_code: str | None,
) -> ReviewCaseKey:
    """Identify the single unit of work shown to a reviewer."""

    if reason_code in DOCUMENT_LEVEL_REASON_CODES:
        return ("document", source_variant_id, reason_code)
    return ("row", raw_item_id, reason_code)


def group_review_rows(
    rows: Iterable[ReviewQueueRow],
) -> tuple[list[ReviewQueueRow], dict[ReviewCaseKey, int]]:
    """Collapse document-wide warnings while retaining their raw row count."""

    grouped: list[ReviewQueueRow] = []
    counts: dict[ReviewCaseKey, int] = {}
    for row in rows:
        raw, decision, variant, _document = row
        key = review_case_key(
            raw_item_id=raw.id,
            source_variant_id=variant.id,
            reason_code=decision.reason_code,
        )
        counts[key] = counts.get(key, 0) + 1
        if counts[key] == 1:
            grouped.append(row)
    return grouped, counts


def summarize_review_cases(
    rows: Iterable[tuple[int, int, str | None]],
) -> tuple[int, Counter[str | None]]:
    """Count reviewer-visible cases without hydrating full ORM records."""

    cases: set[ReviewCaseKey] = set()
    reason_counts: Counter[str | None] = Counter()
    for raw_item_id, source_variant_id, reason_code in rows:
        key = review_case_key(
            raw_item_id=raw_item_id,
            source_variant_id=source_variant_id,
            reason_code=reason_code,
        )
        if key in cases:
            continue
        cases.add(key)
        reason_counts[reason_code] += 1
    return len(cases), reason_counts
