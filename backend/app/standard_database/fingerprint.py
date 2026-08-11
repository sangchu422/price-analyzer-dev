"""Deterministic fingerprints for automatic standard-database builds."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.standard_database.service import EligibleHistoricalRow


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[union-attr]
    return value


def standard_build_fingerprint(
    rows: Iterable[EligibleHistoricalRow],
) -> str:
    """Hash the exact, order-independent evidence projection for a build."""

    evidence = [
        {
            key: _json_value(value)
            for key, value in asdict(row).items()
        }
        for row in rows
    ]
    evidence.sort(
        key=lambda row: json.dumps(
            row,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    payload = json.dumps(
        evidence,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def standard_build_calculation_fingerprint(
    *,
    rule_version: str,
    normalization_version: str,
    calculation_version: str,
) -> str:
    """Fingerprint the declared deterministic build calculation contract."""

    payload = {
        "rule_version": rule_version,
        "normalization_version": normalization_version,
        "calculation_version": calculation_version,
        "representative_order": [
            "CONFIRMED_LATEST_QUOTE_DATE",
            "EXPLICIT_REVISION",
            "UNLOCKED_SOURCE_QUALITY",
            "NEWEST_INGEST",
        ],
        "copy_policy": "EXACT_VALUES_ONLY_WITH_LINEAGE",
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def standard_build_code_fingerprint() -> str:
    """Hash the source modules whose code determines a standard build."""

    backend_root = Path(__file__).resolve().parents[2]
    paths = (
        backend_root / "app" / "standard_database" / "fingerprint.py",
        backend_root / "app" / "standard_database" / "operational.py",
        backend_root / "app" / "standard_database" / "service.py",
        backend_root / "app" / "pricing" / "service.py",
        backend_root / "app" / "catalog" / "models.py",
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(backend_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()
