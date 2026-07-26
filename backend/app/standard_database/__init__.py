"""Public standard-database API without import-time service cycles."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORT_MODULES = {
    "CALCULATION_VERSION": "app.pricing.service",
    "standard_build_fingerprint": "app.standard_database.fingerprint",
    "QuoteDocumentPurpose": "app.standard_database.models",
    "QuoteDocumentRole": "app.standard_database.models",
    "StandardBuildStatus": "app.standard_database.models",
    "StandardDatabaseBuildRun": "app.standard_database.models",
    "ConcurrentStandardBuild": "app.standard_database.service",
    "DuplicateStandardKeyConflict": "app.standard_database.service",
    "ManualMembershipConflict": "app.standard_database.service",
    "NORMALIZATION_VERSION": "app.standard_database.service",
    "RULE_VERSION": "app.standard_database.service",
    "EligibleHistoricalRow": "app.standard_database.service",
    "StandardDatabaseBuildIssue": "app.standard_database.service",
    "StandardDatabaseBuildResult": "app.standard_database.service",
    "build_standard_database": "app.standard_database.service",
    "eligible_historical_rows": "app.standard_database.service",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
