from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.analysis.service import analyze_document
from app.catalog.models import (
    ItemMembershipDecision,
    MembershipStatus,
    StandardItem,
    StandardItemVersion,
    StandardPriceVersion,
)
from app.catalog.service import CandidateEmbeddingRuntime
from app.cleansing.models import CleanDecision, CleanStatus
from app.db.base import Base
from app.db.sqlite import configure_sqlite
from app.documents.models import SourceDocument, SourceVariant
from app.embeddings.base import EmbeddingBatch
from app.embeddings.index import EmbeddingIndex, IndexMetadata
from app.pricing.service import (
    approve_standard_price,
    calculate_standard_price,
)
from app.quotes.models import RawQuoteItem
from app.standard_database.models import (
    QuoteDocumentPurpose,
    QuoteDocumentRole,
)
from app.standard_database.service import build_standard_database


def _session() -> Session:
    engine = configure_sqlite(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(engine)
    return Session(engine)


def _item(
    session: Session,
    *,
    name: str = "BEARING",
    spec: str = "6204 ZZ",
) -> tuple[StandardItem, StandardItemVersion]:
    item = StandardItem()
    version = StandardItemVersion(
        standard_item=item,
        version_number=1,
        canonical_name=name,
        canonical_spec=spec,
        canonical_unit="EA",
        aliases_json='["BALL BEARING"]',
        created_by="buyer",
    )
    session.add_all([item, version])
    session.flush()
    return item, version


def _document(session: Session, name: str) -> SourceDocument:
    document = SourceDocument(logical_name=name)
    document.variants.append(
        SourceVariant(
            path=name,
            sha256=f"{len(name):064x}",
            extension=".xlsx",
            security_state="UNLOCKED",
            selected_for_parsing_at_ingest=True,
        )
    )
    session.add(document)
    session.flush()
    return document


def _row(
    session: Session,
    document: SourceDocument,
    *,
    row: int,
    name: str = "BEARING",
    spec: str = "6204 ZZ",
    price: str | None = "150",
    quantity: str | None = "2",
    amount: str | None = None,
    status: CleanStatus = CleanStatus.INCLUDED,
    item: StandardItem | None = None,
) -> tuple[RawQuoteItem, CleanDecision, ItemMembershipDecision | None]:
    raw = RawQuoteItem(
        source_variant=document.variants[0],
        source_sheet="Sheet1",
        source_row=row,
        source_cells=f"A{row}:G{row}",
        item_name_raw=name,
        spec_raw=spec,
        unit_raw="EA",
        unit_price_raw=price,
        quantity_raw=quantity,
        amount_raw=amount,
        parser_name="xlsx",
        parser_version="reader-v1",
    )
    clean = CleanDecision(
        raw_item=raw,
        status=status,
        reason_code="TEST",
        item_name_norm=name,
        spec_norm=spec,
        unit_norm="EA",
        unit_price=None if price is None else Decimal(price),
        quantity=None if quantity is None else Decimal(quantity),
        amount=None if amount is None else Decimal(amount),
        rule_version="clean-v1",
    )
    membership = None
    if item is not None:
        membership = ItemMembershipDecision(
            raw_item=raw,
            standard_item=item,
            status=MembershipStatus.MATCHED,
            method="MANUAL",
            evidence_json="{}",
            decided_by="buyer",
        )
        session.add(membership)
    session.add_all([raw, clean])
    session.flush()
    return raw, clean, membership


def test_incoming_exact_normalized_key_is_compared_without_membership_mutation() -> None:
    with _session() as session:
        item, item_version = _item(session)
        approved_price = _approve_reference_price(session, item)
        quote = _document(session, "incoming-exact.xlsx")
        raw, clean, _ = _row(
            session,
            quote,
            row=9,
            name=" bearing ",
            spec="6204-zz",
            price="132",
            quantity="3",
            amount="396",
        )

        before_memberships = session.query(ItemMembershipDecision).count()
        line = analyze_document(
            session, quote.id, deterministic_exact_match=True
        ).lines[0]

        assert line.match_status == "MATCHED"
        assert line.standard_item_id == item.id
        assert line.standard_item_version_id == item_version.id
        assert line.standard_price_version_id == approved_price.id
        assert line.membership_decision_id is None
        assert line.quantity == Decimal("3.000000")
        assert line.quote_amount == Decimal("396.000000")
        assert line.raw_item_id == raw.id
        assert line.clean_decision_id == clean.id
        assert session.query(ItemMembershipDecision).count() == before_memberships


def test_incoming_exact_key_does_not_attach_standard_to_excluded_row() -> None:
    with _session() as session:
        item, _ = _item(session)
        _approve_reference_price(session, item)
        quote = _document(session, "incoming-excluded.xlsx")
        _row(
            session,
            quote,
            row=10,
            status=CleanStatus.EXCLUDED,
        )

        line = analyze_document(
            session, quote.id, deterministic_exact_match=True
        ).lines[0]

        assert line.match_status == "EXCLUDED"
        assert line.standard_item_id is None
        assert line.standard_price_version_id is None


def test_incoming_exact_key_respects_latest_rejected_membership() -> None:
    with _session() as session:
        item, _ = _item(session)
        _approve_reference_price(session, item)
        quote = _document(session, "incoming-rejected.xlsx")
        raw, _, _ = _row(session, quote, row=11)
        rejected = ItemMembershipDecision(
            raw_item_id=raw.id,
            standard_item_id=None,
            status=MembershipStatus.REJECTED,
            method="MANUAL",
            evidence_json="{}",
            decided_by="buyer",
        )
        session.add(rejected)
        session.flush()
        before_memberships = session.query(ItemMembershipDecision).count()
        before_prices = session.query(StandardPriceVersion).count()

        line = analyze_document(
            session, quote.id, deterministic_exact_match=True
        ).lines[0]

        assert line.match_status == "CANDIDATE"
        assert line.membership_decision_id == rejected.id
        assert line.standard_item_id is None
        assert line.reference_price is None
        assert line.standard_price_version_id is None
        assert session.query(ItemMembershipDecision).count() == before_memberships
        assert session.query(StandardPriceVersion).count() == before_prices


def _approve_reference_price(
    session: Session,
    item: StandardItem,
    *,
    price: str = "120",
):
    history = _document(session, "history.xlsx")
    session.add(
        QuoteDocumentRole(
            document_id=history.id,
            purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
            decided_by="buyer",
            reason_detail="test historical price evidence",
        )
    )
    _row(session, history, row=1, price=price, item=item)
    draft = calculate_standard_price(session, item.id)
    return approve_standard_price(
        session,
        item.id,
        expected_fingerprint=draft.fingerprint,
        expected_current_version_id=None,
        approved_by="buyer",
    )


def _activate_catalog_item_without_price(
    session: Session,
    item: StandardItem,
) -> None:
    history = _document(session, f"history-no-price-{item.id}.xlsx")
    session.add(
        QuoteDocumentRole(
            document_id=history.id,
            purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
            decided_by="buyer",
            reason_detail="test historical evidence without a usable price",
        )
    )
    _row(session, history, row=1, price=None, item=item)


def test_matched_line_uses_latest_approved_median_and_exact_provenance() -> None:
    with _session() as session:
        item, item_version = _item(session)
        approved_price = _approve_reference_price(session, item)
        quote = _document(session, "new-quote.xlsx")
        raw, clean, membership = _row(
            session,
            quote,
            row=7,
            price="150",
            item=item,
        )

        result = analyze_document(session, quote.id)

        line = result.lines[0]
        assert line.match_status == "MATCHED"
        assert line.quote_unit_price == Decimal("150.000000")
        assert line.reference_price == approved_price.median_price
        assert line.variance_amount == Decimal("30.000000")
        assert line.variance_percent == Decimal("25.000000")
        assert line.assessment == "HIGH"
        assert line.raw_item_id == raw.id
        assert line.clean_decision_id == clean.id
        assert line.membership_decision_id == membership.id
        assert line.standard_item_id == item.id
        assert line.standard_item_version_id == item_version.id
        assert line.standard_price_version_id == approved_price.id
        assert line.source.document_id == quote.id
        assert line.source.variant_id == quote.variants[0].id
        assert line.source.row == 7


def test_stale_price_is_inactive_until_rebuild_captures_remaining_evidence() -> None:
    with _session() as session:
        item, _ = _item(session)
        historical_rows: list[RawQuoteItem] = []
        for index, price in enumerate(("100", "120"), start=1):
            history = _document(session, f"history-{index}.xlsx")
            session.add(
                QuoteDocumentRole(
                    document_id=history.id,
                    purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
                    decided_by="buyer",
                    reason_detail="test historical price evidence",
                )
            )
            raw, _, _ = _row(
                session,
                history,
                row=index,
                price=price,
                item=item,
            )
            historical_rows.append(raw)
        draft = calculate_standard_price(session, item.id)
        old_price = approve_standard_price(
            session,
            item.id,
            expected_fingerprint=draft.fingerprint,
            expected_current_version_id=None,
            approved_by="buyer",
        )
        incoming = _document(session, "incoming-lifecycle.xlsx")
        _row(session, incoming, row=1, price="130")

        assert (
            analyze_document(
                session,
                incoming.id,
                deterministic_exact_match=True,
            ).lines[0].match_status
            == "MATCHED"
        )

        session.add(
            CleanDecision(
                raw_item_id=historical_rows[0].id,
                status=CleanStatus.EXCLUDED,
                reason_code="LIFECYCLE_CHANGE",
                item_name_norm="BEARING",
                spec_norm="6204 ZZ",
                unit_norm="EA",
                rule_version="clean-v2",
            )
        )
        session.flush()

        stale = analyze_document(
            session,
            incoming.id,
            deterministic_exact_match=True,
        ).lines[0]
        assert stale.match_status == "MATCHED_NO_PRICE"
        assert stale.standard_price_version_id is None
        assert session.query(StandardPriceVersion).count() == 1

        result = build_standard_database(session)
        session.flush()
        assert result.created_price_versions == 1

        rebuilt = analyze_document(
            session,
            incoming.id,
            deterministic_exact_match=True,
        ).lines[0]
        assert rebuilt.match_status == "MATCHED"
        assert rebuilt.standard_price_version_id != old_price.id
        assert session.query(StandardPriceVersion).count() == 2


def test_changed_clean_values_invalidate_price_until_rebuild() -> None:
    with _session() as session:
        item, _ = _item(session)
        historical_rows: list[RawQuoteItem] = []
        for index, price in enumerate(("100", "120"), start=1):
            history = _document(session, f"history-value-{index}.xlsx")
            session.add(
                QuoteDocumentRole(
                    document_id=history.id,
                    purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
                    decided_by="buyer",
                    reason_detail="test historical price evidence",
                )
            )
            raw, _, _ = _row(
                session,
                history,
                row=index,
                price=price,
                item=item,
            )
            historical_rows.append(raw)
        draft = calculate_standard_price(session, item.id)
        old_price = approve_standard_price(
            session,
            item.id,
            expected_fingerprint=draft.fingerprint,
            expected_current_version_id=None,
            approved_by="buyer",
        )
        incoming = _document(session, "incoming-value-change.xlsx")
        _row(session, incoming, row=1, price="130")

        session.add(
            CleanDecision(
                raw_item_id=historical_rows[0].id,
                status=CleanStatus.INCLUDED,
                reason_code="CORRECTED_PRICE",
                item_name_norm="BEARING",
                spec_norm="6204 ZZ",
                unit_norm="EA",
                unit_price=Decimal("200"),
                maker_norm="SKF",
                rule_version="clean-v2",
            )
        )
        session.flush()

        stale = analyze_document(
            session,
            incoming.id,
            deterministic_exact_match=True,
        ).lines[0]
        assert stale.match_status == "MATCHED_NO_PRICE"
        assert stale.standard_price_version_id is None
        assert session.query(StandardPriceVersion).count() == 1

        result = build_standard_database(session)
        session.flush()
        assert result.created_price_versions == 1

        rebuilt = analyze_document(
            session,
            incoming.id,
            deterministic_exact_match=True,
        ).lines[0]
        assert rebuilt.match_status == "MATCHED"
        assert rebuilt.standard_price_version_id != old_price.id
        assert rebuilt.reference_price == Decimal("160.000000")
        assert session.query(StandardPriceVersion).count() == 2


def test_equivalent_clean_supersession_still_follows_draft_fingerprint() -> None:
    with _session() as session:
        item, _ = _item(session)
        old_price = _approve_reference_price(session, item, price="120")
        historical_raw = session.scalar(
            select(RawQuoteItem)
            .join(ItemMembershipDecision)
            .where(
                ItemMembershipDecision.standard_item_id == item.id,
                ItemMembershipDecision.status == MembershipStatus.MATCHED,
            )
        )
        incoming = _document(session, "incoming-equivalent-clean.xlsx")
        _row(session, incoming, row=1, price="130")

        before = analyze_document(
            session,
            incoming.id,
            deterministic_exact_match=True,
        ).lines[0]
        assert before.standard_price_version_id == old_price.id

        session.add(
            CleanDecision(
                raw_item_id=historical_raw.id,
                status=CleanStatus.INCLUDED,
                reason_code="REAFFIRMED",
                item_name_norm="BEARING",
                spec_norm="6204 ZZ",
                unit_norm="EA",
                unit_price=Decimal("120"),
                rule_version="clean-v2",
            )
        )
        session.flush()

        stale = analyze_document(
            session,
            incoming.id,
            deterministic_exact_match=True,
        ).lines[0]
        assert stale.match_status == "MATCHED_NO_PRICE"
        assert stale.standard_price_version_id is None

        result = build_standard_database(session)
        session.flush()
        assert result.created_price_versions == 1
        rebuilt = analyze_document(
            session,
            incoming.id,
            deterministic_exact_match=True,
        ).lines[0]
        assert rebuilt.match_status == "MATCHED"
        assert rebuilt.standard_price_version_id != old_price.id
        assert rebuilt.reference_price == Decimal("120.000000")


def test_candidate_never_applies_a_standard_price() -> None:
    with _session() as session:
        item, item_version = _item(session)
        _approve_reference_price(session, item)
        quote = _document(session, "candidate.xlsx")
        _row(session, quote, row=2, price="999")

        line = analyze_document(session, quote.id).lines[0]

        assert line.match_status == "CANDIDATE"
        assert line.assessment == "REVIEW_REQUIRED"
        assert line.reference_price is None
        assert line.standard_price_version_id is None
        assert line.variance_amount is None
        assert line.candidates[0].standard_item_id == item.id
        assert line.candidates[0].standard_item_version_id == item_version.id
        assert not hasattr(
            line.candidates[0], "standard_price_version_id"
        ), "candidate evidence must not expose an applicable price pointer"


def test_all_non_comparison_states_are_distinct() -> None:
    with _session() as session:
        matched_item, _ = _item(session)
        _activate_catalog_item_without_price(session, matched_item)
        quote = _document(session, "states.xlsx")
        _row(
            session,
            quote,
            row=1,
            name="DISCARDED",
            status=CleanStatus.EXCLUDED,
        )
        _row(
            session,
            quote,
            row=2,
            name="UNCERTAIN",
            status=CleanStatus.REVIEW_REQUIRED,
        )
        _row(
            session,
            quote,
            row=3,
            name="CUSTOM FABRICATION ZYX",
            spec="NO-CATALOG-ENTRY",
        )
        _row(session, quote, row=4, item=matched_item)

        result = analyze_document(session, quote.id)

        assert [line.match_status for line in result.lines] == [
            "EXCLUDED",
            "REVIEW_REQUIRED",
            "NO_MATCH",
            "MATCHED_NO_PRICE",
        ]
        assert result.lines[2].market_price_lookup_required is True
        assert result.lines[2].market_price_lookup_status == (
            "FUTURE_MARKET_LOOKUP"
        )
        assert all(
            line.reference_price is None
            for line in result.lines
        )


def test_variance_threshold_boundaries_are_configurable() -> None:
    with _session() as session:
        item, _ = _item(session)
        _approve_reference_price(session, item, price="100")
        quote = _document(session, "thresholds.xlsx")
        for row, price in enumerate(
            ("89.999999", "90", "110", "110.000001", "120", "120.000001"),
            start=1,
        ):
            _row(session, quote, row=row, price=price, item=item)

        result = analyze_document(
            session,
            quote.id,
            review_percent=Decimal("10"),
            high_percent=Decimal("20"),
        )

        assert [line.assessment for line in result.lines] == [
            "LOW",
            "WITHIN_RANGE",
            "WITHIN_RANGE",
            "REVIEW",
            "REVIEW",
            "HIGH",
        ]


def test_assessment_uses_exact_variance_before_display_rounding() -> None:
    with _session() as session:
        item, _ = _item(session)
        _approve_reference_price(session, item, price="1000000")
        quote = _document(session, "exact-thresholds.xlsx")
        for row, price in enumerate(
            (
                "899999.996",
                "900000",
                "1100000",
                "1100000.004",
                "1200000",
                "1200000.004",
            ),
            start=1,
        ):
            _row(session, quote, row=row, price=price, item=item)

        result = analyze_document(session, quote.id)

        assert [line.variance_percent for line in result.lines] == [
            Decimal("-10.000000"),
            Decimal("-10.000000"),
            Decimal("10.000000"),
            Decimal("10.000000"),
            Decimal("20.000000"),
            Decimal("20.000000"),
        ]
        assert [line.assessment for line in result.lines] == [
            "LOW",
            "WITHIN_RANGE",
            "WITHIN_RANGE",
            "REVIEW",
            "REVIEW",
            "HIGH",
        ]


def test_analysis_uses_only_the_current_preferred_parsing_variant() -> None:
    with _session() as session:
        document = SourceDocument(logical_name="quotes/sample")
        document.variants.append(
            SourceVariant(
                path="quotes/sample.xlsx",
                sha256="e" * 64,
                extension=".xlsx",
                security_state="UNKNOWN",
                selected_for_parsing_at_ingest=True,
            )
        )
        session.add(document)
        session.flush()
        _row(
            session,
            document,
            row=1,
            name="STALE ROW",
            spec="OLD",
        )
        current = SourceVariant(
            document=document,
            path="quotes/sample_보안해제.xlsx",
            sha256="f" * 64,
            extension=".xlsx",
            security_state="UNLOCKED",
            selected_for_parsing_at_ingest=True,
        )
        raw = RawQuoteItem(
            source_variant=current,
            source_sheet="Sheet1",
            source_row=2,
            item_name_raw="CURRENT ROW",
            spec_raw="NEW",
            unit_raw="EA",
            unit_price_raw="200",
            parser_name="xlsx",
            parser_version="reader-v2",
        )
        session.add(
            CleanDecision(
                raw_item=raw,
                status=CleanStatus.INCLUDED,
                reason_code="VALID",
                item_name_norm="CURRENT ROW",
                spec_norm="NEW",
                unit_norm="EA",
                unit_price=Decimal("200"),
                rule_version="clean-v2",
            )
        )
        session.flush()

        result = analyze_document(session, document.id)

        assert [line.item_name for line in result.lines] == ["CURRENT ROW"]
        assert result.lines[0].source.variant_id == current.id


def test_analysis_does_not_flush_pending_membership_mutations() -> None:
    with _session() as session:
        item, _ = _item(session)
        _activate_catalog_item_without_price(session, item)
        stored_before = session.connection().exec_driver_sql(
            "SELECT count(*) FROM item_membership_decision"
        ).scalar_one()
        quote = _document(session, "read-only.xlsx")
        raw, _, _ = _row(session, quote, row=1)
        pending = ItemMembershipDecision(
            raw_item=raw,
            standard_item=item,
            status=MembershipStatus.MATCHED,
            method="PENDING",
            evidence_json="{}",
            decided_by="buyer",
        )
        session.add(pending)

        result = analyze_document(session, quote.id)

        stored = session.connection().exec_driver_sql(
            "SELECT count(*) FROM item_membership_decision"
        ).scalar_one()
        assert result.lines[0].match_status == "CANDIDATE"
        assert pending.id is None
        assert stored == stored_before


class _CountingClient:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts) -> EmbeddingBatch:
        self.calls.append(list(texts))
        return EmbeddingBatch(
            vectors=np.repeat(
                np.array([[1.0, 0.0]], dtype=np.float32),
                len(texts),
                axis=0,
            ),
            model="office-model",
            dimension=2,
        )


