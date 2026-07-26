"""Typed HTTP boundary for standard-price drafts and approvals."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
)
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
    PricingVersionNotFound,
    StandardPriceDraft,
    approve_standard_price,
    calculate_standard_price,
    current_standard_price_version,
    standard_price_version,
    standard_price_versions,
)
from app.standard_database.read_service import (
    EvidenceQuality,
    evidence_quality,
    latest_build_provenance,
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
    clean_status: Literal[
        "INCLUDED", "EXCLUDED", "REVIEW_REQUIRED"
    ] | None
    membership_decision_id: int | None
    membership_status: Literal["MATCHED", "REJECTED"] | None
    membership_standard_item_id: int | None
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
    current_standard_price_version_id: int | None
    canonical_unit: str | None
    observation_count: int
    evidence_quality: EvidenceQuality
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
    metadata_version_id: int | None
    metadata: MetadataAuditResponse | None
    source: PriceSourceResponse


class StandardItemAuditResponse(BaseModel):
    id: int
    version_number: int
    canonical_name: str
    canonical_spec: str | None
    canonical_unit: str | None


class MetadataAuditResponse(BaseModel):
    id: int
    version_number: int
    supplier_name: str | None
    quote_date: date | None
    project_name: str | None


class PriceVersionResponse(BaseModel):
    id: int
    standard_item_id: int
    version_number: int
    observation_count: int
    evidence_quality: EvidenceQuality
    supplier_count: int
    latest_quote_date: date | None
    prices: PriceStatisticsResponse
    calculation_version: str
    audit_status: Literal["CAPTURED", "LEGACY_BACKFILL"]
    draft_fingerprint: str | None
    standard_item_version: StandardItemAuditResponse | None
    excluded_count: int
    review_required_count: int
    exclusions: list[PriceExclusionResponse]
    exclusion_context_valid: bool
    exclusion_context_error: str | None
    approved_by: str
    approved_at: datetime
    observations: list[ApprovedObservationResponse]


class PriceVersionHistoryResponse(BaseModel):
    standard_item_id: int
    versions: list[PriceVersionResponse]
    next_cursor: int | None
    limit: int
    latest_build: "PriceBuildProvenanceResponse | None"


class PriceBuildProvenanceResponse(BaseModel):
    build_run_id: int
    status: str
    built_at: datetime
    rule_version: str


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


def _draft_payload(
    draft: StandardPriceDraft,
    *,
    current_standard_price_version_id: int | None,
) -> dict[str, object]:
    return {
        "standard_item_id": draft.standard_item_id,
        "standard_item_version_id": draft.standard_item_version_id,
        "current_standard_price_version_id": (
            current_standard_price_version_id
        ),
        "canonical_unit": draft.canonical_unit,
        "observation_count": draft.observation_count,
        "evidence_quality": evidence_quality(
            draft.observation_count
        ).value,
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
                "clean_status": (
                    None
                    if row.clean_status is None
                    else row.clean_status.value
                ),
                "membership_decision_id": row.membership_decision_id,
                "membership_status": (
                    None
                    if row.membership_status is None
                    else row.membership_status.value
                ),
                "membership_standard_item_id": (
                    row.membership_standard_item_id
                ),
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


def _version_payload(
    version: StandardPriceVersion,
    *,
    include_observations: bool = True,
) -> dict[str, object]:
    exclusions, context_valid, context_error = _safe_exclusion_context(
        version.exclusion_context_json
    )
    item_version = version.standard_item_version
    return {
        "id": version.id,
        "standard_item_id": version.standard_item_id,
        "version_number": version.version_number,
        "observation_count": version.observation_count,
        "evidence_quality": evidence_quality(
            version.observation_count
        ).value,
        "supplier_count": version.supplier_count,
        "latest_quote_date": version.latest_quote_date,
        "prices": {
            "minimum": _decimal(version.minimum_price),
            "median": _decimal(version.median_price),
            "average": _decimal(version.average_price),
            "maximum": _decimal(version.maximum_price),
        },
        "calculation_version": version.calculation_version,
        "audit_status": version.audit_status.value,
        "draft_fingerprint": version.draft_fingerprint,
        "standard_item_version": (
            None
            if item_version is None
            else {
                "id": item_version.id,
                "version_number": item_version.version_number,
                "canonical_name": item_version.canonical_name,
                "canonical_spec": item_version.canonical_spec,
                "canonical_unit": item_version.canonical_unit,
            }
        ),
        "excluded_count": version.excluded_count,
        "review_required_count": version.review_required_count,
        "exclusions": exclusions,
        "exclusion_context_valid": context_valid,
        "exclusion_context_error": context_error,
        "approved_by": version.approved_by,
        "approved_at": version.approved_at,
        "observations": [] if not include_observations else [
            {
                "raw_item_id": row.raw_item_id,
                "clean_decision_id": row.clean_decision_id,
                "membership_decision_id": row.membership_decision_id,
                "metadata_version_id": row.metadata_version_id,
                "metadata": (
                    None
                    if row.metadata_version is None
                    else {
                        "id": row.metadata_version.id,
                        "version_number": (
                            row.metadata_version.version_number
                        ),
                        "supplier_name": (
                            row.metadata_version.supplier_name
                        ),
                        "quote_date": row.metadata_version.quote_date,
                        "project_name": row.metadata_version.project_name,
                    }
                ),
                "source": _source_payload(_observation_source(row)),
            }
            for row in sorted(
                version.observations, key=lambda value: value.raw_item_id
            )
        ],
    }


def _safe_exclusion_context(
    value: str,
) -> tuple[list[dict[str, object]], bool, str | None]:
    try:
        parsed = json.loads(value)
        validated = TypeAdapter(list[PriceExclusionResponse]).validate_python(
            parsed
        )
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        return [], False, f"INVALID_STORED_EXCLUSION_CONTEXT: {exc}"
    return [row.model_dump(mode="json") for row in validated], True, None


def _raise_pricing_error(session: Session, exc: Exception) -> None:
    session.rollback()
    if isinstance(exc, PricingVersionNotFound):
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "STANDARD_PRICE_VERSION_NOT_FOUND",
                "message": str(exc),
            },
        ) from exc
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
        draft = calculate_standard_price(session, standard_item_id)
        current = current_standard_price_version(session, standard_item_id)
        return _draft_payload(
            draft,
            current_standard_price_version_id=(
                None if current is None else current.id
            ),
        )
    except Exception as exc:
        _raise_pricing_error(session, exc)
        raise AssertionError("unreachable")


@router.get(
    "/standard-items/{standard_item_id}/versions/{version_id}",
    response_model=PriceVersionResponse,
)
def get_price_version(
    standard_item_id: int,
    version_id: int,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    try:
        return _version_payload(
            standard_price_version(session, standard_item_id, version_id)
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
    *,
    after_id: int | None = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=100),
    include_observations: bool = Query(True),
) -> dict[str, object]:
    try:
        versions, next_cursor = standard_price_versions(
            session,
            standard_item_id,
            after_id=after_id,
            limit=limit,
            include_observations=include_observations,
        )
        provenance = latest_build_provenance(session)
        return {
            "standard_item_id": standard_item_id,
            "versions": [
                _version_payload(
                    row,
                    include_observations=include_observations,
                )
                for row in versions
            ],
            "next_cursor": next_cursor,
            "limit": limit,
            "latest_build": (
                None
                if provenance is None
                else {
                    "build_run_id": provenance.build_run_id,
                    "status": provenance.status.value,
                    "built_at": provenance.built_at,
                    "rule_version": provenance.rule_version,
                }
            ),
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
