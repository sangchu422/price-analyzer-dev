import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Lock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.cleansing import DecisionRequest, append_manual_decision
from app.cleansing.models import CleanDecision, CleanStatus
from app.db.base import Base
from app.db.sqlite import configure_sqlite
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
    assert payload["remaining"] == 1
    assert payload["next_cursor"] is None
    assert payload["available_reason_codes"] == ["AMOUNT_MISMATCH"]
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
    assert response.json()["remaining"] == 1
    assert [row["raw_item_id"] for row in response.json()["items"]] == [
        second.id
    ]


def test_review_queue_cursor_does_not_skip_after_prior_page_is_resolved(
    client: TestClient,
    api_session: Session,
) -> None:
    first = _seed_review_item(api_session)
    second = _seed_distinct_review(api_session)
    third = _seed_third_review(api_session)
    api_session.commit()
    first_id = first.id
    second_id = second.id
    third_id = third.id

    page = client.get("/api/cleansing/review-queue", params={"limit": 1})
    assert page.status_code == 200
    assert [item["raw_item_id"] for item in page.json()["items"]] == [first_id]
    cursor = page.json()["next_cursor"]
    assert cursor == first_id
    expected_id = page.json()["items"][0]["decision"]["id"]
    resolved = client.post(
        f"/api/cleansing/{first_id}/decisions",
        json={
            "status": "INCLUDED",
            "reason_code": "MANUAL_REVIEW",
            "reason_detail": "resolved",
            "decided_by": "reviewer",
            "expected_current_decision_id": expected_id,
        },
    )
    assert resolved.status_code == 201

    next_page = client.get(
        "/api/cleansing/review-queue",
        params={"limit": 1, "after_id": cursor},
    )

    assert next_page.status_code == 200
    assert [item["raw_item_id"] for item in next_page.json()["items"]] == [
        second_id
    ]
    assert next_page.json()["next_cursor"] == second_id
    assert third_id > second_id
    assert client.get(
        "/api/cleansing/review-queue",
        params={"offset": 1},
    ).status_code == 422


def _seed_third_review(session: Session) -> RawQuoteItem:
    document = SourceDocument(logical_name="라인C/견적")
    variant = SourceVariant(
        document=document,
        path="라인C/견적.xlsx",
        sha256="c" * 64,
        extension=".xlsx",
        security_state="UNKNOWN",
        selected_for_parsing_at_ingest=True,
    )
    raw = RawQuoteItem(
        source_variant=variant,
        source_sheet="내역",
        source_row=4,
        source_cells="A4:C4",
        item_name_raw="5678",
        unit_price_raw="700",
        parser_name="quote-reader",
        parser_version="reader-v1",
    )
    session.add(
        CleanDecision(
            raw_item=raw,
            status=CleanStatus.REVIEW_REQUIRED,
            reason_code="COLUMN_SHIFT_SUSPECTED",
            item_name_norm="5678",
            unit_price=Decimal("700"),
            rule_version="clean-v1",
        )
    )
    session.flush()
    return raw


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


