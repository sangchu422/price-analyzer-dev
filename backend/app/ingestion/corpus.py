"""Shared, auditable quote-corpus scanning and ingestion services."""

from __future__ import annotations

import ntpath
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from zipfile import BadZipFile

from openpyxl.utils.exceptions import InvalidFileException
from pypdf.errors import PdfReadError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from xlrd.biffh import XLRDError

from app.cleansing.models import CleanDecision, CleanStatus
from app.cleansing.service import apply_group_outlier_rules, apply_rules
from app.documents.models import SourceVariant
from app.ingestion.service import (
    SourceEvidenceConflictError,
    SourceFileChangedError,
    UnsupportedQuoteLayoutError,
    ingest_group,
    parsing_variant_for,
    sha256,
)
from app.ingestion.readers import (
    SUPPORTED_QUOTE_EXTENSIONS,
    UnsafeQuoteFileError,
)
from app.ingestion.source_selector import SourceGroup, build_source_groups
from app.quotes.models import RawQuoteItem
from app.standard_database.models import (
    QuoteDocumentPurpose,
    QuoteDocumentRole,
)


HISTORICAL_INGEST_ACTOR = "LOCAL_HISTORICAL_INGEST"


@dataclass(frozen=True)
class VariantEvidence:
    path: str
    sha256: str | None
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "path": self.path,
            "sha256": self.sha256,
        }
        if self.error_code is not None:
            result["error_code"] = self.error_code
        return result


@dataclass(frozen=True)
class CorpusIssue:
    logical_name: str
    error_code: str
    detail: str
    preferred_path: str | None = None
    preferred_sha256: str | None = None
    variants: tuple[VariantEvidence, ...] = ()

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "logical_name": self.logical_name,
            "error_code": self.error_code,
            "detail": self.detail,
        }
        if self.preferred_path is not None:
            result["preferred_path"] = self.preferred_path
            result["preferred_sha256"] = self.preferred_sha256
            result["variants"] = [
                variant.to_dict() for variant in self.variants
            ]
        return result


@dataclass(frozen=True)
class PreflightReport:
    root_available: bool
    physical_files: int
    files_by_extension: dict[str, int]
    logical_documents: int
    variants: int
    paired_documents: int
    unlocked_variants: int
    unlocked_preferred: int
    issues: tuple[CorpusIssue, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class DocumentIngestResult:
    logical_name: str
    status: str
    variants_created: int
    raw_items_created: int
    base_decisions_created: int
    error_code: str | None = None


@dataclass(frozen=True)
class IngestReport:
    preflight: PreflightReport
    documents: tuple[DocumentIngestResult, ...]
    variants_created: int
    raw_items_created: int
    base_decisions_created: int
    outlier_decisions_created: int
    variants_total: int
    raw_items_total: int
    latest_status_counts: dict[str, int]
    failures: tuple[CorpusIssue, ...]

    @property
    def documents_ingested(self) -> int:
        return sum(item.status == "INGESTED" for item in self.documents)

    @property
    def documents_unchanged(self) -> int:
        return sum(item.status == "UNCHANGED" for item in self.documents)

    @property
    def documents_failed(self) -> int:
        return sum(item.status == "FAILED" for item in self.documents)

    def to_dict(self) -> dict[str, object]:
        return {
            "preflight": self.preflight.to_dict(),
            "documents_ingested": self.documents_ingested,
            "documents_unchanged": self.documents_unchanged,
            "documents_failed": self.documents_failed,
            "variants_created": self.variants_created,
            "raw_items_created": self.raw_items_created,
            "base_decisions_created": self.base_decisions_created,
            "outlier_decisions_created": self.outlier_decisions_created,
            "variants_total": self.variants_total,
            "raw_items_total": self.raw_items_total,
            "latest_status_counts": self.latest_status_counts,
            "documents": [asdict(item) for item in self.documents],
            "failures": [issue.to_dict() for issue in self.failures],
        }


def scan_supported_files(root: Path) -> list[Path]:
    """Return supported physical source paths without opening their content."""
    if not root.is_dir():
        return []
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_QUOTE_EXTENSIONS
            and not path.name.startswith("~$")
        ),
        key=lambda path: _relative_name(path, root).casefold(),
    )


