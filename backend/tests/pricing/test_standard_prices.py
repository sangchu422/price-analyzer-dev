from __future__ import annotations

from datetime import date
from decimal import Decimal
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.catalog.models import (
    DocumentMetadataVersion,
    ItemMembershipDecision,
    MembershipStatus,
    StandardItem,
    StandardItemVersion,
    StandardPriceVersion,
)
from app.cleansing.models import CleanDecision, CleanStatus
from app.db.base import Base
from app.db.sqlite import configure_sqlite
from app.documents.models import SourceDocument, SourceVariant
from app.pricing.service import (
    PriceDraftChanged,
    PriceStatistics,
    approve_standard_price,
    calculate_standard_price,
)
from app.quotes.models import RawQuoteItem


def _session() -> Session:
    engine = configure_sqlite(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(engine)
    return Session(engine)


def _item(session: Session) -> StandardItem:
    item = StandardItem()
    item.versions.append(
        StandardItemVersion(
            version_number=1,
            canonical_name="BEARING",
            canonical_spec="6204 ZZ",
            canonical_unit="EA",
            created_by="buyer",
        )
    )
    session.add(item)
    session.flush()
    return item


def _observation(
    session: Session,
    item: StandardItem,
    *,
    row: int,
    price: str | None,
    clean_status: CleanStatus = CleanStatus.INCLUDED,
    membership_status: MembershipStatus = MembershipStatus.MATCHED,
    membership_item: StandardItem | None = None,
    unit: str | None = "EA",
    supplier: str | None = None,
    quote_date: date | None = None,
) -> tuple[RawQuoteItem, CleanDecision, ItemMembershipDecision]:
    document = SourceDocument(logical_name=f"quote-{row}.xlsx")
    variant = SourceVariant(
        document=document,
        path=f"quote-{row}.xlsx",
        sha256=f"{row:064x}",
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    raw = RawQuoteItem(
        source_variant=variant,
        source_sheet="Sheet1",
        source_row=row,
        item_name_raw="BEARING",
        parser_name="xlsx",
        parser_version="1",
    )
    clean = CleanDecision(
        raw_item=raw,
        status=clean_status,
        reason_code="TEST",
        item_name_norm="BEARING",
        spec_norm="6204 ZZ",
        unit_norm=unit,
        unit_price=None if price is None else Decimal(price),
        rule_version="clean-v1",
    )
    membership = ItemMembershipDecision(
        raw_item=raw,
        standard_item=(
            item if membership_item is None else membership_item
        ) if membership_status is MembershipStatus.MATCHED else None,
        status=membership_status,
        method="MANUAL",
        evidence_json="{}",
        decided_by="buyer",
    )
    if supplier is not None or quote_date is not None:
        document_metadata = DocumentMetadataVersion(
            source_document=document,
            version_number=1,
            supplier_name=supplier,
            quote_date=quote_date,
            project_name=None,
            decided_by="buyer",
        )
        session.add(document_metadata)
    session.add_all([document, clean, membership])
    session.flush()
    return raw, clean, membership


def test_price_draft_uses_exact_statistics_and_current_metadata() -> None:
    with _session() as session:
        item = _item(session)
        _observation(
            session,
            item,
            row=1,
            price="100",
            supplier="A",
            quote_date=date(2026, 7, 1),
        )
        _observation(
            session,
            item,
            row=2,
            price="120",
            supplier="A",
            quote_date=date(2026, 7, 20),
        )
        _observation(session, item, row=3, price="200", supplier="B")

        draft = calculate_standard_price(session, item.id)

        assert draft.observation_count == 3
        assert draft.prices == PriceStatistics(
            minimum=Decimal("100.000000"),
            median=Decimal("120.000000"),
            average=Decimal("140.000000"),
            maximum=Decimal("200.000000"),
        )
        assert draft.supplier_count == 2
        assert draft.latest_quote_date == date(2026, 7, 20)
        assert [row.source.path for row in draft.observations] == [
            "quote-1.xlsx",
            "quote-2.xlsx",
            "quote-3.xlsx",
        ]


def test_draft_uses_only_latest_included_and_latest_matched_target() -> None:
    with _session() as session:
        item = _item(session)
        other = _item(session)
        valid_raw, valid_clean, valid_membership = _observation(
            session, item, row=1, price="100"
        )
        excluded_raw, _, _ = _observation(
            session, item, row=2, price="200"
        )
        session.add(
            CleanDecision(
                raw_item=excluded_raw,
                status=CleanStatus.EXCLUDED,
                reason_code="OUTLIER",
                rule_version="clean-v2",
            )
        )
        moved_raw, _, moved_membership = _observation(
            session, item, row=3, price="300"
        )
        session.add(
            ItemMembershipDecision(
                raw_item=moved_raw,
                standard_item=other,
                status=MembershipStatus.MATCHED,
                method="MANUAL",
                evidence_json="{}",
                supersedes=moved_membership,
                decided_by="buyer",
            )
        )
        _observation(session, item, row=4, price="0")
        _observation(session, item, row=5, price="400", unit="KG")
        session.flush()

        draft = calculate_standard_price(session, item.id)

        assert draft.observation_count == 1
        assert draft.decision_ids == (valid_clean.id,)
        assert draft.membership_decision_ids == (valid_membership.id,)
        assert draft.context.excluded_count == 1
        assert draft.context.invalid_price_count == 1
        assert draft.context.unit_incompatible_count == 1
        assert draft.context.other_target_count == 1


def test_one_observation_with_missing_metadata_has_empty_metadata_counts() -> None:
    with _session() as session:
        item = _item(session)
        _observation(session, item, row=1, price="100")
        draft = calculate_standard_price(session, item.id)
        assert draft.observation_count == 1
        assert draft.supplier_count == 0
        assert draft.latest_quote_date is None
        assert draft.prices == PriceStatistics(
            minimum=Decimal("100"),
            median=Decimal("100"),
            average=Decimal("100"),
            maximum=Decimal("100"),
        )


def test_even_median_and_repeating_average_are_exact() -> None:
    with _session() as session:
        item = _item(session)
        _observation(session, item, row=1, price="1")
        _observation(session, item, row=2, price="2")
        draft = calculate_standard_price(session, item.id)
        assert draft.prices.median == Decimal("1.500000")
        assert draft.prices.average == Decimal("1.500000")


def test_repeating_average_is_rounded_only_when_persisted() -> None:
    with _session() as session:
        item = _item(session)
        _observation(session, item, row=1, price="1")
        _observation(session, item, row=2, price="1")
        _observation(session, item, row=3, price="2")
        draft = calculate_standard_price(session, item.id)
        assert draft.prices.average == (
            Decimal("4.000000") / Decimal("3")
        )
        version = approve_standard_price(
            session,
            item.id,
            expected_fingerprint=draft.fingerprint,
            expected_current_version_id=None,
            approved_by="buyer",
        )
        assert version.average_price == Decimal("1.333333")


def test_draft_does_not_mutate_database() -> None:
    with _session() as session:
        item = _item(session)
        _observation(session, item, row=1, price="100")
        before_new = set(session.new)
        calculate_standard_price(session, item.id)
        assert set(session.new) == before_new
        assert session.query(StandardPriceVersion).count() == 0


def test_approval_atomically_persists_version_and_normalized_evidence() -> None:
    with _session() as session:
        item = _item(session)
        _observation(session, item, row=1, price="100")
        _observation(session, item, row=2, price="120")
        draft = calculate_standard_price(session, item.id)

        version = approve_standard_price(
            session,
            item.id,
            expected_fingerprint=draft.fingerprint,
            expected_current_version_id=None,
            approved_by="buyer-1",
        )
        session.flush()

        assert version.version_number == 1
        assert len(version.observations) == 2
        assert {row.clean_decision_id for row in version.observations} == set(
            draft.decision_ids
        )
        assert version.draft_fingerprint == draft.fingerprint
        assert version.excluded_count == 0
        assert version.review_required_count == 0
        assert version.exclusion_context_json == "[]"


def test_approval_rejects_changed_draft_and_stale_current_version() -> None:
    with _session() as session:
        item = _item(session)
        _observation(session, item, row=1, price="100")
        first = calculate_standard_price(session, item.id)
        _observation(session, item, row=2, price="120")
        with pytest.raises(PriceDraftChanged):
            approve_standard_price(
                session,
                item.id,
                expected_fingerprint=first.fingerprint,
                expected_current_version_id=None,
                approved_by="buyer",
            )
        current = calculate_standard_price(session, item.id)
        version = approve_standard_price(
            session,
            item.id,
            expected_fingerprint=current.fingerprint,
            expected_current_version_id=None,
            approved_by="buyer",
        )
        session.flush()
        with pytest.raises(PriceDraftChanged):
            approve_standard_price(
                session,
                item.id,
                expected_fingerprint=current.fingerprint,
                expected_current_version_id=None,
                approved_by="buyer",
            )
        assert version.version_number == 1


def test_metadata_change_invalidates_price_draft_fingerprint() -> None:
    with _session() as session:
        item = _item(session)
        raw, _, _ = _observation(
            session,
            item,
            row=1,
            price="100",
            supplier="A",
            quote_date=date(2026, 7, 1),
        )
        first = calculate_standard_price(session, item.id)
        session.add(
            DocumentMetadataVersion(
                source_document_id=raw.source_variant.document_id,
                version_number=2,
                supplier_name="A CO.",
                quote_date=date(2026, 7, 2),
                project_name=None,
                decided_by="buyer",
            )
        )
        session.flush()
        second = calculate_standard_price(session, item.id)
        assert second.fingerprint != first.fingerprint
        with pytest.raises(PriceDraftChanged):
            approve_standard_price(
                session,
                item.id,
                expected_fingerprint=first.fingerprint,
                expected_current_version_id=None,
                approved_by="buyer",
            )


def test_exclusion_context_change_invalidates_draft_fingerprint() -> None:
    with _session() as session:
        item = _item(session)
        _observation(session, item, row=1, price="100")
        raw, _, _ = _observation(
            session,
            item,
            row=2,
            price="200",
            clean_status=CleanStatus.EXCLUDED,
        )
        first = calculate_standard_price(session, item.id)
        session.add(
            CleanDecision(
                raw_item=raw,
                status=CleanStatus.REVIEW_REQUIRED,
                reason_code="MANUAL_REVIEW",
                rule_version="clean-v2",
            )
        )
        session.flush()
        second = calculate_standard_price(session, item.id)
        assert first.decision_ids == second.decision_ids
        assert first.fingerprint != second.fingerprint


def test_approval_freezes_exclusion_context_without_future_recalculation() -> None:
    with _session() as session:
        item = _item(session)
        _observation(session, item, row=1, price="100")
        excluded_raw, excluded_clean, excluded_membership = _observation(
            session,
            item,
            row=2,
            price="200",
            clean_status=CleanStatus.EXCLUDED,
        )
        review_raw, review_clean, review_membership = _observation(
            session,
            item,
            row=3,
            price="300",
            clean_status=CleanStatus.REVIEW_REQUIRED,
        )
        draft = calculate_standard_price(session, item.id)
        version = approve_standard_price(
            session,
            item.id,
            expected_fingerprint=draft.fingerprint,
            expected_current_version_id=None,
            approved_by="buyer",
        )
        frozen = version.exclusion_context_json
        assert version.excluded_count == 1
        assert version.review_required_count == 1
        context = json.loads(frozen)
        assert {
            (
                row["raw_item_id"],
                row["reason"],
                row["clean_decision_id"],
                row["membership_decision_id"],
                row["source"]["path"],
            )
            for row in context
        } == {
            (
                excluded_raw.id,
                "EXCLUDED",
                excluded_clean.id,
                excluded_membership.id,
                "quote-2.xlsx",
            ),
            (
                review_raw.id,
                "REVIEW_REQUIRED",
                review_clean.id,
                review_membership.id,
                "quote-3.xlsx",
            ),
        }
        session.add(
            CleanDecision(
                raw_item=excluded_raw,
                status=CleanStatus.INCLUDED,
                reason_code="RESTORED",
                item_name_norm="BEARING",
                unit_norm="EA",
                unit_price=Decimal("200"),
                rule_version="clean-v2",
            )
        )
        session.flush()
        assert version.exclusion_context_json == frozen
        assert version.excluded_count == 1


def test_calculation_does_not_autoflush_pending_decisions() -> None:
    with _session() as session:
        item = _item(session)
        raw, included, _ = _observation(
            session, item, row=1, price="100"
        )
        pending = CleanDecision(
            raw_item=raw,
            status=CleanStatus.EXCLUDED,
            reason_code="PENDING",
            rule_version="clean-v2",
        )
        session.add(pending)
        draft = calculate_standard_price(session, item.id)
        assert draft.decision_ids == (included.id,)
        assert pending.id is None
        assert pending in session.new
