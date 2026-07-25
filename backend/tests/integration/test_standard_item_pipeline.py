from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.analysis.service import analyze_document
from app.catalog.cli import (
    build_catalog_embedding_index,
    report_standard_price_drafts,
    seed_exact_catalog,
)
from app.catalog.models import (
    ItemMembershipDecision,
    MembershipStatus,
    StandardItem,
    StandardPriceVersion,
)
from app.catalog.service import append_membership_decision, candidate_matches
from app.cli import main
from app.cleansing.models import CleanDecision, CleanStatus
from app.db.base import Base
from app.db.sqlite import configure_sqlite
from app.documents.models import SourceDocument
from app.embeddings.index import load_index
from app.ingestion.corpus import ingest_corpus
from app.pricing.service import (
    approve_standard_price,
    calculate_standard_price,
)
from app.quotes.models import RawQuoteItem


def _write_quote(path: Path, rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "견적내역"
    sheet.append(["품명", "사양", "단위", "수량", "단가", "금액"])
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def _session() -> Session:
    engine = configure_sqlite(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def _raw_by_name(session: Session, name: str) -> RawQuoteItem:
    return session.scalar(
        select(RawQuoteItem)
        .join(CleanDecision)
        .where(CleanDecision.item_name_norm == name)
        .order_by(RawQuoteItem.id.desc())
        .limit(1)
    )


def test_source_to_standard_price_to_analysis_pipeline(
    tmp_path: Path,
) -> None:
    history = tmp_path / "history"
    _write_quote(
        history / "bearing-a.xlsx",
        [["BEARING", "6204 ZZ", "EA", 1, 100, 100]],
    )
    _write_quote(
        history / "bearing-b.xlsx",
        [["BEARING", "6204 ZZ", "EA", 1, 120, 120]],
    )

    with _session() as session:
        first_ingest = ingest_corpus(session, history)
        first_seed = seed_exact_catalog(session)
        session.commit()
        second_seed = seed_exact_catalog(session)
        session.commit()

        assert first_ingest.latest_status_counts[CleanStatus.INCLUDED] == 2
        assert first_seed.exact_groups_created == 1
        assert first_seed.memberships_created == 2
        assert second_seed.exact_groups_created == 0
        assert second_seed.memberships_created == 0
        assert session.scalar(select(func.count(StandardItem.id))) == 1
        assert (
            session.scalar(select(func.count(ItemMembershipDecision.id))) == 2
        )

        item = session.scalar(select(StandardItem))
        first_drafts = report_standard_price_drafts(session)
        second_drafts = report_standard_price_drafts(session)
        assert first_drafts == second_drafts
        assert first_drafts.drafts_available == 1
        assert session.scalar(select(func.count(StandardPriceVersion.id))) == 0

        draft = calculate_standard_price(session, item.id)
        approved = approve_standard_price(
            session,
            item.id,
            expected_fingerprint=draft.fingerprint,
            expected_current_version_id=None,
            approved_by="buyer-1",
        )
        session.commit()
        assert approved.observation_count == 2

        new_quotes = tmp_path / "new-quotes"
        _write_quote(
            new_quotes / "comparison.xlsx",
            [
                ["BALL BEARING", "6204 ZZ", "EA", 1, 150, 150],
                ["BEARING", "6204 ZZ", "M", 1, 999, 999],
            ],
        )
        ingest_corpus(session, new_quotes)
        document = session.scalar(
            select(SourceDocument).where(
                SourceDocument.logical_name == "comparison"
            )
        )
        semantic = _raw_by_name(session, "BALL BEARING")
        unit_conflict = _raw_by_name(session, "BEARING")

        semantic_result = candidate_matches(session, semantic.id)
        unit_result = candidate_matches(session, unit_conflict.id)
        assert semantic_result.match_status == "CANDIDATE"
        assert semantic_result.current_membership_decision is None
        assert unit_result.match_status == "NO_MATCH"
        assert unit_result.current_membership_decision is None

        append_membership_decision(
            session,
            raw_item_id=semantic.id,
            standard_item_id=item.id,
            status=MembershipStatus.MATCHED,
            expected_current_decision_id=None,
            candidate_score=semantic_result.candidates[0].score.final_score,
            method="MANUAL_CANDIDATE",
            evidence={"candidate_only": True},
            decided_by="buyer-1",
            reason_detail="설비구매팀 검토 후 승인",
        )
        session.commit()

        analysis = analyze_document(session, document.id)
        semantic_line = next(
            line for line in analysis.lines if line.raw_item_id == semantic.id
        )
        conflict_line = next(
            line
            for line in analysis.lines
            if line.raw_item_id == unit_conflict.id
        )
        assert semantic_line.standard_price_version_id == approved.id
        assert semantic_line.assessment == "HIGH"
        assert conflict_line.match_status == "NO_MATCH"
        assert conflict_line.reference_price is None

        membership_count = session.scalar(
            select(func.count(ItemMembershipDecision.id))
        )
        price_version_count = session.scalar(
            select(func.count(StandardPriceVersion.id))
        )
        repeat_ingest = ingest_corpus(session, new_quotes)
        repeat_seed = seed_exact_catalog(session)
        repeat_drafts = report_standard_price_drafts(session)
        assert repeat_ingest.raw_items_created == 0
        assert repeat_seed.memberships_created == 0
        assert repeat_drafts.drafts_available == 1
        assert session.scalar(
            select(func.count(ItemMembershipDecision.id))
        ) == membership_count
        assert session.scalar(
            select(func.count(StandardPriceVersion.id))
        ) == price_version_count


def test_seed_uses_only_latest_included_and_never_overrides_a_decision(
    tmp_path: Path,
) -> None:
    root = tmp_path / "quotes"
    for index in range(4):
        _write_quote(
            root / f"sensor-{index}.xlsx",
            [["SENSOR", "PX-1", "EA", 1, 100 + index, 100 + index]],
        )

    with _session() as session:
        ingest_corpus(session, root)
        rows = list(session.scalars(select(RawQuoteItem).order_by(RawQuoteItem.id)))
        session.add(
            CleanDecision(
                raw_item=rows[0],
                status=CleanStatus.EXCLUDED,
                reason_code="MANUAL_EXCLUSION",
                rule_version="manual-v1",
            )
        )
        session.add(
            ItemMembershipDecision(
                raw_item=rows[1],
                standard_item_id=None,
                status=MembershipStatus.REJECTED,
                method="MANUAL_NO_MATCH",
                evidence_json='{"reason":"reviewed"}',
                decided_by="buyer-1",
            )
        )
        session.flush()

        report = seed_exact_catalog(session)
        session.commit()

        assert report.included_rows_eligible == 3
        assert report.exact_groups_created == 0
        assert report.memberships_created == 0
        assert report.rows_held_by_prior_decision == 1
        assert session.scalar(select(func.count(StandardItem.id))) == 0


def test_seed_holds_exact_subgroups_when_units_conflict(
    tmp_path: Path,
) -> None:
    root = tmp_path / "quotes"
    for index, unit in enumerate(("EA", "EA", "M", "M"), start=1):
        _write_quote(
            root / f"bearing-{index}.xlsx",
            [["BEARING", "6204 ZZ", unit, 1, 100 + index, 100 + index]],
        )

    with _session() as session:
        ingest_corpus(session, root)

        report = seed_exact_catalog(session)

        assert report.exact_groups_eligible == 2
        assert report.exact_groups_created == 0
        assert report.memberships_created == 0
        assert report.conflicts_held_for_review == 4
        assert session.scalar(select(func.count(StandardItem.id))) == 0


def test_mock_index_is_labeled_and_repeatable_while_drafts_are_read_only(
    tmp_path: Path,
) -> None:
    root = tmp_path / "quotes"
    _write_quote(
        root / "a.xlsx",
        [["SERVO MOTOR", "SGMAH-04AAA61", "EA", 1, 100, 100]],
    )
    _write_quote(
        root / "b.xlsx",
        [["SERVO MOTOR", "SGMAH-04AAA61", "EA", 1, 120, 120]],
    )
    index_path = tmp_path / "mock-index.npz"

    with _session() as session:
        ingest_corpus(session, root)
        seed_exact_catalog(session)
        session.commit()
        first = build_catalog_embedding_index(
            session,
            index_path=index_path,
            mock=True,
        )
        second = build_catalog_embedding_index(
            session,
            index_path=index_path,
            mock=True,
        )
        before_versions = session.scalar(
            select(func.count(StandardPriceVersion.id))
        )
        draft_report = report_standard_price_drafts(session)
        after_versions = session.scalar(
            select(func.count(StandardPriceVersion.id))
        )

        assert first.status == second.status == "MOCK_ONLY"
        assert first.model == second.model == "local-mock-v1"
        assert first.item_count == second.item_count == 1
        assert first.catalog_fingerprint == second.catalog_fingerprint
        assert draft_report.drafts_available == 1
        assert before_versions == after_versions == 0
        loaded = load_index(
            index_path,
            expected_model="local-mock-v1",
            expected_catalog_fingerprint=first.catalog_fingerprint,
        )
        assert loaded.metadata.item_count == 1


def test_catalog_cli_disabled_embedding_never_creates_an_index(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "catalog.sqlite3"
    index_path = tmp_path / "disabled.npz"

    exit_code = main(
        [
            "embedding-index",
            "--database-file",
            str(database),
            "--index-file",
            str(index_path),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["status"] == "DISABLED"
    assert payload["network_called"] is False
    assert not index_path.exists()


def test_embedding_cli_rejects_report_that_would_overwrite_index(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "catalog.sqlite3"
    shared_output = tmp_path / "shared.npz"

    exit_code = main(
        [
            "embedding-index",
            "--database-file",
            str(database),
            "--index-file",
            str(shared_output),
            "--report",
            str(shared_output),
            "--mock",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["error_code"] == "UNSAFE_OUTPUT_PATH"
    assert not shared_output.exists()


def test_catalog_cli_seed_and_drafts_are_idempotent(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "quotes"
    database = tmp_path / "catalog.sqlite3"
    report_path = tmp_path / "draft-summary.json"
    _write_quote(
        root / "a.xlsx",
        [["BEARING", "6204", "EA", 1, 100, 100]],
    )
    _write_quote(
        root / "b.xlsx",
        [["BEARING", "6204", "EA", 1, 120, 120]],
    )
    assert main(
        [
            "ingest",
            "--quote-root",
            str(root),
            "--database-file",
            str(database),
            "--json",
        ]
    ) == 0
    capsys.readouterr()

    first = main(
        ["catalog-seed", "--database-file", str(database), "--json"]
    )
    first_payload = json.loads(capsys.readouterr().out)
    second = main(
        ["catalog-seed", "--database-file", str(database), "--json"]
    )
    second_payload = json.loads(capsys.readouterr().out)
    drafts = main(
        [
            "standard-price-drafts",
            "--database-file",
            str(database),
            "--report",
            str(report_path),
            "--json",
        ]
    )
    draft_payload = json.loads(capsys.readouterr().out)

    assert first == second == drafts == 0
    assert first_payload["memberships_created"] == 2
    assert second_payload["memberships_created"] == 0
    assert draft_payload["drafts_available"] == 1
    assert draft_payload["approved_versions_created"] == 0
    assert json.loads(report_path.read_text(encoding="utf-8")) == draft_payload
