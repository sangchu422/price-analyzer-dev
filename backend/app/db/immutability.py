from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session


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


@event.listens_for(Session, "do_orm_execute")
def reject_bulk_evidence_changes(state: ORMExecuteState) -> None:
    """Reject ORM bulk DML that bypasses instance-level flush tracking."""

    if not (state.is_update or state.is_delete):
        return
    mapper = state.bind_mapper
    mapper_is_evidence = mapper is not None and getattr(
        mapper.class_,
        "__evidence_immutable__",
        False,
    )
    table = getattr(state.statement, "table", None)
    table_is_evidence = (
        table is not None
        and table.info.get("evidence_immutable", False)
    )
    if mapper_is_evidence or table_is_evidence:
        target_name = (
            mapper.class_.__name__
            if mapper_is_evidence
            else table.name
        )
        raise ImmutableEvidenceError(
            f"bulk DML is forbidden for {target_name}"
        )
