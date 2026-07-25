from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx
import numpy as np
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.catalog.models import (
    ItemMembershipDecision,
    MembershipStatus,
    StandardItem,
    StandardItemVersion,
)
from app.catalog.service import (
    CatalogConflict,
    append_membership_decision,
    build_candidate_embedding_runtime,
    candidate_matches,
    catalog_fingerprint,
)
from app.cleansing.models import CleanDecision, CleanStatus
from app.core.config import Settings
from app.db.base import Base
from app.db.sqlite import configure_sqlite
from app.documents.models import SourceDocument, SourceVariant
from app.embeddings.index import IndexMetadata, save_index
from app.quotes.models import RawQuoteItem


@pytest.fixture
def session() -> Session:
    engine = configure_sqlite(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as value:
        yield value
    engine.dispose()


def _raw_item(
    session: Session,
    *,
    status: CleanStatus = CleanStatus.INCLUDED,
) -> RawQuoteItem:
    document = SourceDocument(logical_name="quotes/sample.xlsx")
    variant = SourceVariant(
        document=document,
        path="quotes/sample.xlsx",
        sha256="a" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    raw = RawQuoteItem(
        source_variant=variant,
        source_sheet="Sheet1",
        source_row=2,
        item_name_raw="Bearing",
        spec_raw="6204 ZZ",
        unit_raw="EA",
        parser_name="xlsx",
        parser_version="1",
    )
    decision = CleanDecision(
        raw_item=raw,
        status=status,
        reason_code="VALID",
        item_name_norm="BEARING",
        spec_norm="6204 ZZ",
        unit_norm="EA",
        unit_price=Decimal("120"),
        rule_version="clean-v1",
    )
    session.add_all([document, decision])
    session.commit()
    return raw


def _standard_item(session: Session) -> StandardItem:
    item = StandardItem()
    session.add_all(
        [
            item,
            StandardItemVersion(
                standard_item=item,
                version_number=1,
                canonical_name="BALL BEARING",
                canonical_spec="6204-ZZ",
                canonical_unit="EA",
                aliases_json='["BEARING"]',
                created_by="buyer-1",
                change_reason="initial grouping",
            ),
        ]
    )
    session.commit()
    return item


def test_candidate_search_never_creates_membership(session: Session) -> None:
    raw = _raw_item(session)
    item = _standard_item(session)

    result = candidate_matches(session, raw.id, top_n=5)

    assert result.match_status == "CANDIDATE"
    assert result.current_cleansing_decision.status == CleanStatus.INCLUDED
    assert result.candidates[0].standard_item_id == item.id
    assert result.candidates[0].unit_compatible is True
    assert result.candidates[0].model_tokens_compatible is True
    assert session.scalar(select(func.count(ItemMembershipDecision.id))) == 0


def test_valid_configured_openai_index_enriches_candidates(
    session: Session,
    tmp_path,
) -> None:
    raw = _raw_item(session)
    item = _standard_item(session)
    index_path = tmp_path / "standard-items.npz"
    save_index(
        index_path,
        item_ids=np.array([item.id]),
        vectors=np.array([[1.0, 0.0]], dtype=np.float32),
        metadata=IndexMetadata(
            model="office-model",
            dimension=2,
            item_count=1,
            catalog_fingerprint=catalog_fingerprint(session),
            normalization_version="match-v1",
            created_at=datetime.now(timezone.utc),
        ),
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": "office-model",
                "data": [{"index": 0, "embedding": [1.0, 0.0]}],
            },
        )

    runtime = build_candidate_embedding_runtime(
        session,
        settings=Settings(
            hchat_embedding_enabled=True,
            hchat_embedding_endpoint="https://intranet.invalid/embeddings",
            hchat_embedding_api_key=SecretStr("office-key"),
            hchat_embedding_model="office-model",
            hchat_embedding_api_style="openai",
            embedding_index_file=index_path,
        ),
        transport=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = candidate_matches(
        session,
        raw.id,
        top_n=5,
        embedding_runtime=runtime,
    )

    assert result.embedding_model == "office-model"
    assert result.candidates[0].score.embedding_status == "AVAILABLE"
    assert result.candidates[0].score.embedding_score == Decimal("1.000000")
    assert len(requests) == 1


def test_index_mismatch_falls_back_without_http(
    session: Session,
    tmp_path,
) -> None:
    raw = _raw_item(session)
    item = _standard_item(session)
    index_path = tmp_path / "stale-index.npz"
    save_index(
        index_path,
        item_ids=np.array([item.id]),
        vectors=np.array([[1.0, 0.0]], dtype=np.float32),
        metadata=IndexMetadata(
            model="office-model",
            dimension=2,
            item_count=1,
            catalog_fingerprint="stale-catalog",
            normalization_version="match-v1",
            created_at=datetime.now(timezone.utc),
        ),
    )
    runtime = build_candidate_embedding_runtime(
        session,
        settings=Settings(
            hchat_embedding_enabled=True,
            hchat_embedding_endpoint="https://intranet.invalid/embeddings",
            hchat_embedding_api_key=SecretStr("office-key"),
            hchat_embedding_model="office-model",
            hchat_embedding_api_style="openai",
            embedding_index_file=index_path,
        ),
        transport=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: pytest.fail("stale index must prevent HTTP")
            )
        ),
    )

    result = candidate_matches(
        session,
        raw.id,
        top_n=5,
        embedding_runtime=runtime,
    )

    assert result.embedding_model is None
    assert result.candidates[0].score.embedding_status == "UNAVAILABLE"
    assert result.candidates[0].score.embedding_score is None


