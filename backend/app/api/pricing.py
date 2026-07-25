"""Typed HTTP boundary for standard-price drafts and approvals."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.catalog.models import StandardPriceObservation, StandardPriceVersion
from app.db.session import get_session
from app.db.types import EXACT_DECIMAL_QUANTUM
from app.pricing.service import (
    NoEligiblePriceObservations,
    PriceDraftChanged,
    PriceObservationDraft,
    PriceSource,
    PricingNotFound,
    StandardPriceDraft,
    approve_standard_price,
    calculate_standard_price,
    standard_price_versions,
)


router = APIRouter()


class PriceStatisticsResponse(BaseModel):
    minimum: str
    median: str
    average: str
    maximum: str


class PriceSourceResponse(BaseModel):
    document_id: int
    logical_name: str
    variant_id: int
    path: str
    sheet: str | None
    page: int | None
    row: int | None


class DraftObservationResponse(BaseModel):
    raw_item_id: int
    clean_decision_id: int
    membership_decision_id: int
    metadata_version_id: int | None
    unit_price: str
    supplier_name: str | None
    quote_date: date | None
    source: PriceSourceResponse


class PriceExclusionResponse(BaseModel):
    raw_item_id: int
    reason: Literal[
        "EXCLUDED",
        "REVIEW_REQUIRED",
        "MEMBERSHIP_REJECTED",
        "MATCHED_TO_OTHER_ITEM",
        "MISSING_OR_INVALID_PRICE",
        "UNIT_INCOMPATIBLE",
    ]
    clean_decision_id: int | None
    membership_decision_id: int | None
    source: PriceSourceResponse


class PriceContextResponse(BaseModel):
    excluded_count: int
    review_required_count: int
    membership_rejected_count: int
    other_target_count: int
    invalid_price_count: int
    unit_incompatible_count: int


class PriceDraftResponse(BaseModel):
    standard_item_id: int
    standard_item_version_id: int
    canonical_unit: str | None
    observation_count: int
    supplier_count: int
    latest_quote_date: date | None
    prices: PriceStatisticsResponse
    observations: list[DraftObservationResponse]
    exclusions: list[PriceExclusionResponse]
    context: PriceContextResponse
    calculation_version: str
    fingerprint: str


class PriceApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_fingerprint: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    expected_current_version_id: int | None = Field(None, ge=1)
    approved_by: str = Field(min_length=1, max_length=100)


class ApprovedObservationResponse(BaseModel):
    raw_item_id: int
    clean_decision_id: int
    membership_decision_id: int
    source: PriceSourceResponse


class PriceVersionResponse(BaseModel):
    id: int
    standard_item_id: int
    version_number: int
    observation_count: int
    supplier_count: int
    latest_quote_date: date | None
    prices: PriceStatisticsResponse
    calculation_version: str
    approved_by: str
    approved_at: datetime
    observations: list[ApprovedObservationResponse]


class PriceVersionHistoryResponse(BaseModel):
    standard_item_id: int
    versions: list[PriceVersionResponse]


def _decimal(value: object) -> str:
    return format(
        Decimal(value).quantize(
            EXACT_DECIMAL_QUANTUM, rounding=ROUND_HALF_UP
        ),
        "f",
    )


def _source_payload(source: PriceSource) -> dict[str, object]:
    return {
        "document_id": source.document_id,
        "logical_name": source.logical_name,
        "variant_id": source.variant_id,
        "path": source.path,
        "sheet": source.source_sheet,
        "page": source.source_page,
        "row": source.source_row,
    }


def _draft_observation_payload(
    row: PriceObservationDraft,
) -> dict[str, object]:
    return {
        "raw_item_id": row.raw_item_id,
        "clean_decision_id": row.clean_decision_id,
        "membership_decision_id": row.membership_decision_id,
        "metadata_version_id": row.metadata_version_id,
        "unit_price": _decimal(row.unit_price),
        "supplier_name": row.supplier_name,
        "quote_date": row.quote_date,
        "source": _source_payload(row.source),
    }


def _draft_payload(draft: StandardPriceDraft) -> dict[str, object]:
    return {
        "standard_item_id": draft.standard_item_id,
        "standard_item_version_id": draft.standard_item_version_id,
        "canonical_unit": draft.canonical_unit,
        "observation_count": draft.observation_count,
        "supplier_count": draft.supplier_count,
        "latest_quote_date": draft.latest_quote_date,
        "prices": {
            "minimum": _decimal(draft.prices.minimum),
            "median": _decimal(draft.prices.median),
            "average": _decimal(draft.prices.average),
            "maximum": _decimal(draft.prices.maximum),
        },
        "observations": [
            _draft_observation_payload(row) for row in draft.observations
        ],
        "exclusions": [
            {
                "raw_item_id": row.raw_item_id,
                "reason": row.reason,
                "clean_decision_id": row.clean_decision_id,
                "membership_decision_id": row.membership_decision_id,
                "source": _source_payload(row.source),
            }
            for row in draft.exclusions
        ],
        "context": {
            "excluded_count": draft.context.excluded_count,
            "review_required_count": draft.context.review_required_count,
            "membership_rejected_count": (
                draft.context.membership_rejected_count
            ),
            "other_target_count": draft.context.other_target_count,
            "invalid_price_count": draft.context.invalid_price_count,
            "unit_incompatible_count": (
                draft.context.unit_incompatible_count
            ),
        },
        "calculation_version": draft.calculation_version,
        "fingerprint": draft.fingerprint,
    }


def _observation_source(row: StandardPriceObservation) -> PriceSource:
    raw = row.clean_decision.raw_item
    variant = raw.source_variant
    document = variant.document
    return PriceSource(
        document_id=document.id,
        logical_name=document.logical_name,
        variant_id=variant.id,
        path=variant.path,
        source_sheet=raw.source_sheet,
        source_page=raw.source_page,
        source_row=raw.source_row,
    )


def _version_payload(version: StandardPriceVersion) -> dict[str, object]:
    return {
        "id": version.id,
        "standard_item_id": version.standard_item_id,
        "version_number": version.version_number,
        "observation_count": version.observation_count,
        "supplier_count": version.supplier_count,
        "latest_quote_date": version.latest_quote_date,
        "prices": {
            "minimum": _decimal(version.minimum_price),
            "median": _decimal(version.median_price),
            "average": _decimal(version.average_price),
            "maximum": _decimal(version.maximum_price),
        },
        "calculation_version": version.calculation_version,
        "approved_by": version.approved_by,
        "approved_at": version.approved_at,
        "observations": [
            {
                "raw_item_id": row.raw_item_id,
                "clean_decision_id": row.clean_decision_id,
                "membership_decision_id": row.membership_decision_id,
                "source": _source_payload(_observation_source(row)),
            }
            for row in sorted(
                version.observations, key=lambda value: value.raw_item_id
            )
        ],
    }


def _raise_pricing_error(session: Session, exc: Exception) -> None:
    session.rollback()
    if isinstance(exc, PricingNotFound):
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "STANDARD_ITEM_NOT_FOUND",
                "message": str(exc),
            },
        ) from exc
    if isinstance(exc, PriceDraftChanged):
        raise HTTPException(
            status_code=409,
            detail={"error_code": exc.error_code, "message": str(exc)},
        ) from exc
    if isinstance(exc, NoEligiblePriceObservations):
        raise HTTPException(
            status_code=409,
            detail={"error_code": exc.error_code, "message": str(exc)},
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=422,
            detail={"error_code": "INVALID_PRICE_APPROVAL", "message": str(exc)},
        ) from exc
    if isinstance(exc, IntegrityError):
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "PRICE_VERSION_CONFLICT",
                "message": "concurrent standard-price approval conflict",
            },
        ) from exc
    raise exc


@router.get(
    "/standard-items/{standard_item_id}/draft",
    response_model=PriceDraftResponse,
)
def get_price_draft(
    standard_item_id: int,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        return _draft_payload(
            calculate_standard_price(session, standard_item_id)
        )
    except Exception as exc:
        _raise_pricing_error(session, exc)
        raise AssertionError("unreachable")


@router.get(
    "/standard-items/{standard_item_id}/versions",
    response_model=PriceVersionHistoryResponse,
)
def get_price_versions(
    standard_item_id: int,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        versions = standard_price_versions(session, standard_item_id)
        return {
            "standard_item_id": standard_item_id,
            "versions": [_version_payload(row) for row in versions],
        }
    except Exception as exc:
        _raise_pricing_error(session, exc)
        raise AssertionError("unreachable")


@router.post(
    "/standard-items/{standard_item_id}/versions",
    response_model=PriceVersionResponse,
    status_code=201,
)
def post_price_version(
    standard_item_id: int,
    body: PriceApprovalBody,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        if session.in_transaction():
            if session.new or session.dirty or session.deleted:
                raise RuntimeError(
                    "pricing mutation requires a clean session"
                )
            session.rollback()
        session.connection(
            execution_options={"sqlite_begin_mode": "IMMEDIATE"}
        )
        version = approve_standard_price(
            session,
            standard_item_id,
            expected_fingerprint=body.expected_fingerprint,
            expected_current_version_id=body.expected_current_version_id,
            approved_by=body.approved_by,
        )
        session.commit()
        return _version_payload(version)
    except Exception as exc:
        _raise_pricing_error(session, exc)
        raise AssertionError("unreachable")
