from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.catalog.models import (
    ItemMembershipDecision,
    MembershipStatus,
    StandardItem,
    StandardItemVersion,
    StandardPriceObservation,
    StandardPriceVersion,
)
from app.cleansing.models import CleanDecision, CleanStatus
from app.db.base import Base
from app.db.sqlite import configure_sqlite
from app.documents.models import SourceDocument, SourceVariant
from app.quotes.models import RawQuoteItem
from app.pricing.service import (
    approve_standard_price,
    calculate_standard_price,
)
from app.standard_database import (
    CALCULATION_VERSION,
    NORMALIZATION_VERSION,
    RULE_VERSION,
    EligibleHistoricalRow,
    QuoteDocumentPurpose,
    QuoteDocumentRole,
    StandardBuildStatus,
    StandardDatabaseBuildRun,
    build_standard_database,
    standard_build_fingerprint,
)


@pytest.fixture
def session() -> Session:
    engine = configure_sqlite(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as value:
        yield value
    engine.dispose()


def _source(
    session: Session,
    *,
    name: str,
    purpose: QuoteDocumentPurpose,
    supplier: str | None = None,
) -> SourceVariant:
    document = SourceDocument(logical_name=name)
    variant = SourceVariant(
        document=document,
        path=name,
        sha256=(str(len(name) % 10) or "0") * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    session.add_all([document, variant])
    session.flush()
    role = QuoteDocumentRole(
        document_id=document.id,
        purpose=purpose,
        decided_by="buyer",
        reason_detail="source classification",
    )
    session.add(role)
    if supplier is not None:
        from app.catalog.models import DocumentMetadataVersion

        session.add(
            DocumentMetadataVersion(
                source_document=document,
                version_number=1,
                supplier_name=supplier,
                quote_date=date(2026, 7, 1),
                project_name=None,
                decided_by="buyer",
                reason_detail="quote metadata",
            )
        )
    session.flush()
    return variant


def _row(
    session: Session,
    variant: SourceVariant,
    *,
    row_number: int,
    name: str | None,
    spec: str | None,
    unit: str | None,
    price: str,
    status: CleanStatus = CleanStatus.INCLUDED,
) -> tuple[RawQuoteItem, CleanDecision]:
    raw = RawQuoteItem(
        source_variant=variant,
        source_sheet="Sheet1",
        source_row=row_number,
        item_name_raw=name,
        spec_raw=spec,
        unit_raw=unit,
        unit_price_raw=price,
        parser_name="xlsx",
        parser_version="1",
    )
    decision = CleanDecision(
        raw_item=raw,
        status=status,
        reason_code="VALID" if status is CleanStatus.INCLUDED else "REVIEW",
        item_name_norm=name,
        spec_norm=spec,
        unit_norm=unit,
        unit_price=Decimal(price),
        rule_version="clean-v1",
    )
    session.add(decision)
    session.flush()
    return raw, decision


def test_build_groups_equal_historical_rows_and_captures_sources(
    session: Session,
) -> None:
    variant = _source(
        session,
        name="quotes/bearings.xlsx",
        purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
        supplier="Bearing Co",
    )
    first, _ = _row(
        session,
        variant,
        row_number=2,
        name=" Bearing ",
        spec="6204-zz",
        unit="ea",
        price="100",
    )
    second, _ = _row(
        session,
        variant,
        row_number=3,
        name="BEARING",
        spec="6204 ZZ",
        unit="EA",
        price="200",
    )

    result = build_standard_database(session)
    session.commit()

    assert result.standard_item_count == 1
    assert result.observation_count == 2
    assert result.single_observation_count == 0
    assert result.created_count == 1
    assert result.reused_count == 0
    assert result.changed_count == 0
    version = session.scalar(select(StandardItemVersion))
    price = session.scalar(select(StandardPriceVersion))
    assert version is not None
    assert (
        version.canonical_name,
        version.canonical_spec,
        version.canonical_unit,
    ) == ("BEARING", "6204 ZZ", "EA")
    assert price is not None
    assert price.observation_count == 2
    assert price.minimum_price == Decimal("100.000000")
    assert price.median_price == Decimal("150.000000")
    assert price.average_price == Decimal("150.000000")
    assert price.maximum_price == Decimal("200.000000")
    assert price.approved_by == "LOCAL_STANDARD_DB_BUILD"
    assert price.calculation_version == CALCULATION_VERSION
    observations = list(
        session.scalars(
            select(StandardPriceObservation).order_by(
                StandardPriceObservation.raw_item_id
            )
        )
    )
    assert [row.raw_item_id for row in observations] == [first.id, second.id]
    assert {
        row.clean_decision.raw_item.source_variant.path
        for row in observations
    } == {"quotes/bearings.xlsx"}
    memberships = list(session.scalars(select(ItemMembershipDecision)))
    assert {row.method for row in memberships} == {RULE_VERSION}
    evidence = json.loads(memberships[0].evidence_json)
    assert evidence["build_run_id"] == result.run_id
    assert evidence["rule_version"] == RULE_VERSION
    assert evidence["normalization_version"] == NORMALIZATION_VERSION


def test_build_captures_single_historical_observation(
    session: Session,
) -> None:
    variant = _source(
        session,
        name="quotes/motors.xlsx",
        purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
    )
    _row(
        session,
        variant,
        row_number=2,
        name="Motor",
        spec="3 kw",
        unit="set",
        price="350000",
    )

    result = build_standard_database(session)
    session.commit()

    price = session.scalar(select(StandardPriceVersion))
    assert result.standard_item_count == 1
    assert result.observation_count == 1
    assert result.single_observation_count == 1
    assert price is not None
    assert price.observation_count == 1
    assert price.minimum_price == price.median_price
    assert price.median_price == price.average_price
    assert price.average_price == price.maximum_price
    assert price.maximum_price == Decimal("350000.000000")


def test_same_name_and_spec_with_different_units_stay_separate(
    session: Session,
) -> None:
    variant = _source(
        session,
        name="quotes/cable.xlsx",
        purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
    )
    _row(
        session,
        variant,
        row_number=2,
        name="Cable",
        spec="CV 4SQ",
        unit="M",
        price="1000",
    )
    _row(
        session,
        variant,
        row_number=3,
        name="Cable",
        spec="CV 4SQ",
        unit="ROLL",
        price="90000",
    )

    result = build_standard_database(session)
    session.commit()

    assert result.standard_item_count == 2
    assert result.unit_conflict_count == 1
    assert len(result.conflicts) == 1
    assert result.conflicts[0].code == "UNIT_CONFLICT"
    assert session.scalar(select(func.count(StandardItem.id))) == 2


def test_empty_normalized_name_is_reported_and_excluded(
    session: Session,
) -> None:
    variant = _source(
        session,
        name="quotes/empty.xlsx",
        purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
    )
    raw, _ = _row(
        session,
        variant,
        row_number=2,
        name="  ",
        spec="X1",
        unit="EA",
        price="10",
    )

    result = build_standard_database(session)
    session.commit()

    assert result.standard_item_count == 0
    assert result.observation_count == 0
    assert len(result.exclusions) == 1
    assert result.exclusions[0].raw_item_id == raw.id
    assert result.exclusions[0].code == "EMPTY_NORMALIZED_NAME"


def test_only_current_included_historical_rows_are_selected(
    session: Session,
) -> None:
    historical = _source(
        session,
        name="quotes/history.xlsx",
        purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
    )
    incoming = _source(
        session,
        name="quotes/incoming.xlsx",
        purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
    )
    historical_role = session.scalar(
        select(QuoteDocumentRole).where(
            QuoteDocumentRole.document_id == incoming.document_id
        )
    )
    assert historical_role is not None
    session.add(
        QuoteDocumentRole(
            document_id=incoming.document_id,
            purpose=QuoteDocumentPurpose.INCOMING_BID,
            supersedes_role_id=historical_role.id,
            decided_by="buyer",
            reason_detail="current incoming classification",
        )
    )
    session.flush()
    _row(
        session,
        historical,
        row_number=2,
        name="Included history",
        spec=None,
        unit="EA",
        price="10",
    )
    excluded, _ = _row(
        session,
        historical,
        row_number=3,
        name="Excluded history",
        spec=None,
        unit="EA",
        price="20",
        status=CleanStatus.EXCLUDED,
    )
    _row(
        session,
        incoming,
        row_number=2,
        name="Incoming only",
        spec=None,
        unit="EA",
        price="30",
    )
    session.add(
        CleanDecision(
            raw_item=excluded,
            status=CleanStatus.REVIEW_REQUIRED,
            reason_code="LATEST_REVIEW",
            item_name_norm="Excluded history",
            unit_norm="EA",
            unit_price=Decimal("20"),
            rule_version="clean-v2",
        )
    )
    session.flush()

    result = build_standard_database(session)
    session.commit()

    assert result.standard_item_count == 1
    assert result.observation_count == 1
    assert session.scalar(select(func.count(StandardItem.id))) == 1


def test_fingerprint_is_order_stable_and_tracks_decision_role_and_price() -> None:
    base = EligibleHistoricalRow(
        raw_item_id=10,
        clean_decision_id=20,
        document_role_id=30,
        source_document_id=40,
        source_document_name="quotes/a.xlsx",
        source_variant_id=50,
        source_variant_path="quotes/a.xlsx",
        source_variant_sha256="a" * 64,
        source_sheet="Sheet1",
        source_page=None,
        source_row=2,
        normalized_name="BEARING",
        normalized_spec="6204 ZZ",
        normalized_unit="EA",
        maker="SKF",
        unit_price=Decimal("100.000000"),
        metadata_version_id=60,
        supplier_name="Supplier",
        quote_date=date(2026, 7, 1),
    )
    second = replace(base, raw_item_id=11, clean_decision_id=21)

    fingerprint = standard_build_fingerprint([base, second])

    assert fingerprint == standard_build_fingerprint([second, base])
    assert len(fingerprint) == 64
    assert fingerprint == fingerprint.lower()
    assert fingerprint != standard_build_fingerprint(
        [replace(base, clean_decision_id=22), second]
    )
    assert fingerprint != standard_build_fingerprint(
        [replace(base, document_role_id=31), second]
    )
    assert fingerprint != standard_build_fingerprint(
        [replace(base, unit_price=Decimal("100.01")), second]
    )


def test_build_marks_run_succeeded_without_committing(session: Session) -> None:
    variant = _source(
        session,
        name="quotes/run.xlsx",
        purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
    )
    _row(
        session,
        variant,
        row_number=2,
        name="Relay",
        spec="24VDC",
        unit="EA",
        price="12",
    )

    result = build_standard_database(session)

    run = session.get(StandardDatabaseBuildRun, result.run_id)
    assert run is not None
    assert run.status is StandardBuildStatus.SUCCEEDED
    assert run.finished_at is not None
    assert json.loads(run.counts_json)["observation_count"] == 1
    session.rollback()
    assert session.get(StandardDatabaseBuildRun, result.run_id) is None


def test_build_fingerprint_includes_invalid_current_price_evidence(
    session: Session,
) -> None:
    variant = _source(
        session,
        name="quotes/invalid-price.xlsx",
        purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
    )
    raw, _ = _row(
        session,
        variant,
        row_number=2,
        name="Invalid price",
        spec=None,
        unit="EA",
        price="-1",
    )

    first = build_standard_database(session)
    first_run = session.get(StandardDatabaseBuildRun, first.run_id)
    assert first_run is not None
    first_fingerprint = first_run.input_fingerprint
    session.commit()
    session.add(
        CleanDecision(
            raw_item=raw,
            status=CleanStatus.INCLUDED,
            reason_code="LATEST_INVALID_PRICE",
            item_name_norm="Invalid price",
            spec_norm=None,
            unit_norm="EA",
            unit_price=Decimal("-2"),
            rule_version="clean-v2",
        )
    )
    session.flush()

    second = build_standard_database(session)
    second_run = session.get(StandardDatabaseBuildRun, second.run_id)

    assert second_run is not None
    assert second_run.input_fingerprint != first_fingerprint
    assert second.standard_item_count == 0
    assert second.exclusions[0].code == "MISSING_OR_INVALID_PRICE"


def test_build_reuses_exact_catalog_membership_and_unchanged_price(
    session: Session,
) -> None:
    variant = _source(
        session,
        name="quotes/reuse.xlsx",
        purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
    )
    raw, _ = _row(
        session,
        variant,
        row_number=2,
        name="Bearing",
        spec="6204 ZZ",
        unit="EA",
        price="100",
    )
    item = StandardItem()
    version = StandardItemVersion(
        standard_item=item,
        version_number=1,
        canonical_name="BEARING",
        canonical_spec="6204 ZZ",
        canonical_unit="EA",
        aliases_json="[]",
        created_by="buyer",
        change_reason="existing exact catalog identity",
    )
    membership = ItemMembershipDecision(
        raw_item=raw,
        standard_item=item,
        status=MembershipStatus.MATCHED,
        method="MANUAL",
        evidence_json="{}",
        decided_by="buyer",
    )
    session.add_all([version, membership])
    session.flush()
    draft = calculate_standard_price(session, item.id)
    approve_standard_price(
        session,
        item.id,
        expected_fingerprint=draft.fingerprint,
        expected_current_version_id=None,
        approved_by="buyer",
    )
    session.commit()

    result = build_standard_database(session)
    session.commit()

    assert result.created_count == 0
    assert result.reused_count == 1
    assert result.changed_count == 0
    assert session.scalar(select(func.count(StandardItem.id))) == 1
    assert (
        session.scalar(select(func.count(StandardItemVersion.id))) == 1
    )
    assert (
        session.scalar(select(func.count(ItemMembershipDecision.id))) == 1
    )
    assert (
        session.scalar(select(func.count(StandardPriceVersion.id))) == 1
    )


def test_reused_item_price_excludes_existing_incoming_bid_membership(
    session: Session,
) -> None:
    historical_variant = _source(
        session,
        name="quotes/historical-bearing.xlsx",
        purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
    )
    incoming_variant = _source(
        session,
        name="quotes/incoming-bearing.xlsx",
        purpose=QuoteDocumentPurpose.INCOMING_BID,
    )
    historical_raw, _ = _row(
        session,
        historical_variant,
        row_number=2,
        name="Bearing",
        spec="6204 ZZ",
        unit="EA",
        price="100",
    )
    incoming_raw, _ = _row(
        session,
        incoming_variant,
        row_number=2,
        name="Bearing",
        spec="6204 ZZ",
        unit="EA",
        price="900",
    )
    item = StandardItem()
    session.add_all(
        [
            StandardItemVersion(
                standard_item=item,
                version_number=1,
                canonical_name="BEARING",
                canonical_spec="6204 ZZ",
                canonical_unit="EA",
                aliases_json="[]",
                created_by="buyer",
                change_reason="existing exact catalog identity",
            ),
            ItemMembershipDecision(
                raw_item=incoming_raw,
                standard_item=item,
                status=MembershipStatus.MATCHED,
                method="MANUAL",
                evidence_json="{}",
                decided_by="buyer",
            ),
        ]
    )
    session.flush()

    result = build_standard_database(session)
    session.commit()

    price = session.scalar(select(StandardPriceVersion))
    observations = list(session.scalars(select(StandardPriceObservation)))
    assert result.reused_count == 1
    assert result.observation_count == 1
    assert result.single_observation_count == 1
    assert price is not None
    assert price.observation_count == 1
    assert price.minimum_price == Decimal("100.000000")
    assert price.median_price == Decimal("100.000000")
    assert price.average_price == Decimal("100.000000")
    assert price.maximum_price == Decimal("100.000000")
    assert [row.raw_item_id for row in observations] == [historical_raw.id]
