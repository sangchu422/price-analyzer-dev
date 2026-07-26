from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path

from openpyxl import Workbook
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.analysis.service import analyze_document
from app.catalog.cli import (
    build_catalog_embedding_index,
    report_standard_price_drafts,
    seed_exact_catalog,
)
from app.core.config import settings
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
from app.standard_database.models import (
    QuoteDocumentPurpose,
    QuoteDocumentRole,
    StandardDatabaseBuildRun,
)


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


def _local_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    project = tmp_path / "project"
    quote_root = project / "quotes"
    local = project / "backend" / ".local"
    local.mkdir(parents=True)
    monkeypatch.setattr(settings, "project_root", project)
    monkeypatch.setattr(settings, "quote_folder", Path("quotes"))
    return quote_root, local


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, local = _local_project(tmp_path, monkeypatch)
    database = local / "catalog.sqlite3"
    index_path = local / "disabled.npz"

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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, local = _local_project(tmp_path, monkeypatch)
    database = local / "catalog.sqlite3"
    shared_output = local / "shared.npz"

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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, local = _local_project(tmp_path, monkeypatch)
    database = local / "catalog.sqlite3"
    report_path = local / "draft-summary.json"
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


def test_standard_db_build_cli_commits_once_and_reports_repeatable_result(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, local = _local_project(tmp_path, monkeypatch)
    database = local / "standards.sqlite3"
    first_report = local / "standard-build-first.json"
    second_report = local / "standard-build-second.json"
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
    first_exit = main(
        [
            "standard-db-build",
            "--database-file",
            str(database),
            "--report",
            str(first_report),
            "--actor",
            "buyer-automation",
            "--json",
        ]
    )
    first = json.loads(capsys.readouterr().out)
    second_exit = main(
        [
            "standard-db-build",
            "--database-file",
            str(database),
            "--report",
            str(second_report),
            "--actor",
            "buyer-automation",
            "--json",
        ]
    )
    second = json.loads(capsys.readouterr().out)

    assert first_exit == second_exit == 0
    assert first["status"] == second["status"] == "SUCCEEDED"
    assert first["rule_version"] == second["rule_version"]
    assert len(first["fingerprint"]) == 64
    assert first["created_standard_items"] == 1
    assert first["created_memberships"] == 2
    assert first["created_price_versions"] == 1
    assert first["groups"] == 1
    assert first["observations"] == 2
    assert first["single_observation_count"] == 0
    assert second["run_id"] == first["run_id"]
    assert second["reused_run_id"] == first["run_id"]
    assert second["created_standard_items"] == 0
    assert second["created_memberships"] == 0
    assert second["created_price_versions"] == 0
    assert json.loads(first_report.read_text(encoding="utf-8")) == first
    assert json.loads(second_report.read_text(encoding="utf-8")) == second
    with Session(create_engine(f"sqlite:///{database.as_posix()}")) as session:
        run = session.scalar(select(StandardDatabaseBuildRun))
        assert run is not None
        assert run.report_path == str(first_report.resolve())
        assert set(
            session.scalars(select(ItemMembershipDecision.decided_by))
        ) == {"buyer-automation"}
        assert set(
            session.scalars(select(StandardPriceVersion.approved_by))
        ) == {"buyer-automation"}
        assert session.scalar(select(func.count(QuoteDocumentRole.id))) == 2
        assert set(session.scalars(select(QuoteDocumentRole.purpose))) == {
            QuoteDocumentPurpose.HISTORICAL_REFERENCE
        }


def test_standard_db_bootstrap_preserves_incoming_and_classifies_only_unroled(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, local = _local_project(tmp_path, monkeypatch)
    database = local / "mixed-roles.sqlite3"
    report = local / "mixed-roles.json"
    _write_quote(
        root / "legacy.xlsx",
        [["BEARING", "6204", "EA", 1, 100, 100]],
    )
    _write_quote(
        root / "incoming.xlsx",
        [["SENSOR", "PX-1", "EA", 1, 999, 999]],
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
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    with Session(engine) as session:
        incoming = session.scalar(
            select(SourceDocument).where(
                SourceDocument.logical_name == "incoming"
            )
        )
        session.add(
            QuoteDocumentRole(
                document_id=incoming.id,
                purpose=QuoteDocumentPurpose.INCOMING_BID,
                decided_by="submission-api",
                reason_detail="incoming fixture",
            )
        )
        session.commit()
    engine.dispose()

    exit_code = main(
        [
            "standard-db-build",
            "--database-file",
            str(database),
            "--report",
            str(report),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["groups"] == 1
    assert payload["observations"] == 1
    with Session(create_engine(f"sqlite:///{database.as_posix()}")) as session:
        roles = {
            document.logical_name: role.purpose
            for document, role in session.execute(
                select(SourceDocument, QuoteDocumentRole).join(
                    QuoteDocumentRole,
                    QuoteDocumentRole.document_id == SourceDocument.id,
                )
            )
        }
        assert roles == {
            "incoming": QuoteDocumentPurpose.INCOMING_BID,
            "legacy": QuoteDocumentPurpose.HISTORICAL_REFERENCE,
        }


def test_standard_db_build_cli_rolls_back_and_sanitizes_unexpected_error(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, local = _local_project(tmp_path, monkeypatch)
    database = local / "unexpected.sqlite3"
    report = local / "unexpected.json"
    _write_quote(
        root / "legacy.xlsx",
        [["BEARING", "6204", "EA", 1, 100, 100]],
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

    def fail_after_bootstrap(*args, **kwargs):
        raise ValueError(f"private failure at {tmp_path}")

    monkeypatch.setattr("app.cli.build_standard_database", fail_after_bootstrap)
    exit_code = main(
        [
            "standard-db-build",
            "--database-file",
            str(database),
            "--report",
            str(report),
            "--json",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert exit_code == 2
    assert payload == {
        "error_code": "STANDARD_DB_BUILD_ERROR",
        "detail": "standard database build failed",
    }
    assert str(tmp_path) not in output
    assert json.loads(report.read_text(encoding="utf-8")) == payload
    with Session(create_engine(f"sqlite:///{database.as_posix()}")) as session:
        assert session.scalar(select(func.count(QuoteDocumentRole.id))) == 1
        assert session.scalar(
            select(func.count(StandardDatabaseBuildRun.id))
        ) == 0


def test_standard_db_build_cli_writes_database_failure_report(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, local = _local_project(tmp_path, monkeypatch)
    database = local / "database-error.sqlite3"
    report = local / "database-error.json"

    def fail_with_database_error(*args, **kwargs):
        raise OperationalError(
            "SELECT",
            {},
            Exception("database locked"),
        )

    monkeypatch.setattr(
        "app.cli.build_standard_database",
        fail_with_database_error,
    )
    exit_code = main(
        [
            "standard-db-build",
            "--database-file",
            str(database),
            "--report",
            str(report),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload == {
        "error_code": "DATABASE_LOCKED",
        "detail": "database is locked by another process",
    }
    assert json.loads(report.read_text(encoding="utf-8")) == payload


def test_catalog_cli_rejects_original_and_output_alias_without_modification(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, local = _local_project(tmp_path, monkeypatch)
    original = root / "quote.xlsx"
    _write_quote(
        original,
        [
            ["BEARING", "6204", "EA", 1, 100, 100],
            ["BEARING", "6204", "EA", 1, 120, 120],
        ],
    )
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    database = local / "catalog.sqlite3"
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

    direct = main(
        [
            "catalog-seed",
            "--database-file",
            str(database),
            "--report",
            str(original),
            "--json",
        ]
    )
    direct_payload = json.loads(capsys.readouterr().out)
    alias = local / "original-alias.xlsx"
    try:
        os.link(original, alias)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    aliased = main(
        [
            "catalog-seed",
            "--database-file",
            str(database),
            "--report",
            str(alias),
            "--json",
        ]
    )
    alias_payload = json.loads(capsys.readouterr().out)

    assert direct == aliased == 2
    assert direct_payload["error_code"] == "UNSAFE_OUTPUT_PATH"
    assert alias_payload["error_code"] == "UNSAFE_OUTPUT_PATH"
    assert hashlib.sha256(original.read_bytes()).hexdigest() == before


def test_catalog_cli_rejects_directory_and_symlink_outputs(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, local = _local_project(tmp_path, monkeypatch)
    database = local / "catalog.sqlite3"
    directory = local / "report-directory"
    directory.mkdir()
    directory_exit = main(
        [
            "standard-price-drafts",
            "--database-file",
            str(database),
            "--report",
            str(directory),
            "--json",
        ]
    )
    directory_payload = json.loads(capsys.readouterr().out)
    assert directory_exit == 2
    assert directory_payload["error_code"] == "UNSAFE_OUTPUT_PATH"

    target = local / "real-report.json"
    target.write_text("sentinel", encoding="utf-8")
    link = local / "linked-report.json"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")
    link_exit = main(
        [
            "standard-price-drafts",
            "--database-file",
            str(database),
            "--report",
            str(link),
            "--json",
        ]
    )
    link_payload = json.loads(capsys.readouterr().out)
    assert link_exit == 2
    assert link_payload["error_code"] == "UNSAFE_OUTPUT_PATH"
    assert target.read_text(encoding="utf-8") == "sentinel"


def test_catalog_cli_rejects_reparse_and_arbitrary_database_files(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, local = _local_project(tmp_path, monkeypatch)
    report = local / "report.json"
    monkeypatch.setattr(
        "app.cli._is_reparse_point",
        lambda path: path == local,
    )
    reparse_exit = main(
        [
            "standard-price-drafts",
            "--database-file",
            str(local / "catalog.sqlite3"),
            "--report",
            str(report),
            "--json",
        ]
    )
    reparse_payload = json.loads(capsys.readouterr().out)
    assert reparse_exit == 2
    assert reparse_payload["error_code"] == "UNSAFE_OUTPUT_PATH"

    arbitrary = local / "not-a-database.xlsx"
    arbitrary.write_bytes(b"do not modify")
    before = arbitrary.read_bytes()
    database_exit = main(
        [
            "catalog-seed",
            "--database-file",
            str(arbitrary),
            "--json",
        ]
    )
    database_payload = json.loads(capsys.readouterr().out)
    assert database_exit == 2
    assert database_payload["error_code"] == "UNSAFE_OUTPUT_PATH"
    assert arbitrary.read_bytes() == before


def test_catalog_seed_report_failure_rolls_back_memberships(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, local = _local_project(tmp_path, monkeypatch)
    database = local / "catalog.sqlite3"
    report = local / "seed.json"
    for name in ("a.xlsx", "b.xlsx"):
        _write_quote(
            root / name,
            [["BEARING", "6204", "EA", 1, 100, 100]],
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

    real_replace = os.replace

    def fail_publication(source, target):
        if (
            Path(source).suffix == ".staged"
            and Path(target) == report
        ):
            raise OSError("simulated report publication failure")
        return real_replace(source, target)

    monkeypatch.setattr("app.cli.os.replace", fail_publication)
    exit_code = main(
        [
            "catalog-seed",
            "--database-file",
            str(database),
            "--report",
            str(report),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    with Session(create_engine(f"sqlite:///{database.as_posix()}")) as session:
        assert session.scalar(select(func.count(StandardItem.id))) == 0
        assert (
            session.scalar(select(func.count(ItemMembershipDecision.id))) == 0
        )
    assert exit_code == 2
    assert payload["error_code"] == "REPORT_WRITE_ERROR"
    assert not report.exists()


def test_catalog_seed_commit_failure_restores_previous_report(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, local = _local_project(tmp_path, monkeypatch)
    database = local / "catalog.sqlite3"
    report = local / "seed.json"
    previous = b'{"previous":true}\n'
    report.write_bytes(previous)
    for name in ("a.xlsx", "b.xlsx"):
        _write_quote(
            root / name,
            [["BEARING", "6204", "EA", 1, 100, 100]],
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

    def fail_commit(self):
        raise OperationalError("COMMIT", {}, Exception("simulated"))

    monkeypatch.setattr(Session, "commit", fail_commit)
    exit_code = main(
        [
            "catalog-seed",
            "--database-file",
            str(database),
            "--report",
            str(report),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["error_code"] == "DATABASE_UNAVAILABLE"
    assert report.read_bytes() == previous
    with Session(create_engine(f"sqlite:///{database.as_posix()}")) as session:
        assert session.scalar(select(func.count(StandardItem.id))) == 0


def test_catalog_cli_write_failures_are_structured(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, local = _local_project(tmp_path, monkeypatch)
    database = local / "catalog.sqlite3"

    def fail_index(*args, **kwargs):
        raise OSError("simulated index failure")

    monkeypatch.setattr("app.cli.build_catalog_embedding_index", fail_index)
    index_exit = main(
        [
            "embedding-index",
            "--database-file",
            str(database),
            "--index-file",
            str(local / "items.npz"),
            "--mock",
            "--json",
        ]
    )
    index_payload = json.loads(capsys.readouterr().out)
    assert index_exit == 2
    assert index_payload["error_code"] == "INDEX_WRITE_ERROR"

    def fail_report(*args, **kwargs):
        raise OSError("simulated report failure")

    monkeypatch.setattr("app.cli._stage_catalog_report", fail_report)
    draft_exit = main(
        [
            "standard-price-drafts",
            "--database-file",
            str(database),
            "--report",
            str(local / "drafts.json"),
            "--json",
        ]
    )
    draft_payload = json.loads(capsys.readouterr().out)
    assert draft_exit == 2
    assert draft_payload["error_code"] == "REPORT_WRITE_ERROR"


def test_default_catalog_reports_preserve_each_run(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, local = _local_project(tmp_path, monkeypatch)
    database = local / "catalog.sqlite3"

    for _ in range(2):
        assert main(
            [
                "catalog-seed",
                "--database-file",
                str(database),
                "--json",
            ]
        ) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["report_file"].startswith(
            "backend/.local/reports/catalog-seed-"
        )

    reports = list((local / "reports").glob("catalog-seed-*.json"))
    assert len(reports) == 2
    assert reports[0].name != reports[1].name


def test_seed_evidence_records_normalization_version(
    tmp_path: Path,
) -> None:
    root = tmp_path / "quotes"
    for name in ("a.xlsx", "b.xlsx"):
        _write_quote(
            root / name,
            [["BEARING", "6204", "EA", 1, 100, 100]],
        )
    with _session() as session:
        ingest_corpus(session, root)
        seed_exact_catalog(session)
        evidence = [
            json.loads(row.evidence_json)
            for row in session.scalars(select(ItemMembershipDecision))
        ]
        assert {row["normalization_version"] for row in evidence} == {
            "match-v1"
        }


def test_standard_price_draft_report_query_count_is_chunk_bounded(
    tmp_path: Path,
) -> None:
    root = tmp_path / "quotes"
    rows: list[list[object]] = []
    for index in range(120):
        rows.extend(
            [
                [f"ITEM {index}", f"MODEL-{index}", "EA", 1, 100, 100],
                [f"ITEM {index}", f"MODEL-{index}", "EA", 1, 120, 120],
            ]
        )
    _write_quote(root / "many.xlsx", rows)
    with _session() as session:
        ingest_corpus(session, root)
        seed_exact_catalog(session)
        session.commit()
        statements = 0

        def count_selects(
            conn: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            nonlocal statements
            if statement.lstrip().upper().startswith("SELECT"):
                statements += 1

        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", count_selects)
        try:
            report = report_standard_price_drafts(session)
        finally:
            event.remove(engine, "before_cursor_execute", count_selects)
        assert report.standard_items == 120
        assert report.drafts_available == 120
        assert report.observations_available == 240
        assert statements <= 6
