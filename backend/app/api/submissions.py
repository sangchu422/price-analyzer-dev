"""Evidence-preserving upload API for incoming supplier bids."""

from __future__ import annotations

import hashlib
import ntpath
import os
import tempfile
import threading
import unicodedata
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cleansing.models import CleanDecision, CleanStatus
from app.cleansing.service import apply_rules
from app.core.config import settings
from app.db.session import get_session
from app.documents.models import SourceVariant
from app.ingestion.corpus import (
    EXPECTED_INGESTION_ERRORS,
    ingestion_issue,
)
from app.ingestion.readers import SUPPORTED_QUOTE_EXTENSIONS
from app.ingestion.service import (
    ingest_path,
    parsing_variant_for,
)
from app.ingestion.source_selector import build_source_groups
from app.standard_database.models import (
    QuoteDocumentPurpose,
    QuoteDocumentRole,
)


router = APIRouter()

_CHUNK_SIZE = 1024 * 1024
_WINDOWS_INVALID_FILENAME_CHARACTERS = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_BASENAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
)
_IDENTITY_LOCKS_GUARD = threading.Lock()


@dataclass
class _IdentityLockEntry:
    lock: threading.Lock
    users: int = 0


_IDENTITY_LOCKS: dict[str, _IdentityLockEntry] = {}


class SubmissionResponse(BaseModel):
    document_id: int
    sha256: str
    purpose: QuoteDocumentPurpose
    parser_name: str
    parser_version: str
    status: str
    raw_item_count: int
    included_count: int
    excluded_count: int
    review_required_count: int


@router.post("", response_model=SubmissionResponse, status_code=201)
def submit_bid(
    file: UploadFile = File(...),
    submitted_by: str = Form(..., min_length=1, max_length=100),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    actor = submitted_by.strip()
    if not actor:
        raise _http_error(
            422,
            "INVALID_SUBMITTED_BY",
            "submitted_by must contain a non-whitespace actor name",
        )
    filename = _validated_filename(file.filename)
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_QUOTE_EXTENSIONS:
        raise _http_error(
            415,
            "UNSUPPORTED_FILE_TYPE",
            "supported quote types are .xlsx, .xls, and .pdf",
        )

    root = settings.submission_path.resolve(strict=False)
    temporary, digest = _stage_upload(
        file.file,
        root=root,
        maximum_bytes=settings.submission_max_bytes,
    )
    try:
        with _submission_identity_lock(digest, filename):
            stored_path = _place_staged_upload(
                temporary,
                root=root,
                digest=digest,
                filename=filename,
            )
            relative_path = stored_path.relative_to(root).as_posix()
            existed = session.scalar(
                select(func.count(SourceVariant.id)).where(
                    SourceVariant.path == relative_path
                )
            )
            try:
                variant = ingest_path(session, stored_path, root=root)
                parsing_variant = parsing_variant_for(session, variant)
                for raw_item in sorted(
                    parsing_variant.raw_items,
                    key=lambda item: item.id,
                ):
                    apply_rules(session, raw_item)
                session.flush()
                role = _ensure_incoming_role(
                    session,
                    document_id=variant.document_id,
                    submitted_by=actor,
                )
                counts = _decision_counts(
                    session,
                    raw_item_ids=tuple(
                        row.id for row in parsing_variant.raw_items
                    ),
                )
                parser_name, parser_version = _parser_identity(
                    parsing_variant
                )
                session.commit()
            except EXPECTED_INGESTION_ERRORS as exc:
                session.rollback()
                group = build_source_groups([stored_path], root=root)[0]
                issue = ingestion_issue(group, exc, root=root)
                raise _http_error(
                    422, issue.error_code, issue.detail
                ) from exc
            except HTTPException:
                session.rollback()
                raise
            except Exception:
                session.rollback()
                raise
    finally:
        temporary.unlink(missing_ok=True)
        file.file.close()

    return {
        "document_id": variant.document_id,
        "sha256": digest,
        "purpose": role.purpose,
        "parser_name": parser_name,
        "parser_version": parser_version,
        "status": "UNCHANGED" if existed else "INGESTED",
        "raw_item_count": len(parsing_variant.raw_items),
        "included_count": counts[CleanStatus.INCLUDED],
        "excluded_count": counts[CleanStatus.EXCLUDED],
        "review_required_count": counts[CleanStatus.REVIEW_REQUIRED],
    }


def _canonical_filename_identity(filename: str) -> str:
    return unicodedata.normalize("NFC", filename).casefold()


@contextmanager
def _submission_identity_lock(
    digest: str,
    filename: str,
) -> Iterator[None]:
    key = f"{digest}:{_canonical_filename_identity(filename)}"
    with _IDENTITY_LOCKS_GUARD:
        entry = _IDENTITY_LOCKS.get(key)
        if entry is None:
            entry = _IdentityLockEntry(threading.Lock())
            _IDENTITY_LOCKS[key] = entry
        entry.users += 1
    entry.lock.acquire()
    try:
        yield
    finally:
        entry.lock.release()
        with _IDENTITY_LOCKS_GUARD:
            entry.users -= 1
            if entry.users == 0 and _IDENTITY_LOCKS.get(key) is entry:
                del _IDENTITY_LOCKS[key]


def _place_staged_upload(
    temporary: Path,
    *,
    root: Path,
    digest: str,
    filename: str,
) -> Path:
    directory = root / digest
    _assert_confined(directory, root)
    directory.mkdir(parents=True, exist_ok=True)
    identity = _canonical_filename_identity(filename)
    aliases = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file()
            and _canonical_filename_identity(path.name) == identity
        )
    )
    if aliases:
        destination = aliases[0]
        if _file_sha256(destination) != digest:
            raise _http_error(
                409,
                "EVIDENCE_FILE_CONFLICT",
                "stored evidence path contains different bytes",
            )
        temporary.unlink(missing_ok=True)
        return destination
    destination = directory / filename
    _assert_confined(destination, root)
    os.replace(temporary, destination)
    return destination


