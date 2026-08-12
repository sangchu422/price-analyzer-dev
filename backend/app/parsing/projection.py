"""SQL projection for rows emitted by the latest successful parser run."""

from sqlalchemy import func, select

from app.parsing.models import ParseRunStatus, SourceParseOutput, SourceParseRun
from app.quotes.models import RawQuoteItem


def current_raw_item_ids():
    latest_runs = (
        select(
            SourceParseRun.source_variant_id,
            func.max(SourceParseRun.id).label("parse_run_id"),
        )
        .where(SourceParseRun.status == ParseRunStatus.SUCCEEDED)
        .group_by(SourceParseRun.source_variant_id)
        .subquery()
    )
    projected = (
        select(SourceParseOutput.raw_item_id.label("raw_item_id"))
        .join(latest_runs, latest_runs.c.parse_run_id == SourceParseOutput.parse_run_id)
    )
    legacy_without_run = select(RawQuoteItem.id.label("raw_item_id")).where(
        ~select(SourceParseRun.id)
        .where(
            SourceParseRun.source_variant_id == RawQuoteItem.source_variant_id,
            SourceParseRun.status == ParseRunStatus.SUCCEEDED,
        )
        .exists()
    )
    return projected.union_all(legacy_without_run).subquery()
