"""REST boundary for human-approved standard-item grouping."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.catalog.models import (
    DocumentMetadataCandidate,
    DocumentMetadataScan,
    DocumentMetadataVersion,
    ItemMembershipDecision,
    MembershipStatus,
    StandardPriceObservation,
    StandardItemVersion,
)
from app.catalog.service import (
    CandidateEmbeddingRuntime,
    CatalogConflict,
    CatalogNotFound,
    append_document_metadata,
    append_membership_decision,
    append_standard_item_version,
    build_candidate_embedding_runtime,
    candidate_matches,
    create_standard_item,
    standard_item_members,
    unmatched_included,
)
from app.api.xlsx_export import build_xlsx_response
from app.cleansing.models import CleanDecision, CleanStatus
from app.core.config import settings
from app.db.session import get_session
from app.documents.models import SourceDocument, SourceVariant
from app.metadata_audit.service import AUDIT_RULE_VERSION
from app.procurement.categories import current_category_subquery
from app.procurement.models import ItemCategory
from app.quotes.models import RawQuoteItem
from app.standard_database.read_service import (
    EvidenceQuality,
    StandardBuildProvenance,
    StandardExplorerNotFound,
    StandardExplorerSummary,
    evidence_quality,
    list_standard_explorer_items,
    standard_item_evidence,
)


router = APIRouter()
MAX_ALIAS_LENGTH = 500
MAX_ALIASES_TOTAL_LENGTH = 20_000
MAX_EVIDENCE_JSON_BYTES = 65_536
MAX_EVIDENCE_JSON_DEPTH = 8


class MetadataAuditSummaryResponse(BaseModel):
    scanned_files: int
    parsed_files: int
    standard_price_files: int
    unparsed_files: int
    ocr_required_files: int
    parser_required_files: int
    recollection_required_files: int
    recovered_copy_files: int
    security_release_required_files: int
    unsupported_files: int
    raw_item_count: int
    auto_confirmed_files: int
    review_required_files: int
    failed_files: int
    candidate_count: int
    accepted_candidate_count: int


class MetadataCandidateResponse(BaseModel):
    field_name: str
    value_text: str
    source_kind: str
    confidence: int
    status: str
    source_sheet: str | None
    source_page: int | None
    source_cells: str | None


class MetadataAuditDocumentResponse(BaseModel):
    scan_id: int
    document_id: int | None
    variant_id: int | None
    file_name: str
    acquisition_channel: str | None
    open_status: str
    content_status: str
    review_status: str
    candidates: list[MetadataCandidateResponse]


class MetadataAuditDocumentListResponse(BaseModel):
    items: list[MetadataAuditDocumentResponse]
    next_cursor: int | None
    limit: int


class AuditBody(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    reason_detail: str = Field(min_length=1, max_length=2000)


class StandardItemBody(AuditBody):
    canonical_name: str = Field(min_length=1, max_length=1000)
    canonical_spec: str | None = Field(default=None, max_length=2000)
    canonical_unit: str | None = Field(default=None, max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    created_by: str = Field(min_length=1, max_length=100)

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("aliases must not contain blank values")
        if any(len(value) > MAX_ALIAS_LENGTH for value in cleaned):
            raise ValueError(
                f"each alias must be at most {MAX_ALIAS_LENGTH} characters"
            )
        if sum(map(len, cleaned)) > MAX_ALIASES_TOTAL_LENGTH:
            raise ValueError("aliases payload is too large")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("aliases must be unique")
        return cleaned

    @field_validator("created_by")
    @classmethod
    def actor_is_human(cls, value: str) -> str:
        if value.casefold() == "system":
            raise ValueError("SYSTEM is reserved for automatic operations")
        return value


class StandardItemVersionBody(StandardItemBody):
    expected_current_version_id: int = Field(gt=0)


class CreateAndMatchBody(StandardItemBody):
    expected_current_decision_id: int | None = Field(default=None, gt=0)


class MembershipBody(AuditBody):
    standard_item_id: int | None = Field(default=None, gt=0)
    status: MembershipStatus
    expected_current_decision_id: int | None = Field(default=None, gt=0)
    candidate_score: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        le=Decimal("1"),
        decimal_places=6,
    )
    method: str = Field(min_length=1, max_length=100)
    evidence: dict[str, Any]
    decided_by: str = Field(min_length=1, max_length=100)

    @field_validator("decided_by")
    @classmethod
    def decision_actor_is_human(cls, value: str) -> str:
        if value.casefold() == "system":
            raise ValueError("SYSTEM cannot make a manual catalog decision")
        return value

    @field_validator("evidence")
    @classmethod
    def validate_evidence(cls, value: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_EVIDENCE_JSON_BYTES:
            raise ValueError("evidence JSON is too large")
        if _json_depth_exceeds(value, MAX_EVIDENCE_JSON_DEPTH):
            raise ValueError("evidence JSON is too deeply nested")
        return value

    @model_validator(mode="after")
    def target_matches_status(self) -> MembershipBody:
        if (
            self.status == MembershipStatus.MATCHED
            and self.standard_item_id is None
        ):
            raise ValueError("MATCHED requires standard_item_id")
        if (
            self.status == MembershipStatus.REJECTED
            and self.standard_item_id is not None
        ):
            raise ValueError("REJECTED must not include standard_item_id")
        return self


class DocumentMetadataBody(AuditBody):
    supplier_name: str | None = Field(default=None, max_length=1000)
    quote_date: date | None = None
    project_name: str | None = Field(default=None, max_length=1000)
    expected_current_version_id: int | None = Field(default=None, gt=0)
    decided_by: str = Field(min_length=1, max_length=100)

    @field_validator("decided_by")
    @classmethod
    def metadata_actor_is_human(cls, value: str) -> str:
        if value.casefold() == "system":
            raise ValueError("SYSTEM cannot make a manual metadata decision")
        return value


class StandardItemVersionResponse(BaseModel):
    id: int
    standard_item_id: int
    version_number: int
    canonical_name: str
    canonical_spec: str | None
    canonical_unit: str | None
    aliases: list[str]
    created_by: str
    reason_detail: str
    created_at: datetime


class StandardItemResponse(BaseModel):
    id: int
    current_version: StandardItemVersionResponse


class StandardItemSummaryResponse(StandardItemResponse):
    member_count: int
    observation_count: int | None
    supplier_count: int | None
    current_price_version_id: int | None
    captured_price_version_id: int | None
    operational_status: str
    evidence_quality: EvidenceQuality | None
    current_price: "ExplorerPriceResponse | None"
    supplier_summary: list[str]
    maker_summary: list[str]
    quote_date_start: date | None
    quote_date_end: date | None
    quote_date_end_quality: str | None
    spec_source_status: str
    provenance: "BuildProvenanceResponse | None"
    category_code: str | None = None
    category_name: str | None = None
    category_confidence: Decimal | None = None


class ExplorerPriceResponse(BaseModel):
    minimum: str
    median: str
    average: str
    maximum: str


class BuildProvenanceResponse(BaseModel):
    build_run_id: int
    status: str
    built_at: datetime
    rule_version: str
    input_fingerprint: str
    calculation_fingerprint: str
    code_fingerprint: str


class StandardItemListResponse(BaseModel):
    items: list[StandardItemSummaryResponse]
    next_cursor: int | None
    limit: int
    latest_build: BuildProvenanceResponse | None


class StandardEvidenceSourceResponse(BaseModel):
    document_id: int
    logical_name: str
    variant_id: int
    path: str
    sheet: str | None
    page: int | None
    row: int | None
    cells: str | None


class StandardEvidenceRowResponse(BaseModel):
    raw_item_id: int
    unit_price: str
    supplier_name: str | None
    maker: str | None
    quote_date: date | None
    quote_date_quality: str | None
    source: StandardEvidenceSourceResponse


class StandardEvidenceResponse(BaseModel):
    standard_item_id: int
    standard_price_version_id: int
    observation_count: int
    supplier_count: int
    evidence_quality: EvidenceQuality
    provenance: BuildProvenanceResponse | None
    observations: list[StandardEvidenceRowResponse]
    next_cursor: int | None
    limit: int


class MembershipResponse(BaseModel):
    id: int
    raw_item_id: int
    standard_item_id: int | None
    status: MembershipStatus
    candidate_score: str | None
    method: str
    evidence: dict[str, Any]
    supersedes_decision_id: int | None
    decided_by: str
    decided_at: datetime


class CreateAndMatchResponse(BaseModel):
    standard_item: StandardItemResponse
    membership: MembershipResponse


class DocumentMetadataResponse(BaseModel):
    id: int
    source_document_id: int
    version_number: int
    supplier_name: str | None
    quote_date: date | None
    project_name: str | None
    decided_by: str
    reason_detail: str
    evidence: dict[str, Any]
    created_at: datetime


class UnmatchedItemResponse(BaseModel):
    raw_item_id: int
    name: str | None
    spec: str | None
    unit: str | None
    current_cleansing_decision_id: int
    current_membership_decision_id: int | None


class UnmatchedResponse(BaseModel):
    items: list[UnmatchedItemResponse]
    next_cursor: int | None
    limit: int


class CandidateRawResponse(BaseModel):
    id: int
    name: str | None
    spec: str | None
    unit: str | None
    quantity: str | None
    unit_price: str | None
    amount: str | None


class CandidateNormalizedResponse(BaseModel):
    name: str | None
    spec: str | None
    unit: str | None
    quantity: str | None
    unit_price: str | None
    amount: str | None


class CandidateCleanDecisionResponse(BaseModel):
    id: int
    status: CleanStatus
    reason_code: str
    reason_detail: str | None
    rule_version: str


class CandidateSourceResponse(BaseModel):
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


class CandidateEvidenceResponse(BaseModel):
    standard_item_id: int
    standard_item_version_id: int
    canonical_name: str
    canonical_spec: str | None
    canonical_unit: str | None
    aliases: list[str]
    name_score: str
    spec_score: str
    token_score: str
    embedding_score: str | None
    embedding_status: Literal[
        "DISABLED",
        "UNAVAILABLE",
        "AVAILABLE",
        "MOCK_ONLY",
    ]
    embedding_model: str | None
    final_score: str
    matched_tokens: list[str]
    method: str
    unit_compatible: bool
    model_tokens_compatible: bool


class CandidateResponse(BaseModel):
    match_status: Literal["CANDIDATE", "NO_MATCH"]
    raw_item: CandidateRawResponse
    normalized: CandidateNormalizedResponse
    current_cleansing_decision: CandidateCleanDecisionResponse
    current_membership_decision_id: int | None
    current_document_metadata: DocumentMetadataResponse | None
    source: CandidateSourceResponse
    candidates: list[CandidateEvidenceResponse]


class StandardItemMemberResponse(BaseModel):
    raw_item_id: int
    name: str | None
    spec: str | None
    unit: str | None
    unit_price: str | None
    clean_decision_id: int
    membership_decision_id: int
    current_cleansing_decision: CandidateCleanDecisionResponse
    source: CandidateSourceResponse


class StandardItemMembersResponse(BaseModel):
    standard_item_id: int
    members: list[StandardItemMemberResponse]
    next_cursor: int | None
    limit: int


def _begin_immediate(session: Session) -> None:
    if session.in_transaction():
        raise RuntimeError("catalog mutation requires a fresh session")
    session.connection(execution_options={"sqlite_begin_mode": "IMMEDIATE"})


def _json_depth_exceeds(value: Any, maximum: int) -> bool:
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > maximum:
            return True
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
    return False


def get_candidate_embedding_runtime(
    session: Session = Depends(get_session),
) -> CandidateEmbeddingRuntime:
    return build_candidate_embedding_runtime(session, settings=settings)


def _conflict_detail(exc: CatalogConflict) -> dict[str, object]:
    detail: dict[str, object] = {
        "error_code": exc.error_code,
        "message": str(exc),
    }
    if exc.current_id is not None:
        detail[exc.current_key] = exc.current_id
    return detail


def _raise_catalog_error(session: Session, exc: Exception) -> None:
    session.rollback()
    if isinstance(exc, CatalogNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, CatalogConflict):
        raise HTTPException(
            status_code=409,
            detail=_conflict_detail(exc),
        ) from exc
    if isinstance(exc, (IntegrityError, ValueError)):
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "CATALOG_WRITE_CONFLICT",
                "message": "catalog history could not be appended",
            },
        ) from exc
    raise exc


def _commit(session: Session) -> None:
    session.commit()


def _version_payload(version: StandardItemVersion) -> dict[str, object]:
    return {
        "id": version.id,
        "standard_item_id": version.standard_item_id,
        "version_number": version.version_number,
        "canonical_name": version.canonical_name,
        "canonical_spec": version.canonical_spec,
        "canonical_unit": version.canonical_unit,
        "aliases": json.loads(version.aliases_json),
        "created_by": version.created_by,
        "reason_detail": version.change_reason,
        "created_at": version.created_at,
    }


def _clean_decision_payload(clean: CleanDecision) -> dict[str, object]:
    return {
        "id": clean.id,
        "status": clean.status.value,
        "reason_code": clean.reason_code,
        "reason_detail": clean.reason_detail,
        "rule_version": clean.rule_version,
    }


def _source_payload(
    raw: RawQuoteItem,
    variant: SourceVariant,
    document: SourceDocument,
) -> dict[str, object]:
    return {
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
    }


def _metadata_payload(
    row: DocumentMetadataVersion,
) -> dict[str, object]:
    return {
        "id": row.id,
        "source_document_id": row.source_document_id,
        "version_number": row.version_number,
        "supplier_name": row.supplier_name,
        "quote_date": row.quote_date,
        "project_name": row.project_name,
        "decided_by": row.decided_by,
        "reason_detail": row.reason_detail,
        "evidence": _json_object(row.evidence_json),
        "created_at": row.created_at,
    }


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _membership_payload(
    row: ItemMembershipDecision,
) -> dict[str, object]:
    return {
        "id": row.id,
        "raw_item_id": row.raw_item_id,
        "standard_item_id": row.standard_item_id,
        "status": row.status.value,
        "candidate_score": (
            None
            if row.candidate_score is None
            else format(row.candidate_score, "f")
        ),
        "method": row.method,
        "evidence": json.loads(row.evidence_json),
        "supersedes_decision_id": row.supersedes_decision_id,
        "decided_by": row.decided_by,
        "decided_at": row.decided_at,
    }


def _build_provenance_payload(
    provenance: StandardBuildProvenance | None,
) -> dict[str, object] | None:
    if provenance is None:
        return None
    return {
        "build_run_id": provenance.build_run_id,
        "status": provenance.status.value,
        "built_at": provenance.built_at,
        "rule_version": provenance.rule_version,
        "input_fingerprint": provenance.input_fingerprint,
        "calculation_fingerprint": provenance.calculation_fingerprint,
        "code_fingerprint": provenance.code_fingerprint,
    }


def _explorer_price_payload(
    summary: StandardExplorerSummary,
) -> dict[str, str] | None:
    price = summary.current_price
    if price is None:
        return None
    return {
        "minimum": format(price.minimum_price, "f"),
        "median": format(price.median_price, "f"),
        "average": format(price.average_price, "f"),
        "maximum": format(price.maximum_price, "f"),
    }


def _explorer_summary_payload(
    summary: StandardExplorerSummary,
) -> dict[str, object]:
    price = summary.current_price
    observation_count = None if price is None else price.observation_count
    supplier_count = None if price is None else price.supplier_count
    return {
        "id": summary.current_version.standard_item_id,
        "current_version": _version_payload(summary.current_version),
        "member_count": summary.member_count,
        "observation_count": observation_count,
        "supplier_count": supplier_count,
        "current_price_version_id": None if price is None else price.id,
        "captured_price_version_id": summary.captured_price_version_id,
        "operational_status": summary.operational_status.value,
        "evidence_quality": (
            None
            if summary.evidence_quality is None
            else summary.evidence_quality.value
        ),
        "current_price": _explorer_price_payload(summary),
        "supplier_summary": list(summary.supplier_summary),
        "maker_summary": list(summary.maker_summary),
        "quote_date_start": summary.quote_date_start,
        "quote_date_end": summary.quote_date_end,
        "quote_date_end_quality": (
            None
            if summary.quote_date_end_quality is None
            else summary.quote_date_end_quality.value
        ),
        "spec_source_status": summary.spec_source_status,
        "provenance": _build_provenance_payload(summary.provenance),
    }


def _category_payloads(
    session: Session,
    standard_item_ids: list[int],
) -> dict[int, dict[str, object]]:
    if not standard_item_ids or not inspect(session.get_bind()).has_table(
        "standard_item_category_assignment"
    ):
        return {}
    current_categories = current_category_subquery(name="api_current_categories")
    return {
        standard_item_id: {
            "category_code": code,
            "category_name": name,
            "category_confidence": confidence,
        }
        for standard_item_id, code, name, confidence in session.execute(
            select(
                current_categories.c.standard_item_id,
                ItemCategory.code,
                ItemCategory.name,
                current_categories.c.confidence,
            )
            .join(
                ItemCategory,
                ItemCategory.id == current_categories.c.category_id,
            )
            .where(
                current_categories.c.standard_item_id.in_(standard_item_ids)
            )
        )
    }


@router.get(
    "/metadata-audit/summary",
    response_model=MetadataAuditSummaryResponse,
)
def get_metadata_audit_summary(
    session: Session = Depends(get_session),
) -> dict[str, int]:
    scans = list(
        session.scalars(
            select(DocumentMetadataScan).where(
                DocumentMetadataScan.rule_version == AUDIT_RULE_VERSION
            )
        )
    )
    variant_ids = {
        row.source_variant_id
        for row in scans
        if row.source_variant_id is not None
    }
    raw_counts = {
        variant_id: count
        for variant_id, count in session.execute(
            select(
                RawQuoteItem.source_variant_id,
                func.count(RawQuoteItem.id),
            )
            .where(RawQuoteItem.source_variant_id.in_(variant_ids))
            .group_by(RawQuoteItem.source_variant_id)
        )
    } if variant_ids else {}
    parsed_variant_ids = {
        variant_id for variant_id, count in raw_counts.items() if count > 0
    }
    standard_price_variant_ids = set(
        session.scalars(
            select(RawQuoteItem.source_variant_id)
            .join(
                StandardPriceObservation,
                StandardPriceObservation.raw_item_id == RawQuoteItem.id,
            )
            .where(RawQuoteItem.source_variant_id.in_(variant_ids))
            .distinct()
        )
    ) if variant_ids else set()
    unparsed = [
        row for row in scans
        if row.source_variant_id not in parsed_variant_ids
    ]
    recovered_copy_files = sum(
        "복구본/3차 학습/" in path.replace("\\", "/")
        for path in session.scalars(select(SourceVariant.path))
    )
    recollection_required_files = sum(
        row.open_status == "FAILED" for row in unparsed
    )
    return {
        "scanned_files": len(scans),
        "parsed_files": len(parsed_variant_ids),
        "standard_price_files": len(standard_price_variant_ids),
        "unparsed_files": len(unparsed),
        "ocr_required_files": sum(
            row.open_status == "OPENED"
            and row.content_status == "OCR_REQUIRED"
            for row in unparsed
        ),
        "parser_required_files": sum(
            row.open_status == "OPENED"
            and row.content_status == "TEXT"
            for row in unparsed
        ),
        "recollection_required_files": recollection_required_files,
        "recovered_copy_files": recovered_copy_files,
        "security_release_required_files": max(
            0,
            recollection_required_files - recovered_copy_files,
        ),
        "unsupported_files": sum(
            row.open_status == "UNSUPPORTED" for row in unparsed
        ),
        "raw_item_count": sum(raw_counts.values()),
        "auto_confirmed_files": sum(
            row.review_status == "AUTO_CONFIRMED" for row in scans
        ),
        "review_required_files": sum(
            row.review_status == "REVIEW_REQUIRED" for row in scans
        ),
        "failed_files": sum(row.open_status == "FAILED" for row in scans),
        "candidate_count": session.scalar(
            select(func.count(DocumentMetadataCandidate.id))
            .join(
                DocumentMetadataScan,
                DocumentMetadataScan.id == DocumentMetadataCandidate.scan_id,
            )
            .where(DocumentMetadataScan.rule_version == AUDIT_RULE_VERSION)
        ) or 0,
        "accepted_candidate_count": session.scalar(
            select(func.count(DocumentMetadataCandidate.id))
            .join(
                DocumentMetadataScan,
                DocumentMetadataScan.id == DocumentMetadataCandidate.scan_id,
            )
            .where(
                DocumentMetadataScan.rule_version == AUDIT_RULE_VERSION,
                DocumentMetadataCandidate.status == "AUTO_ACCEPTED",
            )
        ) or 0,
    }


@router.get(
    "/metadata-audit/documents",
    response_model=MetadataAuditDocumentListResponse,
)
def get_metadata_audit_documents(
    session: Session = Depends(get_session),
    *,
    after_id: int | None = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=100),
    review_status: str | None = Query(None, max_length=32),
) -> dict[str, object]:
    statement = (
        select(DocumentMetadataScan, SourceVariant)
        .outerjoin(SourceVariant, SourceVariant.id == DocumentMetadataScan.source_variant_id)
        .where(DocumentMetadataScan.rule_version == AUDIT_RULE_VERSION)
        .order_by(DocumentMetadataScan.id)
    )
    if after_id is not None:
        statement = statement.where(DocumentMetadataScan.id > after_id)
    if review_status:
        statement = statement.where(
            DocumentMetadataScan.review_status == review_status
        )
    rows = session.execute(statement.limit(limit + 1)).all()
    page = rows[:limit]
    scan_ids = [scan.id for scan, _ in page]
    candidates_by_scan: dict[int, list[DocumentMetadataCandidate]] = {}
    if scan_ids:
        for candidate in session.scalars(
            select(DocumentMetadataCandidate)
            .where(DocumentMetadataCandidate.scan_id.in_(scan_ids))
            .order_by(DocumentMetadataCandidate.id)
        ):
            candidates_by_scan.setdefault(candidate.scan_id, []).append(candidate)
    return {
        "items": [
            {
                "scan_id": scan.id,
                "document_id": None if variant is None else variant.document_id,
                "variant_id": None if variant is None else variant.id,
                "file_name": Path(scan.source_path).name,
                "acquisition_channel": scan.acquisition_channel,
                "open_status": scan.open_status,
                "content_status": scan.content_status,
                "review_status": scan.review_status,
                "candidates": [
                    {
                        "field_name": candidate.field_name,
                        "value_text": candidate.value_text,
                        "source_kind": candidate.source_kind,
                        "confidence": candidate.confidence,
                        "status": candidate.status,
                        "source_sheet": candidate.source_sheet,
                        "source_page": candidate.source_page,
                        "source_cells": candidate.source_cells,
                    }
                    for candidate in candidates_by_scan.get(scan.id, [])
                ],
            }
            for scan, variant in page
        ],
        "next_cursor": page[-1][0].id if len(rows) > limit and page else None,
        "limit": limit,
    }


@router.get(
    "/standard-items",
    response_model=StandardItemListResponse,
)
def get_standard_items(
    session: Session = Depends(get_session),
    *,
    after_id: int | None = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: str | None = Query(None, max_length=200),
    evidence_quality: EvidenceQuality | None = Query(None),
    category: str | None = Query(None, max_length=64),
) -> dict[str, object]:
    rows, next_cursor, latest_build = list_standard_explorer_items(
        session,
        after_id=after_id,
        limit=limit,
        search=search,
        quality=evidence_quality,
        category_code=category,
    )
    categories = _category_payloads(
        session,
        [row.current_version.standard_item_id for row in rows],
    )
    items: list[dict[str, object]] = []
    for row in rows:
        payload = _explorer_summary_payload(row)
        payload.update(categories.get(row.current_version.standard_item_id, {}))
        items.append(payload)
    return {
        "items": items,
        "next_cursor": next_cursor,
        "limit": limit,
        "latest_build": _build_provenance_payload(latest_build),
    }


@router.get("/standard-items/export")
def export_standard_items(
    session: Session = Depends(get_session),
    *,
    search: str | None = Query(None, max_length=200),
    evidence_quality: EvidenceQuality | None = Query(None),
    category: str | None = Query(None, max_length=64),
) -> Response:
    headers = [
        "품명", "규격", "단위", "최저", "중앙값", "평균", "최고",
        "근거 건수", "제품 제조사", "견적 제출사", "최근 견적일",
    ]
    rows: list[list[object]] = []
    after_id: int | None = None
    while True:
        chunk, next_cursor, _latest_build = list_standard_explorer_items(
            session,
            after_id=after_id,
            limit=200,
            search=search,
            quality=evidence_quality,
            category_code=category,
        )
        for summary in chunk:
            payload = _explorer_summary_payload(summary)
            version = payload["current_version"]
            price = payload["current_price"]
            rows.append([
                version["canonical_name"],
                version["canonical_spec"] or "",
                version["canonical_unit"] or "",
                price["minimum"] if price else None,
                price["median"] if price else None,
                price["average"] if price else None,
                price["maximum"] if price else None,
                payload["observation_count"],
                ", ".join(payload["maker_summary"]),
                ", ".join(payload["supplier_summary"]),
                payload["quote_date_end"] or "",
            ])
        session.commit()
        if next_cursor is None:
            break
        after_id = next_cursor

    return build_xlsx_response(
        sheet_title="표준 품목 목록",
        headers=headers,
        rows=rows,
        filename=f"표준품목목록_{date.today():%Y%m%d}.xlsx",
    )


@router.get(
    "/standard-items/{standard_item_id}/evidence",
    response_model=StandardEvidenceResponse,
)
def get_standard_item_evidence(
    standard_item_id: int,
    session: Session = Depends(get_session),
    *,
    price_version_id: int = Query(..., gt=0),
    after_id: int | None = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> dict[str, object]:
    try:
        price, observations, next_cursor, provenance = (
            standard_item_evidence(
                session,
                standard_item_id,
                price_version_id=price_version_id,
                after_id=after_id,
                limit=limit,
            )
        )
    except StandardExplorerNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "standard_item_id": standard_item_id,
        "standard_price_version_id": price.id,
        "observation_count": price.observation_count,
        "supplier_count": price.supplier_count,
        "evidence_quality": evidence_quality(price.supplier_count).value,
        "provenance": _build_provenance_payload(provenance),
        "observations": [
            {
                "raw_item_id": row.raw_item_id,
                "unit_price": format(row.unit_price, "f"),
                "supplier_name": row.supplier_name,
                "maker": row.maker,
                "quote_date": row.quote_date,
                "quote_date_quality": (
                    None
                    if row.quote_date_quality is None
                    else row.quote_date_quality.value
                ),
                "source": {
                    "document_id": row.document_id,
                    "logical_name": row.logical_name,
                    "variant_id": row.variant_id,
                    "path": row.path,
                    "sheet": row.sheet,
                    "page": row.page,
                    "row": row.row,
                    "cells": row.cells,
                },
            }
            for row in observations
        ],
        "next_cursor": next_cursor,
        "limit": limit,
    }


@router.get("/unmatched", response_model=UnmatchedResponse)
def get_unmatched(
    session: Session = Depends(get_session),
    *,
    after_id: int | None = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: str | None = Query(None, max_length=200),
) -> dict[str, object]:
    rows, next_cursor = unmatched_included(
        session,
        after_id=after_id,
        limit=limit,
        search=search,
    )
    return {
        "items": [
            {
                "raw_item_id": raw.id,
                "name": clean.item_name_norm,
                "spec": clean.spec_norm,
                "unit": clean.unit_norm,
                "current_cleansing_decision_id": clean.id,
                "current_membership_decision_id": membership_id,
            }
            for raw, clean, membership_id in rows
        ],
        "next_cursor": next_cursor,
        "limit": limit,
    }


@router.get(
    "/raw-items/{raw_item_id}/candidates",
    response_model=CandidateResponse,
)
def get_candidates(
    raw_item_id: int,
    session: Session = Depends(get_session),
    embedding_runtime: CandidateEmbeddingRuntime = Depends(
        get_candidate_embedding_runtime
    ),
    *,
    top_n: int = Query(10, ge=1, le=50),
) -> dict[str, object]:
    try:
        result = candidate_matches(
            session,
            raw_item_id,
            top_n=top_n,
            embedding_runtime=embedding_runtime,
        )
    except (CatalogNotFound, CatalogConflict) as exc:
        _raise_catalog_error(session, exc)
        raise AssertionError("unreachable")
    raw = result.raw_item
    clean = result.current_cleansing_decision
    variant = result.source_variant
    document = result.source_document
    return {
        "match_status": result.match_status,
        "raw_item": {
            "id": raw.id,
            "name": raw.item_name_raw,
            "spec": raw.spec_raw,
            "unit": raw.unit_raw,
            "quantity": raw.quantity_raw,
            "unit_price": raw.unit_price_raw,
            "amount": raw.amount_raw,
        },
        "normalized": {
            "name": clean.item_name_norm,
            "spec": clean.spec_norm,
            "unit": clean.unit_norm,
            "quantity": (
                None
                if clean.quantity is None
                else format(clean.quantity, ".6f")
            ),
            "unit_price": (
                None
                if clean.unit_price is None
                else format(clean.unit_price, ".6f")
            ),
            "amount": (
                None
                if clean.amount is None
                else format(clean.amount, ".6f")
            ),
        },
        "current_cleansing_decision": _clean_decision_payload(clean),
        "current_membership_decision_id": (
            None
            if result.current_membership_decision is None
            else result.current_membership_decision.id
        ),
        "current_document_metadata": (
            None
            if result.current_document_metadata is None
            else _metadata_payload(result.current_document_metadata)
        ),
        "source": _source_payload(raw, variant, document),
        "candidates": [
            {
                "standard_item_id": candidate.standard_item_id,
                "standard_item_version_id": candidate.version_id,
                "canonical_name": candidate.canonical_name,
                "canonical_spec": candidate.canonical_spec,
                "canonical_unit": candidate.canonical_unit,
                "aliases": list(candidate.aliases),
                "name_score": format(candidate.score.name_score, "f"),
                "spec_score": format(candidate.score.spec_score, "f"),
                "token_score": format(candidate.score.token_score, "f"),
                "embedding_score": (
                    None
                    if candidate.score.embedding_score is None
                    else format(candidate.score.embedding_score, "f")
                ),
                "embedding_status": candidate.score.embedding_status,
                "embedding_model": result.embedding_model,
                "final_score": format(candidate.score.final_score, "f"),
                "matched_tokens": list(candidate.score.matched_tokens),
                "method": candidate.score.method,
                "unit_compatible": candidate.unit_compatible,
                "model_tokens_compatible": (
                    candidate.model_tokens_compatible
                ),
            }
            for candidate in result.candidates
        ],
    }


@router.post(
    "/standard-items",
    status_code=201,
    response_model=StandardItemResponse,
)
def post_standard_item(
    body: StandardItemBody,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    _begin_immediate(session)
    try:
        item, version = create_standard_item(
            session,
            canonical_name=body.canonical_name,
            canonical_spec=body.canonical_spec,
            canonical_unit=body.canonical_unit,
            aliases=body.aliases,
            created_by=body.created_by,
            reason_detail=body.reason_detail,
        )
        _commit(session)
    except Exception as exc:
        _raise_catalog_error(session, exc)
        raise AssertionError("unreachable")
    return {"id": item.id, "current_version": _version_payload(version)}


@router.post(
    "/raw-items/{raw_item_id}/standard-item",
    status_code=201,
    response_model=CreateAndMatchResponse,
)
def post_standard_item_and_membership(
    raw_item_id: int,
    body: CreateAndMatchBody,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    """Create a catalog identity and its first membership atomically."""

    _begin_immediate(session)
    try:
        item, version = create_standard_item(
            session,
            canonical_name=body.canonical_name,
            canonical_spec=body.canonical_spec,
            canonical_unit=body.canonical_unit,
            aliases=body.aliases,
            created_by=body.created_by,
            reason_detail=body.reason_detail,
        )
        membership = append_membership_decision(
            session,
            raw_item_id=raw_item_id,
            standard_item_id=item.id,
            status=MembershipStatus.MATCHED,
            expected_current_decision_id=(
                body.expected_current_decision_id
            ),
            candidate_score=None,
            method="MANUAL_NEW_STANDARD_ITEM",
            evidence={
                "created_standard_item_version_id": version.id,
            },
            decided_by=body.created_by,
            reason_detail=body.reason_detail,
        )
        _commit(session)
    except Exception as exc:
        _raise_catalog_error(session, exc)
        raise AssertionError("unreachable")
    return {
        "standard_item": {
            "id": item.id,
            "current_version": _version_payload(version),
        },
        "membership": _membership_payload(membership),
    }


@router.post(
    "/standard-items/{standard_item_id}/versions",
    status_code=201,
    response_model=StandardItemVersionResponse,
)
def post_standard_item_version(
    standard_item_id: int,
    body: StandardItemVersionBody,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    _begin_immediate(session)
    try:
        version = append_standard_item_version(
            session,
            standard_item_id=standard_item_id,
            expected_current_version_id=body.expected_current_version_id,
            canonical_name=body.canonical_name,
            canonical_spec=body.canonical_spec,
            canonical_unit=body.canonical_unit,
            aliases=body.aliases,
            created_by=body.created_by,
            reason_detail=body.reason_detail,
        )
        _commit(session)
    except Exception as exc:
        _raise_catalog_error(session, exc)
        raise AssertionError("unreachable")
    return _version_payload(version)


@router.post(
    "/raw-items/{raw_item_id}/memberships",
    status_code=201,
    response_model=MembershipResponse,
)
def post_membership(
    raw_item_id: int,
    body: MembershipBody,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    _begin_immediate(session)
    try:
        row = append_membership_decision(
            session,
            raw_item_id=raw_item_id,
            standard_item_id=body.standard_item_id,
            status=body.status,
            expected_current_decision_id=(
                body.expected_current_decision_id
            ),
            candidate_score=body.candidate_score,
            method=body.method,
            evidence=body.evidence,
            decided_by=body.decided_by,
            reason_detail=body.reason_detail,
        )
        _commit(session)
    except Exception as exc:
        _raise_catalog_error(session, exc)
        raise AssertionError("unreachable")
    return _membership_payload(row)


@router.get(
    "/standard-items/{standard_item_id}/members",
    response_model=StandardItemMembersResponse,
)
def get_standard_item_members(
    standard_item_id: int,
    session: Session = Depends(get_session),
    *,
    after_id: int | None = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> dict[str, object]:
    try:
        rows, next_cursor = standard_item_members(
            session,
            standard_item_id,
            after_id=after_id,
            limit=limit,
        )
    except CatalogNotFound as exc:
        _raise_catalog_error(session, exc)
        raise AssertionError("unreachable")
    return {
        "standard_item_id": standard_item_id,
        "members": [
            {
                "raw_item_id": raw.id,
                "name": clean.item_name_norm,
                "spec": clean.spec_norm,
                "unit": clean.unit_norm,
                "unit_price": (
                    None
                    if clean.unit_price is None
                    else format(clean.unit_price, "f")
                ),
                "clean_decision_id": clean.id,
                "membership_decision_id": membership.id,
                "current_cleansing_decision": _clean_decision_payload(clean),
                "source": _source_payload(
                    raw,
                    raw.source_variant,
                    raw.source_variant.document,
                ),
            }
            for raw, clean, membership in rows
        ],
        "next_cursor": next_cursor,
        "limit": limit,
    }


def _parser_warnings(value: str) -> list[Any]:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return [{"code": "INVALID_WARNING_JSON", "raw": value}]
    return parsed if isinstance(parsed, list) else [parsed]


@router.post(
    "/documents/{document_id}/metadata",
    status_code=201,
    response_model=DocumentMetadataResponse,
)
def post_document_metadata(
    document_id: int,
    body: DocumentMetadataBody,
    session: Session = Depends(get_session),
) -> dict[str, object]:
    _begin_immediate(session)
    try:
        row = append_document_metadata(
            session,
            document_id=document_id,
            supplier_name=body.supplier_name,
            quote_date=body.quote_date,
            project_name=body.project_name,
            expected_current_version_id=body.expected_current_version_id,
            decided_by=body.decided_by,
            reason_detail=body.reason_detail,
        )
        _commit(session)
    except Exception as exc:
        _raise_catalog_error(session, exc)
        raise AssertionError("unreachable")
    return _metadata_payload(row)