def _seed_review_batch(
    session: Session,
    *,
    count: int = 55,
) -> list[RawQuoteItem]:
    document = SourceDocument(logical_name="대량/검색문서")
    variant = SourceVariant(
        document=document,
        path="대량/검색문서_보안해제.xlsx",
        sha256="d" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    rows: list[RawQuoteItem] = []
    for index in range(count):
        is_last = index == count - 1
        raw = RawQuoteItem(
            source_variant=variant,
            source_sheet="내역",
            source_row=index + 1,
            source_cells=f"A{index + 1}:C{index + 1}",
            item_name_raw=(
                "PAGE2%_Needle" if is_last else f"GENERIC-{index:03d}"
            ),
            spec_raw="RAW-SPECIAL" if index == 1 else "STANDARD",
            parser_name="quote-reader",
            parser_version="reader-v1",
        )
        session.add(
            CleanDecision(
                raw_item=raw,
                status=CleanStatus.REVIEW_REQUIRED,
                reason_code=(
                    "RARE_PAGE2_REASON" if is_last else "COMMON_REASON"
                ),
                item_name_norm=(
                    "PAGE2%_NEEDLE" if is_last else f"GENERIC-{index:03d}"
                ),
                spec_norm="NORM-SPECIAL" if index == 2 else "STANDARD",
                rule_version="clean-v1",
            )
        )
        rows.append(raw)
    session.commit()
    return rows


def test_review_queue_searches_all_pages_and_escapes_sql_wildcards(
    client: TestClient,
    api_session: Session,
) -> None:
    rows = _seed_review_batch(api_session)

    ordinary_page = client.get(
        "/api/cleansing/review-queue",
        params={"limit": 50},
    )
    assert ordinary_page.status_code == 200
    assert rows[-1].id not in {
        item["raw_item_id"] for item in ordinary_page.json()["items"]
    }

    search = client.get(
        "/api/cleansing/review-queue",
        params={"search": "  page2%_needle  ", "limit": 50},
    )

    assert search.status_code == 200
    assert search.json()["remaining"] == 1
    assert [item["raw_item_id"] for item in search.json()["items"]] == [
        rows[-1].id
    ]


@pytest.mark.parametrize(
    ("search", "expected_index"),
    [
        ("raw-special", 1),
        ("norm-special", 2),
        ("대량/검색문서", 0),
        ("검색문서_보안해제.xlsx", 0),
    ],
)
def test_review_queue_searches_raw_normalized_and_source_fields(
    client: TestClient,
    api_session: Session,
    search: str,
    expected_index: int,
) -> None:
    rows = _seed_review_batch(api_session, count=4)

    response = client.get(
        "/api/cleansing/review-queue",
        params={"search": search},
    )

    assert response.status_code == 200
    ids = [item["raw_item_id"] for item in response.json()["items"]]
    if search in {"대량/검색문서", "검색문서_보안해제.xlsx"}:
        assert ids == [row.id for row in rows]
    else:
        assert ids == [rows[expected_index].id]


def test_review_queue_reason_facets_cover_unloaded_search_result(
    client: TestClient,
    api_session: Session,
) -> None:
    rows = _seed_review_batch(api_session)

    first_page = client.get(
        "/api/cleansing/review-queue",
        params={"limit": 50},
    )
    rare_only = client.get(
        "/api/cleansing/review-queue",
        params={"reason_code": "RARE_PAGE2_REASON"},
    )

    assert first_page.status_code == 200
    assert first_page.json()["available_reason_codes"] == [
        "COMMON_REASON",
        "RARE_PAGE2_REASON",
    ]
    assert rare_only.status_code == 200
    assert rare_only.json()["available_reason_codes"] == [
        "COMMON_REASON",
        "RARE_PAGE2_REASON",
    ]
    assert [item["raw_item_id"] for item in rare_only.json()["items"]] == [
        rows[-1].id
    ]


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
            "expected_current_decision_id": original.id,
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
            "expected_current_decision_id": raw.decisions[-1].id,
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
        {
            "status": "EXCLUDED",
            "reason_code": "MANUAL_REVIEW",
            "reason_detail": "ok",
            "decided_by": "reviewer",
            "expected_current_decision_id": str(raw.decisions[0].id),
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
            "expected_current_decision_id": 1,
        },
    )

    assert response.status_code == 404
    assert api_session.scalar(select(func.count(CleanDecision.id))) == 0


