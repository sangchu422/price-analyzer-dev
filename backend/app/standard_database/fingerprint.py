"""Deterministic fingerprints for automatic standard-database builds."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict
from decimal import Decimal
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
