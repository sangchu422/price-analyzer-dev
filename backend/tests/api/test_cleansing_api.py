from datetime import datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cleansing.models import CleanDecision, CleanStatus
from app.documents.models import SourceDocument, SourceVariant
from app.quotes.models import RawQuoteItem


def _seed_review_item(
    session: Session,
    *,
    decided_at: datetime | None = None,
) -> RawQuoteItem:
    document = SourceDocument(logical_name="라인A/견적")
    variant = SourceVariant(
        document=document,
        path="라인A/견적_보안해제.xlsx",
        sha256="a" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    raw = RawQuoteItem(
        source_variant=variant,
        source_sheet="단위설비1",
        source_row=12,
        source_cells="A12:G12",
        item_name_raw=" BEARING ",
        spec_raw=" 6204 ",
        unit_raw=" ea ",
        quantity_raw="2",
        unit_price_raw="1000",
        amount_raw="9000",
        maker_raw=" ACME ",
        parser_name="quote-reader",
        parser_version="reader-v1",
        parse_warnings_json='["HEADER_GUESS"]',
    )
    decision = CleanDecision(
        raw_item=raw,
        status=CleanStatus.REVIEW_REQUIRED,
        reason_code="AMOUNT_MISMATCH",
        reason_detail="2*1000!=9000",
        item_name_norm="BEARING",
        spec_norm="6204",
        unit_norm="EA",
        maker_norm="ACME",
        quantity=Decimal("2"),
        unit_price=Decimal("1000"),
        amount=Decimal("9000"),
        rule_version="clean-v1",
        decided_at=decided_at,
    )
    session.add(document)
    session.add(decision)
    session.commit()
    return raw


def test_review_queue_returns_current_decision_and_exact_provenance(
    client: TestClient,
    api_session: Session,
) -> None:
    raw = _seed_review_item(api_session)

    response = client.get("/api/cleansing/review-queue")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    row = payload["items"][0]
    assert row["raw_item_id"] == raw.id
    assert row["raw"]["item_name"] == " BEARING "
    assert row["normalized"]["item_name"] == "BEARING"
    assert row["normalized"]["unit_price"] == "1000"
    assert row["reason_code"] == "AMOUNT_MISMATCH"
    assert row["decision"]["id"] > 0
    assert row["decision"]["rule_version"] == "clean-v1"
    assert row["decision"]["decided_by"] == "SYSTEM"
    assert row["source"] == {
        "document_id": raw.source_variant.document.id,
        "logical_name": "라인A/견적",
        "variant_id": raw.source_variant.id,
        "path": "라인A/견적_보안해제.xlsx",
        "sha256": "a" * 64,
        "security_state": "UNLOCKED",
        "selected_for_parsing_at_ingest": True,
        "sheet": "단위설비1",
        "page": None,
        "row": 12,
        "cells": "A12:G12",
        "parser_name": "quote-reader",
        "parser_version": "reader-v1",
        "parser_warnings": ["HEADER_GUESS"],
    }


def test_review_queue_uses_latest_id_not_decided_time_and_filters(
    client: TestClient,
    api_session: Session,
) -> None:
    first = _seed_review_item(
        api_session,
        decided_at=datetime(2099, 1, 1),
    )
    api_session.add(
        CleanDecision(
            raw_item=first,
            status=CleanStatus.INCLUDED,
            reason_code="MANUAL_REVIEW",
            item_name_norm="BEARING",
            spec_norm="6204",
            unit_norm="EA",
            unit_price=Decimal("1000"),
            rule_version="manual-v1",
            decided_by="reviewer",
            decided_at=datetime(2000, 1, 1),
        )
    )
    second = _seed_distinct_review(api_session)
    api_session.commit()

    response = client.get(
        "/api/cleansing/review-queue",
        params={"reason_code": "COLUMN_SHIFT_SUSPECTED", "limit": 1},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [row["raw_item_id"] for row in response.json()["items"]] == [
        second.id
    ]


def _seed_distinct_review(session: Session) -> RawQuoteItem:
    document = SourceDocument(logical_name="라인B/견적")
    variant = SourceVariant(
        document=document,
        path="라인B/견적.xlsx",
        sha256="b" * 64,
        extension=".xlsx",
        security_state="UNKNOWN",
        selected_for_parsing_at_ingest=True,
    )
    raw = RawQuoteItem(
        source_variant=variant,
        source_sheet="내역",
        source_row=3,
        source_cells="A3:C3",
        item_name_raw="1234",
        unit_price_raw="500",
        parser_name="quote-reader",
        parser_version="reader-v1",
    )
    session.add(
        CleanDecision(
            raw_item=raw,
            status=CleanStatus.REVIEW_REQUIRED,
            reason_code="COLUMN_SHIFT_SUSPECTED",
            item_name_norm="1234",
            unit_price=Decimal("500"),
            rule_version="clean-v1",
        )
    )
    session.flush()
    return raw


def test_manual_decision_appends_and_preserves_baseline_values(
    client: TestClient,
    api_session: Session,
) -> None:
    raw = _seed_review_item(api_session)
    original = raw.decisions[0]

    response = client.post(
        f"/api/cleansing/{raw.id}/decisions",
        json={
            "status": "INCLUDED",
            "reason_code": "MANUAL_REVIEW",
            "reason_detail": "원본 확인",
            "decided_by": "sangwoo",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["id"] > original.id
    assert payload["status"] == "INCLUDED"
    assert payload["decided_by"] == "sangwoo"
    decisions = api_session.scalars(
        select(CleanDecision)
        .where(CleanDecision.raw_item_id == raw.id)
        .order_by(CleanDecision.id)
    ).all()
    assert len(decisions) == 2
    assert decisions[0].status is CleanStatus.REVIEW_REQUIRED
    assert decisions[1].item_name_norm == "BEARING"
    assert decisions[1].quantity == Decimal("2")
    assert decisions[1].unit_price == Decimal("1000")
    assert decisions[1].amount == Decimal("9000")
    assert decisions[1].rule_version == "manual-v1"


def test_manual_include_recovers_values_from_latest_complete_history(
    client: TestClient,
    api_session: Session,
) -> None:
    raw = _seed_review_item(api_session)
    api_session.add(
        CleanDecision(
            raw_item=raw,
            status=CleanStatus.EXCLUDED,
            reason_code="LEGACY_MANUAL",
            rule_version="manual-v0",
            decided_by="legacy-reviewer",
        )
    )
    api_session.commit()

    response = client.post(
        f"/api/cleansing/{raw.id}/decisions",
        json={
            "status": "INCLUDED",
            "reason_code": "MANUAL_REVIEW",
            "reason_detail": "원본 재확인",
            "decided_by": "reviewer",
        },
    )

    assert response.status_code == 201
    latest = api_session.scalar(
        select(CleanDecision)
        .where(CleanDecision.raw_item_id == raw.id)
        .order_by(CleanDecision.id.desc())
    )
    assert latest.item_name_norm == "BEARING"
    assert latest.spec_norm == "6204"
    assert latest.unit_price == Decimal("1000")
    assert latest.amount == Decimal("9000")


def test_manual_decision_rejects_impersonation_and_invalid_contract(
    client: TestClient,
    api_session: Session,
) -> None:
    raw = _seed_review_item(api_session)
    invalid_bodies = [
        {
            "status": "INCLUDED",
            "reason_code": "MANUAL_REVIEW",
            "reason_detail": "ok",
            "decided_by": " SYSTEM ",
        },
        {
            "status": "REVIEW_REQUIRED",
            "reason_code": "MANUAL_REVIEW",
            "reason_detail": "ok",
            "decided_by": "reviewer",
        },
        {
            "status": "EXCLUDED",
            "reason_code": "VALID",
            "reason_detail": "ok",
            "decided_by": "reviewer",
        },
        {
            "status": "EXCLUDED",
            "reason_code": "MANUAL_REVIEW",
            "reason_detail": "x" * 2001,
            "decided_by": "reviewer",
        },
    ]

    for body in invalid_bodies:
        assert client.post(
            f"/api/cleansing/{raw.id}/decisions", json=body
        ).status_code == 422

    assert api_session.scalar(select(func.count(CleanDecision.id))) == 1


def test_manual_decision_returns_404_without_history(
    client: TestClient,
    api_session: Session,
) -> None:
    response = client.post(
        "/api/cleansing/999999/decisions",
        json={
            "status": "EXCLUDED",
            "reason_code": "MANUAL_REVIEW",
            "reason_detail": "not applicable",
            "decided_by": "reviewer",
        },
    )

    assert response.status_code == 404
    assert api_session.scalar(select(func.count(CleanDecision.id))) == 0
