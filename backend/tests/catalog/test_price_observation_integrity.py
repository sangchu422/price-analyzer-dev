from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.catalog.models import (
    CatalogIntegrityError,
    ItemMembershipDecision,
    MembershipStatus,
    StandardItem,
    StandardPriceObservation,
    StandardPriceVersion,
)
from app.cleansing.models import CleanDecision, CleanStatus
from app.db.base import Base
from app.db.immutability import ImmutableEvidenceError
from app.db.sqlite import configure_sqlite
from app.documents.models import SourceDocument, SourceVariant
from app.quotes.models import RawQuoteItem


def _engine():
    return configure_sqlite(create_engine("sqlite:///:memory:"))


def _raw(variant: SourceVariant, row: int, name: str) -> RawQuoteItem:
    return RawQuoteItem(
        source_variant=variant,
        source_row=row,
        item_name_raw=name,
        parser_name="xlsx",
        parser_version="1",
    )


def _decision(raw_item: RawQuoteItem, price: str) -> CleanDecision:
    return CleanDecision(
        raw_item=raw_item,
        status=CleanStatus.INCLUDED,
        reason_code="VALID",
        item_name_norm=raw_item.item_name_raw,
        unit_norm="EA",
        unit_price=Decimal(price),
        rule_version="clean-v1",
    )


def _membership(
    raw_item: RawQuoteItem,
    item: StandardItem | None,
    status: MembershipStatus = MembershipStatus.MATCHED,
) -> ItemMembershipDecision:
    return ItemMembershipDecision(
        raw_item=raw_item,
        standard_item=item,
        status=status,
        method="MANUAL",
        evidence_json="{}",
        decided_by="buyer",
    )


