from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.catalog.models import (
    ItemMembershipDecision,
    MembershipStatus,
    StandardItem,
    StandardItemVersion,
)
from app.cleansing.models import CleanDecision, CleanStatus
from app.db.base import Base
from app.db.sqlite import configure_sqlite
from app.documents.models import SourceDocument, SourceVariant
from app.parsing.models import ParseRunStatus, SourceParseOutput, SourceParseRun
from app.quotes.models import RawQuoteItem
from app.standard_database.models import QuoteDocumentPurpose, QuoteDocumentRole
from app.standard_database.operational import current_standard_member_counts


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
    session.flush()
    return variant


def _row(
    session: Session,
    variant: SourceVariant,
    *,
    row_number: int,
    name: str,
    spec: str,
    unit: str,
    price: str,
    parser_version: str,
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
        parser_version=parser_version,
    )
    decision = CleanDecision(
        raw_item=raw,
        status=CleanStatus.INCLUDED,
        reason_code="VALID",
        item_name_norm=name,
        spec_norm=spec,
        unit_norm=unit,
        unit_price=Decimal(price),
        rule_version="clean-v1",
    )
    session.add(decision)
    session.flush()
    return raw, decision


def test_stale_reparsed_raw_item_does_not_double_count_membership(
    session: Session,
) -> None:
    """A superseded reader-v1 parse must not inflate the member count
    alongside its reader-v2 reparse of the same physical row."""

    variant = _source(
        session,
        name="quotes/reparsed.xlsx",
        purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
    )
    stale_raw, _ = _row(
        session,
        variant,
        row_number=2,
        name="Bearing",
        spec="6204 ZZ",
        unit="EA",
        price="100",
        parser_version="reader-v1",
    )
    fresh_raw, _ = _row(
        session,
        variant,
        row_number=2,
        name="Bearing",
        spec="6204 ZZ",
        unit="EA",
        price="100",
        parser_version="reader-v2",
    )

    # Insert the stale run/output first so it receives the lower id, then the
    # fresh reparse second, matching production autoincrement ordering.
    stale_run = SourceParseRun(
        source_variant_id=variant.id,
        parser_name="xlsx",
        parser_version="reader-v1",
        code_fingerprint="a" * 64,
        status=ParseRunStatus.SUCCEEDED,
    )
    session.add(stale_run)
    session.flush()
    session.add(
        SourceParseOutput(parse_run_id=stale_run.id, raw_item_id=stale_raw.id)
    )
    session.flush()

    fresh_run = SourceParseRun(
        source_variant_id=variant.id,
        parser_name="xlsx",
        parser_version="reader-v2",
        code_fingerprint="b" * 64,
        status=ParseRunStatus.SUCCEEDED,
    )
    session.add(fresh_run)
    session.flush()
    session.add(
        SourceParseOutput(parse_run_id=fresh_run.id, raw_item_id=fresh_raw.id)
    )
    session.flush()

    assert fresh_run.id > stale_run.id

    item = StandardItem()
    session.add(item)
    session.flush()
    session.add(
        StandardItemVersion(
            standard_item=item,
            version_number=1,
            canonical_name="BEARING",
            canonical_spec="6204 ZZ",
            canonical_unit="EA",
            aliases_json="[]",
            created_by="buyer",
        )
    )
    session.add_all(
        [
            ItemMembershipDecision(
                raw_item=stale_raw,
                standard_item=item,
                status=MembershipStatus.MATCHED,
                method="MANUAL",
                evidence_json="{}",
                decided_by="buyer",
            ),
            ItemMembershipDecision(
                raw_item=fresh_raw,
                standard_item=item,
                status=MembershipStatus.MATCHED,
                method="MANUAL",
                evidence_json="{}",
                decided_by="buyer",
            ),
        ]
    )
    session.commit()

    counts = current_standard_member_counts(session, [item.id])

    assert counts == {item.id: 1}