def _embedding_runtime(
    item: StandardItem,
    client: _CountingClient,
) -> CandidateEmbeddingRuntime:
    return CandidateEmbeddingRuntime(
        client=client,
        index=EmbeddingIndex(
            item_ids=np.array([item.id]),
            vectors=np.array([[1.0, 0.0]], dtype=np.float32),
            metadata=IndexMetadata(
                model="office-model",
                dimension=2,
                item_count=1,
                catalog_fingerprint="test",
                normalization_version="match-v1",
                created_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
            ),
        ),
        model="office-model",
    )


@pytest.mark.parametrize("row_count", [5, 100])
def test_analysis_batches_candidate_embeddings_per_page(
    row_count: int,
) -> None:
    with _session() as session:
        item, _ = _item(session)
        _activate_catalog_item_without_price(session, item)
        quote = _document(session, f"batch-{row_count}.xlsx")
        for row in range(1, row_count + 1):
            _row(session, quote, row=row)
        client = _CountingClient()

        result = analyze_document(
            session,
            quote.id,
            limit=100,
            embedding_runtime=_embedding_runtime(item, client),
        )

        assert len(result.lines) == row_count
        assert len(client.calls) == 1
        assert len(client.calls[0]) == row_count
        assert result.lines[0].candidates[0].embedding_status == "AVAILABLE"
        assert result.lines[0].candidates[0].embedding_model == "office-model"


