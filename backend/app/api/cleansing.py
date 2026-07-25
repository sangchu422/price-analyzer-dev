"""Review queue and append-only manual cleansing decisions."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cleansing.models import CleanDecision, CleanStatus
from app.db.session import get_session
from app.documents.models import SourceDocument, SourceVariant
from app.quotes.models import RawQuoteItem


router = APIRouter()


class DecisionRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )

    status: Literal["INCLUDED", "EXCLUDED"]
    reason_code: Literal["MANUAL_REVIEW"]
    reason_detail: str = Field(min_length=1, max_length=2000)
    decided_by: str = Field(min_length=1, max_length=100)
    expected_current_decision_id: int = Field(gt=0)

    @field_validator("decided_by")
    @classmethod
    def reject_system_actor(cls, value: str) -> str:
        if value.casefold() == "system":
            raise ValueError("SYSTEM is reserved for automatic decisions")
        return value


class DecisionResponse(BaseModel):
    id: int
    status: CleanStatus
    reason_code: str
    reason_detail: str | None
    rule_version: str
    decided_by: str
    decided_at: datetime


class RawDisplay(BaseModel):
    item_name: str | None
    spec: str | None
    unit: str | None
    quantity: str | None
    unit_price: str | None
    amount: str | None
    maker: str | None


class NormalizedDisplay(RawDisplay):
    pass


class SourceEvidence(BaseModel):
    document_id: int
    logical_name: str
    variant_id: int
    path: str
    sha256: str
    security_state: str
    selected_for_parsing_at_ingest: bool
    sheet: str | None
    page: int | None
    row: int | None
    cells: str | None
    parser_name: str
    parser_version: str
    parser_warnings: list[Any]


class ReviewQueueItem(BaseModel):
    raw_item_id: int
    raw: RawDisplay
    normalized: NormalizedDisplay
    reason_code: str
    reason_detail: str | None
    decision: DecisionResponse
    source: SourceEvidence


class ReviewQueueResponse(BaseModel):
    items: list[ReviewQueueItem]
    remaining: int
    limit: int
    next_cursor: int | None
    available_reason_codes: list[str] = Field(
        description=(
            "Reason facets for the full current search/logical-document "
            "result, before reason and cursor filters."
        )
    )


@router.get("/review-queue", response_model=ReviewQueueResponse)
def review_queue(
    session: Session = Depends(get_session),
    *,
    limit: int = Query(50, ge=1, le=100),
    after_id: int | None = Query(
        None,
        ge=0,
        description=(
            "Stable raw-item cursor. Refresh from the first page to discover "
            "new review items inserted with lower IDs."
        ),
    ),
    offset: int | None = Query(None, include_in_schema=False),
    search: str | None = Query(
        None,
        max_length=200,
        description=(
            "Trimmed literal substring search. ASCII letters are "
            "case-insensitive; non-ASCII follows SQLite lower() behavior "
            "(Korean text is matched as an exact substring). SQL LIKE "
            "wildcards are ordinary characters."
        ),
    ),
    reason_code: str | None = Query(None, min_length=1, max_length=100),
    logical_name: str | None = Query(None, min_length=1, max_length=500),
) -> dict[str, object]:
    if offset is not None:
        raise HTTPException(
            status_code=422,
            detail="offset pagination is unsupported; use after_id",
        )
    latest_ids = (
        select(
            CleanDecision.raw_item_id,
            func.max(CleanDecision.id).label("decision_id"),
        )
        .group_by(CleanDecision.raw_item_id)
        .subquery()
    )
    base = (
        select(RawQuoteItem, CleanDecision, SourceVariant, SourceDocument)
        .join(
            latest_ids,
            latest_ids.c.raw_item_id == RawQuoteItem.id,
        )
        .join(
            CleanDecision,
            CleanDecision.id == latest_ids.c.decision_id,
        )
        .join(SourceVariant, SourceVariant.id == RawQuoteItem.source_variant_id)
        .join(SourceDocument, SourceDocument.id == SourceVariant.document_id)
        .where(CleanDecision.status == CleanStatus.REVIEW_REQUIRED)
    )
    if logical_name is not None:
        base = base.where(SourceDocument.logical_name == logical_name)
    normalized_search = search.strip() if search is not None else ""
    if normalized_search:
        pattern = f"%{_escape_like(normalized_search.casefold())}%"
        base = base.where(
            or_(
                func.lower(RawQuoteItem.item_name_raw).like(
                    pattern,
                    escape="\\",
                ),
                func.lower(RawQuoteItem.spec_raw).like(
                    pattern,
                    escape="\\",
                ),
                func.lower(CleanDecision.item_name_norm).like(
                    pattern,
                    escape="\\",
                ),
                func.lower(CleanDecision.spec_norm).like(
                    pattern,
                    escape="\\",
                ),
                func.lower(SourceDocument.logical_name).like(
                    pattern,
                    escape="\\",
                ),
                func.lower(SourceVariant.path).like(
                    pattern,
                    escape="\\",
                ),
            )
        )

    available_reason_codes = list(
        session.scalars(
            base.with_only_columns(
                CleanDecision.reason_code,
                maintain_column_froms=True,
            )
            .distinct()
            .order_by(CleanDecision.reason_code)
        )
    )
    if reason_code is not None:
        base = base.where(CleanDecision.reason_code == reason_code)
    if after_id is not None:
        base = base.where(RawQuoteItem.id > after_id)

    count_query = select(func.count()).select_from(base.subquery())
    total = session.scalar(count_query) or 0
    rows = session.execute(
        base.order_by(RawQuoteItem.id, CleanDecision.id)
        .limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    return {
        "items": [
            _review_item(raw, decision, variant, document)
            for raw, decision, variant, document in page_rows
        ],
        "remaining": total,
        "limit": limit,
        "next_cursor": (
            page_rows[-1][0].id if has_more and page_rows else None
        ),
        "available_reason_codes": available_reason_codes,
    }


@router.post(
    "/{raw_item_id}/decisions",
    status_code=201,
    response_model=DecisionResponse,
)
def append_manual_decision(
    raw_item_id: int,
    body: DecisionRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    if session.in_transaction():
        raise RuntimeError(
            "manual decision endpoint requires a fresh database session"
        )
    session.connection(
        execution_options={"sqlite_begin_mode": "IMMEDIATE"}
    )
    raw_item = session.get(RawQuoteItem, raw_item_id)
    if raw_item is None:
        session.rollback()
        raise HTTPException(status_code=404, detail="raw quote item not found")

    history = list(
        session.scalars(
            select(CleanDecision)
            .where(CleanDecision.raw_item_id == raw_item_id)
            .order_by(CleanDecision.id.desc())
        )
    )
    if not history:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="raw quote item has no cleansing baseline",
        )
    values = _preserved_values(history)
    current_id = session.scalar(
        select(func.max(CleanDecision.id)).where(
            CleanDecision.raw_item_id == raw_item_id
        )
    )
    if current_id != body.expected_current_decision_id:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "STALE_DECISION",
                "message": "cleansing decision changed; refresh and retry",
                "current_decision_id": current_id,
            },
        )

    decision = CleanDecision(
        raw_item=raw_item,
        status=CleanStatus(body.status),
        reason_code=body.reason_code,
        reason_detail=body.reason_detail,
        **values,
        rule_version="manual-v1",
        decided_by=body.decided_by,
    )
    session.add(decision)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="manual decision could not be appended",
        ) from exc
    except Exception:
        session.rollback()
        raise
    return _decision_summary(decision)


def _review_item(
    raw: RawQuoteItem,
    decision: CleanDecision,
    variant: SourceVariant,
    document: SourceDocument,
) -> dict[str, object]:
    return {
        "raw_item_id": raw.id,
        "raw": {
            "item_name": raw.item_name_raw,
            "spec": raw.spec_raw,
            "unit": raw.unit_raw,
            "quantity": raw.quantity_raw,
            "unit_price": raw.unit_price_raw,
            "amount": raw.amount_raw,
            "maker": raw.maker_raw,
        },
        "normalized": {
            "item_name": decision.item_name_norm,
            "spec": decision.spec_norm,
            "unit": decision.unit_norm,
            "quantity": _decimal_text(decision.quantity),
            "unit_price": _decimal_text(decision.unit_price),
            "amount": _decimal_text(decision.amount),
            "maker": decision.maker_norm,
        },
        "reason_code": decision.reason_code,
        "reason_detail": decision.reason_detail,
        "decision": _decision_summary(decision),
        "source": {
            "document_id": document.id,
            "logical_name": document.logical_name,
            "variant_id": variant.id,
            "path": variant.path,
            "sha256": variant.sha256,
            "security_state": variant.security_state,
            "selected_for_parsing_at_ingest": (
                variant.selected_for_parsing_at_ingest
            ),
            "sheet": raw.source_sheet,
            "page": raw.source_page,
            "row": raw.source_row,
            "cells": raw.source_cells,
            "parser_name": raw.parser_name,
            "parser_version": raw.parser_version,
            "parser_warnings": _parser_warnings(raw.parse_warnings_json),
        },
    }


def _decision_summary(decision: CleanDecision) -> dict[str, object]:
    return {
        "id": decision.id,
        "status": decision.status.value,
        "reason_code": decision.reason_code,
        "reason_detail": decision.reason_detail,
        "rule_version": decision.rule_version,
        "decided_by": decision.decided_by,
        "decided_at": decision.decided_at.isoformat(),
    }


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _preserved_values(
    history: list[CleanDecision],
) -> dict[str, object]:
    fields = (
        "item_name_norm",
        "spec_norm",
        "unit_norm",
        "maker_norm",
        "quantity",
        "unit_price",
        "amount",
    )
    return {
        field: next(
            (
                value
                for decision in history
                if (value := getattr(decision, field)) is not None
            ),
            None,
        )
        for field in fields
    }


def _parser_warnings(value: str) -> list[object]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return [{"code": "INVALID_WARNING_JSON", "raw": value}]
    return parsed if isinstance(parsed, list) else [parsed]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