def test_membership_append_is_human_only_and_compare_and_swap(
    session: Session,
) -> None:
    raw = _raw_item(session)
    item = _standard_item(session)
    first = append_membership_decision(
        session,
        raw_item_id=raw.id,
        standard_item_id=item.id,
        status=MembershipStatus.MATCHED,
        expected_current_decision_id=None,
        candidate_score=Decimal("0.920000"),
        method="MANUAL_CANDIDATE",
        evidence={"matched_tokens": ["6204-ZZ"]},
        decided_by="buyer-1",
        reason_detail="verified model and unit",
    )
    session.commit()

    assert first.decided_by == "buyer-1"
    assert '"reason_detail":"verified model and unit"' in first.evidence_json
    with pytest.raises(CatalogConflict) as stale:
        append_membership_decision(
            session,
            raw_item_id=raw.id,
            standard_item_id=item.id,
            status=MembershipStatus.MATCHED,
            expected_current_decision_id=None,
            candidate_score=Decimal("0.920000"),
            method="MANUAL_CANDIDATE",
            evidence={},
            decided_by="buyer-2",
            reason_detail="duplicate stale decision",
        )
    assert stale.value.error_code == "STALE_CATALOG_DECISION"
    assert stale.value.current_id == first.id


@pytest.mark.parametrize(
    ("decided_by", "reason_detail"),
    [("SYSTEM", "automatic"), ("buyer-1", "   ")],
)
def test_manual_membership_requires_human_actor_and_reason(
    session: Session,
    decided_by: str,
    reason_detail: str,
) -> None:
    raw = _raw_item(session)
    item = _standard_item(session)

    with pytest.raises(ValueError, match="human actor|reason"):
        append_membership_decision(
            session,
            raw_item_id=raw.id,
            standard_item_id=item.id,
            status=MembershipStatus.MATCHED,
            expected_current_decision_id=None,
            candidate_score=None,
            method="MANUAL",
            evidence={},
            decided_by=decided_by,
            reason_detail=reason_detail,
        )


@pytest.mark.parametrize(
    "status",
    [CleanStatus.EXCLUDED, CleanStatus.REVIEW_REQUIRED],
)
def test_only_currently_included_rows_can_receive_membership(
    session: Session,
    status: CleanStatus,
) -> None:
    raw = _raw_item(session, status=status)
    item = _standard_item(session)

    with pytest.raises(CatalogConflict) as blocked:
        append_membership_decision(
            session,
            raw_item_id=raw.id,
            standard_item_id=item.id,
            status=MembershipStatus.MATCHED,
            expected_current_decision_id=None,
            candidate_score=None,
            method="MANUAL",
            evidence={},
            decided_by="buyer-1",
            reason_detail="manual review",
        )

    assert blocked.value.error_code == "RAW_ITEM_NOT_INCLUDED"