def test_manual_decision_rejects_stale_snapshot_without_extra_history(
    client: TestClient,
    api_session: Session,
) -> None:
    raw = _seed_review_item(api_session)
    snapshot_id = raw.decisions[0].id
    body = {
        "status": "INCLUDED",
        "reason_code": "MANUAL_REVIEW",
        "reason_detail": "reviewed",
        "decided_by": "reviewer",
        "expected_current_decision_id": snapshot_id,
    }

    first = client.post(f"/api/cleansing/{raw.id}/decisions", json=body)
    second = client.post(f"/api/cleansing/{raw.id}/decisions", json=body)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"] == {
        "error_code": "STALE_DECISION",
        "message": "cleansing decision changed; refresh and retry",
        "current_decision_id": first.json()["id"],
    }
    assert api_session.scalar(select(func.count(CleanDecision.id))) == 2


def test_immediate_execution_option_emits_begin_immediate(
    tmp_path: Path,
) -> None:
    database = tmp_path / "immediate.sqlite3"
    engine = configure_sqlite(
        create_engine(f"sqlite:///{database.as_posix()}")
    )
    statements: list[str] = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda connection, cursor, statement, parameters, context, many: (
            statements.append(statement)
        ),
    )
    with Session(engine) as session:
        session.connection(
            execution_options={"sqlite_begin_mode": "IMMEDIATE"}
        )
        session.rollback()
    with Session(engine) as read_session:
        read_session.execute(select(1))
        read_session.rollback()

    assert statements[0] == "BEGIN IMMEDIATE"
    assert statements[-2] == "BEGIN DEFERRED"


def test_sqlite_begin_mode_rejects_unallowlisted_sql(
    tmp_path: Path,
) -> None:
    engine = configure_sqlite(
        create_engine(f"sqlite:///{(tmp_path / 'mode.sqlite3').as_posix()}")
    )

    with Session(engine) as session:
        with pytest.raises(ValueError, match="sqlite_begin_mode"):
            session.connection(
                execution_options={
                    "sqlite_begin_mode": "IMMEDIATE; DROP TABLE x"
                }
            )


def test_concurrent_manual_decisions_serialize_to_success_and_stale(
    tmp_path: Path,
) -> None:
    database = tmp_path / "concurrent.sqlite3"
    engine = configure_sqlite(
        create_engine(
            f"sqlite:///{database.as_posix()}",
            connect_args={"check_same_thread": False},
        )
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup:
        raw = _seed_review_item(setup)
        raw_id = raw.id
        expected_id = raw.decisions[0].id

    start = Barrier(2)
    first_begin = Lock()
    begin_was_held = False

    def hold_first_writer(
        connection,
        cursor,
        statement,
        parameters,
        context,
        many,
    ) -> None:
        nonlocal begin_was_held
        if statement != "BEGIN IMMEDIATE":
            return
        with first_begin:
            if begin_was_held:
                return
            begin_was_held = True
        time.sleep(0.2)

    event.listen(engine, "after_cursor_execute", hold_first_writer)

    def submit(actor: str) -> tuple[int, int | None]:
        with factory() as session:
            start.wait(timeout=5)
            request = DecisionRequest(
                status="INCLUDED",
                reason_code="MANUAL_REVIEW",
                reason_detail="concurrent review",
                decided_by=actor,
                expected_current_decision_id=expected_id,
            )
            try:
                response = append_manual_decision(raw_id, request, session)
            except HTTPException as exc:
                current_id = (
                    exc.detail.get("current_decision_id")
                    if isinstance(exc.detail, dict)
                    else None
                )
                return exc.status_code, current_id
            return 201, response["id"]

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(submit, "reviewer-a"),
                executor.submit(submit, "reviewer-b"),
            ]
            results = sorted(
                future.result(timeout=10)
                for future in futures
            )
    finally:
        event.remove(engine, "after_cursor_execute", hold_first_writer)

    assert [status for status, _ in results] == [201, 409]
    assert begin_was_held
    with factory() as observer:
        history = observer.scalars(
            select(CleanDecision)
            .where(CleanDecision.raw_item_id == raw_id)
            .order_by(CleanDecision.id)
        ).all()
        assert len(history) == 2
        assert history[-1].decided_by in {"reviewer-a", "reviewer-b"}