def preflight_corpus(root: Path) -> PreflightReport:
    """Inventory source metadata only; quote content is never opened."""
    root = Path(root)
    try:
        root_exists = root.exists()
        root_is_directory = root.is_dir()
    except OSError:
        root_exists = False
        root_is_directory = False
    if not root_is_directory:
        error_code = (
            "QUOTE_ROOT_NOT_DIRECTORY"
            if root_exists
            else "QUOTE_ROOT_NOT_FOUND"
        )
        detail = (
            "configured quote root is not a directory"
            if root_exists
            else "configured quote root is not an accessible directory"
        )
        return PreflightReport(
            root_available=False,
            physical_files=0,
            files_by_extension={},
            logical_documents=0,
            variants=0,
            paired_documents=0,
            unlocked_variants=0,
            unlocked_preferred=0,
            issues=(
                CorpusIssue(
                    logical_name=".",
                    error_code=error_code,
                    detail=detail,
                ),
            ),
        )

    paths = scan_supported_files(root)
    groups, issues = prepare_source_groups(paths, root)
    extensions = Counter(path.suffix.lower() for path in paths)
    return PreflightReport(
        root_available=True,
        physical_files=len(paths),
        files_by_extension=dict(sorted(extensions.items())),
        logical_documents=len(groups) + len(issues),
        variants=sum(len(group.variants) for group in groups),
        paired_documents=sum(
            _has_unlocked(group) and len(group.variants) > 1
            for group in groups
        ),
        unlocked_variants=sum(
            _is_unlocked(path)
            for group in groups
            for path in group.variants
        ),
        unlocked_preferred=sum(
            _is_unlocked(group.preferred) for group in groups
        ),
        issues=tuple(issues),
    )


def ingest_corpus(session: Session, root: Path) -> IngestReport:
    """Ingest each logical document independently and apply cleansing rules."""
    root = Path(root).resolve(strict=False)
    preflight = preflight_corpus(root)
    if not preflight.root_available:
        results = tuple(
            DocumentIngestResult(
                logical_name=issue.logical_name,
                status="FAILED",
                variants_created=0,
                raw_items_created=0,
                base_decisions_created=0,
                error_code=issue.error_code,
            )
            for issue in preflight.issues
        )
        return IngestReport(
            preflight=preflight,
            documents=results,
            variants_created=0,
            raw_items_created=0,
            base_decisions_created=0,
            outlier_decisions_created=0,
            variants_total=_count(session, SourceVariant.id),
            raw_items_total=_count(session, RawQuoteItem.id),
            latest_status_counts=_latest_status_counts(session),
            failures=preflight.issues,
        )
    paths = scan_supported_files(root)
    groups, preflight_issues = prepare_source_groups(paths, root)
    results: list[DocumentIngestResult] = [
        DocumentIngestResult(
            logical_name=issue.logical_name,
            status="FAILED",
            variants_created=0,
            raw_items_created=0,
            base_decisions_created=0,
            error_code=issue.error_code,
        )
        for issue in preflight_issues
    ]
    failures = list(preflight_issues)

    for group in groups:
        before_variants = _count(session, SourceVariant.id)
        before_rows = _count(session, RawQuoteItem.id)
        before_decisions = _count(session, CleanDecision.id)
        try:
            selected = ingest_group(session, group, root=root)
            current_role = session.scalar(
                select(QuoteDocumentRole)
                .where(
                    QuoteDocumentRole.document_id == selected.document_id
                )
                .order_by(QuoteDocumentRole.id.desc())
                .limit(1)
            )
            if current_role is None:
                session.add(
                    QuoteDocumentRole(
                        document_id=selected.document_id,
                        purpose=(
                            QuoteDocumentPurpose.HISTORICAL_REFERENCE
                        ),
                        decided_by=HISTORICAL_INGEST_ACTOR,
                        reason_detail=(
                            "ingested from the configured historical "
                            "quote corpus"
                        ),
                    )
                )
            parsing_variant = parsing_variant_for(session, selected)
            for raw_item in sorted(
                parsing_variant.raw_items,
                key=lambda item: item.id,
            ):
                apply_rules(session, raw_item)
            session.commit()
        except EXPECTED_INGESTION_ERRORS as exc:
            session.rollback()
            issue = ingestion_issue(group, exc, root=root)
            failures.append(issue)
            results.append(
                DocumentIngestResult(
                    logical_name=group.logical_name,
                    status="FAILED",
                    variants_created=0,
                    raw_items_created=0,
                    base_decisions_created=0,
                    error_code=issue.error_code,
                )
            )
            continue
        except Exception:
            session.rollback()
            raise

        variants_created = _count(session, SourceVariant.id) - before_variants
        rows_created = _count(session, RawQuoteItem.id) - before_rows
        decisions_created = (
            _count(session, CleanDecision.id) - before_decisions
        )
        changed = bool(variants_created or rows_created or decisions_created)
        results.append(
            DocumentIngestResult(
                logical_name=group.logical_name,
                status="INGESTED" if changed else "UNCHANGED",
                variants_created=variants_created,
                raw_items_created=rows_created,
                base_decisions_created=decisions_created,
            )
        )

    before_outliers = _count(session, CleanDecision.id)
    apply_group_outlier_rules(session)
    session.commit()
    outliers_created = _count(session, CleanDecision.id) - before_outliers
    results.sort(
        key=lambda item: (
            ntpath.normcase(item.logical_name),
            item.logical_name,
        )
    )
    return IngestReport(
        preflight=preflight,
        documents=tuple(results),
        variants_created=sum(item.variants_created for item in results),
        raw_items_created=sum(item.raw_items_created for item in results),
        base_decisions_created=sum(
            item.base_decisions_created for item in results
        ),
        outlier_decisions_created=outliers_created,
        variants_total=_count(session, SourceVariant.id),
        raw_items_total=_count(session, RawQuoteItem.id),
        latest_status_counts=_latest_status_counts(session),
        failures=tuple(
            sorted(
                failures,
                key=lambda item: (
                    ntpath.normcase(item.logical_name),
                    item.logical_name,
                ),
            )
        ),
    )