def test_analysis_embeds_only_included_unmatched_rows() -> None:
    with _session() as session:
        item, _ = _item(session)
        _activate_catalog_item_without_price(session, item)
        quote = _document(session, "eligible-batch.xlsx")
        _row(session, quote, row=1)
        _row(
            session,
            quote,
            row=2,
            status=CleanStatus.EXCLUDED,
        )
        _row(
            session,
            quote,
            row=3,
            status=CleanStatus.REVIEW_REQUIRED,
        )
        _row(session, quote, row=4, item=item)
        client = _CountingClient()

        analyze_document(
            session,
            quote.id,
            embedding_runtime=_embedding_runtime(item, client),
        )

        assert len(client.calls) == 1
        assert len(client.calls[0]) == 1


def _select_count(row_count: int) -> int:
    with _session() as session:
        item, _ = _item(session)
        _approve_reference_price(session, item)
        quote = _document(session, f"many-{row_count}.xlsx")
        for row in range(1, row_count + 1):
            _row(session, quote, row=row, item=item)
        engine = session.get_bind()
        selects = 0

        def count_selects(
            conn: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            nonlocal selects
            if statement.lstrip().upper().startswith("SELECT"):
                selects += 1

        event.listen(engine, "before_cursor_execute", count_selects)
        try:
            analyze_document(session, quote.id, limit=50)
        finally:
            event.remove(engine, "before_cursor_execute", count_selects)
        return selects


def test_analysis_query_count_does_not_grow_per_row() -> None:
    one = _select_count(1)
    many = _select_count(40)
    assert many == one
