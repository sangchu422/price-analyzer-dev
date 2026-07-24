from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session


class ImmutableEvidenceError(RuntimeError):
    """Raised when ORM code attempts to rewrite persistent evidence."""


def _is_evidence(instance: Any) -> bool:
    return bool(getattr(type(instance), "__evidence_immutable__", False))


@event.listens_for(Session, "before_flush")
def reject_persistent_evidence_changes(
    session: Session,
    flush_context: Any,
    instances: Any,
) -> None:
    """Enforce append-only evidence at the ORM Session boundary."""

    for instance in session.deleted:
        if _is_evidence(instance):
            raise ImmutableEvidenceError(
                f"{type(instance).__name__} rows cannot be deleted"
            )
    for instance in session.dirty:
        if _is_evidence(instance) and session.is_modified(
            instance,
            include_collections=False,
        ):
            raise ImmutableEvidenceError(
                f"{type(instance).__name__} rows cannot be updated"
            )
