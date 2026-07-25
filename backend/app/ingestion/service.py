"""Transactional registration and parsing of quote source evidence."""

from __future__ import annotations

import hashlib
import json
import ntpath
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.documents.models import SourceDocument, SourceVariant
from app.ingestion.readers import ParsedRow, read_quote
from app.ingestion.source_selector import SourceGroup, build_source_groups
from app.quotes.models import RawQuoteItem


_SUPPORTED_EXTENSIONS = {".xlsx", ".xls", ".pdf"}
_UNLOCKED_SUFFIX = "_보안해제"
_PARSER_NAME = "quote-reader"
_PARSER_VERSION = "reader-v1"


class UnsupportedQuoteLayoutError(ValueError):
    """Raised when a supported file has no safely recognizable quote rows."""


class SourceFileChangedError(RuntimeError):
    """Raised when source bytes change between hashing and parsing."""


class SourceEvidenceConflictError(ValueError):
    """Raised when an immutable stored path now has different content."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ingest_path(
    session: Session,
    path: Path,
    *,
    root: Path | None = None,
) -> SourceVariant:
    """Ingest one source path.

    An absolute path requires a stable root so the stored identity remains
    portable. Original/protected paths are retained as evidence but are not
    selected at ingest; an unlocked path is the parsing candidate. The caller
    owns the outer transaction and must commit when all related work succeeds.
    """
    canonical = build_source_groups([Path(path)], root=root)[0]
    source_path = canonical.variants[0]
    preferred = _is_unlocked(source_path)
    with session.begin_nested():
        variant = _register_variant(
            session,
            source_path,
            logical_name=canonical.logical_name,
            root=root,
            parse=True,
            selected_at_ingest=preferred,
        )
        session.flush()
    return variant


def ingest_group(
    session: Session,
    group: SourceGroup,
    *,
    root: Path | None = None,
) -> SourceVariant:
    """Register all variants and parse the selected ingest-time snapshot.

    The caller owns the outer transaction and must commit when all related
    work succeeds.
    """
    canonical_groups = build_source_groups(group.variants, root=root)
    if len(canonical_groups) != 1:
        raise ValueError("source group variants do not share one identity")
    canonical = canonical_groups[0]
    if canonical.logical_name != group.logical_name:
        raise ValueError("source group logical identity is not canonical")

    ordered_paths = (
        canonical.preferred,
        *(
            path
            for path in canonical.variants
            if path != canonical.preferred
        ),
    )
    preferred_variant: SourceVariant | None = None
    with session.begin_nested():
        for source_path in ordered_paths:
            is_preferred = source_path == canonical.preferred
            variant = _register_variant(
                session,
                source_path,
                logical_name=canonical.logical_name,
                root=root,
                parse=is_preferred,
                selected_at_ingest=is_preferred,
            )
            if is_preferred:
                preferred_variant = variant
        session.flush()

    if preferred_variant is None:  # pragma: no cover - SourceGroup invariant
        raise RuntimeError("source group has no preferred variant")
    return preferred_variant


def parsing_variant_for(
    session: Session,
    variant: SourceVariant,
) -> SourceVariant:
    """Resolve the variant that owns rows for identical document content.

    Distinct evidence paths are always retained. When two paths in one
    logical document have identical bytes, rows are stored once and this
    projection exposes the exact variant from which they were parsed.
    """
    if variant.raw_items:
        return variant
    parsed_sibling = session.scalar(
        select(SourceVariant)
        .join(SourceVariant.raw_items)
        .where(
            SourceVariant.document_id == variant.document_id,
            SourceVariant.sha256 == variant.sha256,
        )
        .order_by(SourceVariant.id)
    )
    return parsed_sibling or variant


def preferred_variant_for(
    document: SourceDocument,
) -> SourceVariant:
    """Project the current preferred path from immutable variant evidence."""
    if not document.variants:
        raise ValueError("source document has no variants")
    groups = build_source_groups(
        [Path(variant.path) for variant in document.variants]
    )
    if len(groups) != 1:
        raise ValueError("source document variants have divergent identities")
    preferred_path = ntpath.normcase(groups[0].preferred.as_posix())
    return next(
        variant
        for variant in document.variants
        if ntpath.normcase(variant.path) == preferred_path
    )


def _register_variant(
    session: Session,
    source_path: Path,
    *,
    logical_name: str,
    root: Path | None,
    parse: bool,
    selected_at_ingest: bool,
) -> SourceVariant:
    extension = source_path.suffix.lower()
    if extension not in _SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"unsupported quote extension: {source_path.suffix}"
        )

    stored_path = _stored_path(source_path, root)
    digest = sha256(source_path)
    existing_path = _find_variant_by_path(session, stored_path)
    if existing_path is not None:
        if existing_path.sha256 != digest:
            raise SourceEvidenceConflictError(
                f"content changed at immutable source path: {stored_path}"
            )
        return existing_path

    document = _find_document(session, logical_name)
    if document is None:
        document = SourceDocument(logical_name=logical_name)
        session.add(document)

    same_content_has_rows = any(
        sibling.sha256 == digest and bool(sibling.raw_items)
        for sibling in document.variants
    )
    should_parse = parse and not same_content_has_rows
    rows = read_quote(source_path) if should_parse else []
    verified_digest = sha256(source_path)
    if verified_digest != digest:
        raise SourceFileChangedError(
            f"source file changed while parsing: {stored_path}"
        )
    if should_parse and not rows:
        raise UnsupportedQuoteLayoutError(
            f"no quote rows matched a supported layout: {stored_path}"
        )
    variant = SourceVariant(
        document=document,
        path=stored_path,
        sha256=digest,
        extension=extension,
        security_state=(
            "UNLOCKED" if _is_unlocked(source_path) else "UNKNOWN"
        ),
        selected_for_parsing_at_ingest=selected_at_ingest,
    )
    session.add(variant)
    for parsed in rows:
        variant.raw_items.append(_raw_item(parsed))
    session.flush()
    return variant


def _find_document(
    session: Session,
    logical_name: str,
) -> SourceDocument | None:
    normalized_name = ntpath.normcase(logical_name)
    return next(
        (
            document
            for document in session.scalars(select(SourceDocument))
            if ntpath.normcase(document.logical_name) == normalized_name
        ),
        None,
    )


def _find_variant_by_path(
    session: Session,
    stored_path: str,
) -> SourceVariant | None:
    normalized_path = ntpath.normcase(stored_path)
    return next(
        (
            variant
            for variant in session.scalars(select(SourceVariant))
            if ntpath.normcase(variant.path) == normalized_path
        ),
        None,
    )


def _stored_path(path: Path, root: Path | None) -> str:
    if path.is_absolute():
        if root is None:
            raise ValueError("absolute paths require an explicit stable root")
        return path.resolve(strict=False).relative_to(
            Path(root).resolve(strict=False)
        ).as_posix()
    return path.as_posix()


def _is_unlocked(path: Path) -> bool:
    stem = path.stem.strip()
    return ntpath.normcase(stem).endswith(
        ntpath.normcase(_UNLOCKED_SUFFIX)
    )


def _raw_item(parsed: ParsedRow) -> RawQuoteItem:
    return RawQuoteItem(
        source_sheet=parsed.sheet,
        source_page=parsed.page,
        source_row=parsed.row,
        source_cells=parsed.cells,
        item_name_raw=parsed.item_name,
        spec_raw=parsed.spec,
        unit_raw=parsed.unit,
        quantity_raw=parsed.quantity,
        unit_price_raw=parsed.unit_price,
        amount_raw=parsed.amount,
        maker_raw=parsed.maker,
        parser_name=_PARSER_NAME,
        parser_version=_PARSER_VERSION,
        parse_warnings_json=json.dumps(
            parsed.warnings,
            ensure_ascii=False,
        ),
    )