def _validated_filename(value: str | None) -> str:
    raw = value or ""
    drive, _ = ntpath.splitdrive(raw)
    if (
        not raw
        or raw in {".", ".."}
        or drive
        or ntpath.isabs(raw)
        or "/" in raw
        or "\\" in raw
        or ntpath.basename(raw) != raw
        or raw.endswith((" ", "."))
        or any(
            character in _WINDOWS_INVALID_FILENAME_CHARACTERS
            or unicodedata.category(character) in {"Cc", "Cf"}
            for character in raw
        )
        or _windows_device_basename(raw)
        or len(raw.encode("utf-8")) > 255
    ):
        raise _http_error(
            400,
            "INVALID_FILENAME",
            "upload filename must be a plain basename",
        )
    return raw


def _windows_device_basename(filename: str) -> bool:
    first_component = filename.split(".", 1)[0].rstrip(" .").upper()
    return first_component in _WINDOWS_RESERVED_BASENAMES


def _stage_upload(
    upload: BinaryIO,
    *,
    root: Path,
    maximum_bytes: int,
) -> tuple[Path, str]:
    root.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".incoming-",
        suffix=".tmp",
        dir=root,
    )
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(descriptor, "wb") as stream:
            while block := upload.read(_CHUNK_SIZE):
                size += len(block)
                if size > maximum_bytes:
                    raise _http_error(
                        413,
                        "UPLOAD_TOO_LARGE",
                        f"upload exceeds {maximum_bytes} bytes",
                    )
                digest.update(block)
                stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())
        if size == 0:
            raise _http_error(
                400,
                "EMPTY_UPLOAD",
                "uploaded quote is empty",
            )
        return temporary, digest.hexdigest()
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _assert_confined(path: Path, root: Path) -> None:
    try:
        path.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise _http_error(
            400,
            "INVALID_FILENAME",
            "upload destination is outside submission storage",
        ) from exc


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def _latest_role(
    session: Session,
    document_id: int,
) -> QuoteDocumentRole | None:
    return session.scalar(
        select(QuoteDocumentRole)
        .where(QuoteDocumentRole.document_id == document_id)
        .order_by(QuoteDocumentRole.id.desc())
        .limit(1)
    )


def _ensure_incoming_role(
    session: Session,
    *,
    document_id: int,
    submitted_by: str,
) -> QuoteDocumentRole:
    current = _latest_role(session, document_id)
    if current is not None:
        if current.purpose is QuoteDocumentPurpose.INCOMING_BID:
            return current
        raise _http_error(
            409,
            "DOCUMENT_ROLE_CONFLICT",
            "an explicitly historical document cannot become an incoming bid",
        )
    role = QuoteDocumentRole(
        document_id=document_id,
        purpose=QuoteDocumentPurpose.INCOMING_BID,
        decided_by=submitted_by,
        reason_detail="WEB_BID_SUBMISSION",
    )
    session.add(role)
    session.flush()
    return role


def _decision_counts(
    session: Session,
    *,
    raw_item_ids: tuple[int, ...],
) -> Counter[CleanStatus]:
    counts: Counter[CleanStatus] = Counter()
    if not raw_item_ids:
        return counts
    latest = (
        select(
            CleanDecision.raw_item_id,
            func.max(CleanDecision.id).label("id"),
        )
        .where(CleanDecision.raw_item_id.in_(raw_item_ids))
        .group_by(CleanDecision.raw_item_id)
        .subquery()
    )
    for status in session.scalars(
        select(CleanDecision.status).join(
            latest, CleanDecision.id == latest.c.id
        )
    ):
        counts[status] += 1
    return counts


def _parser_identity(variant: SourceVariant) -> tuple[str, str]:
    if not variant.raw_items:
        return "quote-reader", "reader-v1"
    first = min(variant.raw_items, key=lambda item: item.id)
    return first.parser_name, first.parser_version


def _http_error(
    status_code: int,
    error_code: str,
    message: str,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error_code": error_code, "message": message},
    )
