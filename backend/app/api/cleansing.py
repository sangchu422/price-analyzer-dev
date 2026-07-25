"""Review queue and append-only manual cleansing decisions."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cleansing.models import CleanDecision, CleanStatus
from app.db.session import get_session
from app.documents.models import SourceDocument, SourceVariant
from app.quotes.models import RawQuoteItem


router = APIRouter()


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: Literal["INCLUDED", "EXCLUDED"]
    reason_code: Literal["MANUAL_REVIEW"]
    reason_detail: str = Field(min_length=1, max_length=2000)
    decided_by: str = Field(min_length=1, max_length=100)

    @field_validator("decided_by")
    @classmethod
    def reject_system_actor(cls, value: str) -> str:
        if value.casefold() == "system":
            raise ValueError("SYSTEM is reserved for automatic decisions")
        return value


@router.get("/review-queue")
def review_queue(
    session: Session = Depends(get_session),
    *,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    reason_code: str | None = Query(None, min_length=1, max_length=100),
    logical_name: str | None = Query(None, min_length=1, max_length=500),
) -> dict[str, object]:
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
    if reason_code is not None:
        base = base.where(CleanDecision.reason_code == reason_code)
    if logical_name is not None:
        base = base.where(SourceDocument.logical_name == logical_name)

    count_query = select(func.count()).select_from(base.subquery())
    total = session.scalar(count_query) or 0
    rows = session.execute(
        base.order_by(RawQuoteItem.id, CleanDecision.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [
            _review_item(raw, decision, variant, document)
            for raw, decision, variant, document in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/{raw_item_id}/decisions", status_code=201)
def append_manual_decision(
    raw_item_id: int,
    body: DecisionRequest,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    raw_item = session.get(RawQuoteItem, raw_item_id)
    if raw_item is None:
        raise HTTPException(status_code=404, detail="raw quote item not found")

    history = list(
        session.scalars(
            select(CleanDecision)
            .where(CleanDecision.raw_item_id == raw_item_id)
            .order_by(CleanDecision.id.desc())
        )
    )
    if not history:
        raise HTTPException(
            status_code=409,
            detail="raw quote item has no cleansing baseline",
        )
    values = _preserved_values(history)

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