def _graph():
    document = SourceDocument(logical_name="quote.xlsx")
    variant = SourceVariant(
        document=document,
        path="quote.xlsx",
        sha256="a" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    raw_item = _raw(variant, 2, "BEARING")
    clean = _decision(raw_item, "120")
    item = StandardItem()
    membership = _membership(raw_item, item)
    price = StandardPriceVersion(
        standard_item=item,
        version_number=1,
        observation_count=1,
        supplier_count=0,
        minimum_price=Decimal("120"),
        median_price=Decimal("120"),
        average_price=Decimal("120"),
        maximum_price=Decimal("120"),
        calculation_version="v1",
        approved_by="buyer",
    )
    observation = StandardPriceObservation(
        standard_price_version=price,
        clean_decision=clean,
        membership_decision=membership,
    )
    return document, item, raw_item, clean, membership, price, observation


def test_normalized_price_observation_persists_valid_decision_links() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    graph = _graph()
    with Session(engine) as session:
        session.add_all([graph[0], graph[1], graph[5]])
        session.commit()
        saved = session.get(StandardPriceObservation, graph[6].id)
        assert saved is not None
        assert saved.raw_item_id == graph[2].id
        assert saved.clean_decision_id == graph[3].id
        assert saved.membership_decision_id == graph[4].id
        assert saved.standard_item_id == graph[1].id
        assert saved.membership_status is MembershipStatus.MATCHED


def test_new_price_version_requires_matching_observation_count() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    price = StandardPriceVersion(
        standard_item=StandardItem(),
        version_number=1,
        observation_count=1,
        supplier_count=0,
        minimum_price=Decimal("1"),
        median_price=Decimal("1"),
        average_price=Decimal("1"),
        maximum_price=Decimal("1"),
        calculation_version="v1",
        approved_by="buyer",
    )
    with Session(engine) as session:
        session.add(price)
        with pytest.raises(CatalogIntegrityError):
            session.flush()


def test_observation_cannot_be_appended_to_persisted_price_version() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    graph = _graph()
    with Session(engine) as session:
        session.add_all([graph[0], graph[1], graph[5]])
        session.commit()
        second_document = SourceDocument(logical_name="quote-2.xlsx")
        second_variant = SourceVariant(
            document=second_document,
            path="quote-2.xlsx",
            sha256="b" * 64,
            extension=".xlsx",
            security_state="UNLOCKED",
            selected_for_parsing_at_ingest=True,
        )
        second_raw = _raw(second_variant, 2, "BEARING")
        second_clean = _decision(second_raw, "130")
        second_membership = _membership(second_raw, graph[1])
        session.add_all([second_document, second_clean, second_membership])
        with session.no_autoflush:
            graph[5].observations.append(
                StandardPriceObservation(
                    clean_decision=second_clean,
                    membership_decision=second_membership,
                )
            )
        with pytest.raises(CatalogIntegrityError):
            session.flush()


@pytest.mark.parametrize(
    "statement_factory",
    [
        lambda table: update(table).values(raw_item_id=999),
        lambda table: delete(table),
    ],
)
def test_price_observations_reject_session_bulk_dml(statement_factory) -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    graph = _graph()
    with Session(engine) as session:
        session.add_all([graph[0], graph[1], graph[5]])
        session.commit()
        with pytest.raises(ImmutableEvidenceError):
            session.execute(statement_factory(StandardPriceObservation.__table__))


def test_price_observation_rejects_orm_update_and_delete() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    graph = _graph()
    with Session(engine) as session:
        session.add_all([graph[0], graph[1], graph[5]])
        session.commit()
        graph[6].raw_item_id = 999
        with pytest.raises(ImmutableEvidenceError):
            session.flush()
        session.rollback()
        observation = session.get(StandardPriceObservation, graph[6].id)
        session.delete(observation)
        with pytest.raises(ImmutableEvidenceError):
            session.flush()


def _persist_adversarial_fixture(engine):
    graph = _graph()
    second_raw = _raw(graph[2].source_variant, 3, "MOTOR")
    second_clean = _decision(second_raw, "200")
    second_membership = _membership(second_raw, graph[1])
    other_item = StandardItem()
    other_membership = _membership(graph[2], other_item)
    rejected = _membership(
        graph[2],
        None,
        status=MembershipStatus.REJECTED,
    )
    with Session(engine) as session:
        session.add_all(
            [
                graph[0],
                graph[1],
                graph[5],
                second_clean,
                second_membership,
                other_item,
                other_membership,
                rejected,
            ]
        )
        session.commit()
        return {
            "price_id": graph[5].id,
            "item_id": graph[1].id,
            "raw_id": graph[2].id,
            "clean_id": graph[3].id,
            "membership_id": graph[4].id,
            "second_raw_id": second_raw.id,
            "second_clean_id": second_clean.id,
            "second_membership_id": second_membership.id,
            "other_membership_id": other_membership.id,
            "rejected_id": rejected.id,
        }


def _insert_observation(connection, **values) -> None:
    connection.execute(
        text(
            """
            INSERT INTO standard_price_observation (
                standard_price_version_id, standard_item_id, raw_item_id,
                clean_decision_id, membership_decision_id, membership_status
            ) VALUES (
                :price_id, :item_id, :raw_id, :clean_id,
                :membership_id, :membership_status
            )
            """
        ),
        values,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"clean_id": "second_clean_id"},
        {"membership_id": "second_membership_id"},
        {"membership_id": "other_membership_id"},
        {
            "membership_id": "rejected_id",
            "membership_status": MembershipStatus.REJECTED.value,
        },
        {"clean_id": "missing_id"},
        {"membership_id": "missing_id"},
    ],
)
def test_direct_sql_rejects_false_or_cross_target_evidence(
    changes: dict[str, str],
) -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    ids = _persist_adversarial_fixture(engine)
    values = {
        "price_id": ids["price_id"],
        "item_id": ids["item_id"],
        "raw_id": ids["raw_id"],
        "clean_id": ids["clean_id"],
        "membership_id": ids["membership_id"],
        "membership_status": MembershipStatus.MATCHED.value,
    }
    ids["missing_id"] = 999999
    for key, source_key in changes.items():
        values[key] = (
            source_key
            if key == "membership_status"
            else ids[source_key]
        )
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            _insert_observation(connection, **values)


@pytest.mark.parametrize(
    "changed_column",
    ["raw_id", "clean_id", "membership_id"],
)
def test_direct_sql_rejects_duplicate_price_evidence(
    changed_column: str,
) -> None:
    engine = _engine()
    Base.metadata.create_all(engine)
    ids = _persist_adversarial_fixture(engine)
    duplicate = {
        "price_id": ids["price_id"],
        "item_id": ids["item_id"],
        "raw_id": ids["raw_id"],
        "clean_id": ids["clean_id"],
        "membership_id": ids["membership_id"],
        "membership_status": MembershipStatus.MATCHED.value,
    }
    if changed_column == "raw_id":
        duplicate["raw_id"] = ids["second_raw_id"]
    elif changed_column == "clean_id":
        duplicate["clean_id"] = ids["second_clean_id"]
    else:
        duplicate["membership_id"] = ids["second_membership_id"]
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            _insert_observation(connection, **duplicate)