def prepare_source_groups(
    paths: list[Path],
    root: Path,
) -> tuple[list[SourceGroup], list[CorpusIssue]]:
    """Validate containment, then group sources by portable logical identity."""
    resolved_root = root.resolve(strict=False)
    grouped_paths: dict[str, list[Path]] = {}
    issues: list[CorpusIssue] = []
    for path in paths:
        display_name = _relative_name(path, root)
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            issues.append(
                CorpusIssue(
                    logical_name=display_name,
                    error_code="INVALID_SOURCE_PATH",
                    detail="source path could not be resolved",
                )
            )
            continue
        try:
            resolved.relative_to(resolved_root)
        except ValueError:
            issues.append(
                CorpusIssue(
                    logical_name=display_name,
                    error_code="PATH_OUTSIDE_ROOT",
                    detail="source path resolves outside configured quote root",
                )
            )
            continue
        try:
            candidate = build_source_groups([path], root=root)[0]
        except (OSError, ValueError):
            issues.append(
                CorpusIssue(
                    logical_name=display_name,
                    error_code="INVALID_SOURCE_PATH",
                    detail="source path could not be grouped",
                )
            )
            continue
        key = ntpath.normcase(candidate.logical_name)
        grouped_paths.setdefault(key, []).append(path)

    groups = [
        build_source_groups(group_paths, root=root)[0]
        for _, group_paths in sorted(grouped_paths.items())
    ]
    return groups, issues


EXPECTED_INGESTION_ERRORS = (
    UnsupportedQuoteLayoutError,
    UnsafeQuoteFileError,
    SourceFileChangedError,
    SourceEvidenceConflictError,
    BadZipFile,
    InvalidFileException,
    PdfReadError,
    XLRDError,
    OSError,
)


def ingestion_issue(
    group: SourceGroup,
    exc: Exception,
    *,
    root: Path,
) -> CorpusIssue:
    if isinstance(exc, UnsafeQuoteFileError):
        code = "UNSAFE_SOURCE"
        detail = "source exceeds bounded parsing safety limits"
    elif isinstance(exc, UnsupportedQuoteLayoutError):
        code = "UNSUPPORTED_LAYOUT"
        detail = "source layout is not currently supported"
    elif isinstance(exc, SourceFileChangedError):
        code = "SOURCE_CHANGED"
        detail = "source file changed during ingestion"
    elif isinstance(
        exc,
        (BadZipFile, InvalidFileException, PdfReadError, XLRDError),
    ):
        code = "UNREADABLE_SOURCE"
        detail = "source file could not be read"
    elif isinstance(exc, OSError):
        code = "SOURCE_IO_ERROR"
        detail = "source file could not be accessed"
    else:
        code = "INVALID_SOURCE_EVIDENCE"
        detail = "source evidence is invalid or changed"
    variants = _variant_evidence(group, root)
    preferred_path = _relative_name(group.preferred, root)
    preferred = next(
        item for item in variants if item.path == preferred_path
    )
    return CorpusIssue(
        logical_name=group.logical_name,
        error_code=code,
        detail=detail,
        preferred_path=preferred_path,
        preferred_sha256=preferred.sha256,
        variants=variants,
    )


def _variant_evidence(
    group: SourceGroup,
    root: Path,
) -> tuple[VariantEvidence, ...]:
    evidence: list[VariantEvidence] = []
    for path in sorted(
        group.variants,
        key=lambda item: _relative_name(item, root).casefold(),
    ):
        relative_path = _relative_name(path, root)
        try:
            digest = sha256(path)
        except OSError:
            evidence.append(
                VariantEvidence(
                    path=relative_path,
                    sha256=None,
                    error_code="HASH_UNAVAILABLE",
                )
            )
        else:
            evidence.append(
                VariantEvidence(path=relative_path, sha256=digest)
            )
    return tuple(evidence)


def _count(session: Session, column: object) -> int:
    return session.scalar(select(func.count(column))) or 0


def _latest_status_counts(session: Session) -> dict[str, int]:
    latest_ids = (
        select(
            CleanDecision.raw_item_id,
            func.max(CleanDecision.id).label("decision_id"),
        )
        .group_by(CleanDecision.raw_item_id)
        .subquery()
    )
    counts = {status.value: 0 for status in CleanStatus}
    for status, count in session.execute(
        select(CleanDecision.status, func.count(CleanDecision.id))
        .join(latest_ids, CleanDecision.id == latest_ids.c.decision_id)
        .group_by(CleanDecision.status)
    ):
        counts[status.value] = count
    return counts


def _relative_name(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.stem


def _is_unlocked(path: Path) -> bool:
    return ntpath.normcase(path.stem.strip()).endswith(
        ntpath.normcase("_보안해제")
    )


def _has_unlocked(group: SourceGroup) -> bool:
    return any(_is_unlocked(path) for path in group.variants)
