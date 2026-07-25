from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.catalog.models import (
    DocumentMetadataVersion,
    ItemMembershipDecision,
    StandardItem,
    StandardItemVersion,
)
from app.catalog.service import CandidateEmbeddingRuntime
from app.cleansing.models import CleanDecision, CleanStatus
from app.documents.models import SourceDocument, SourceVariant
from app.embeddings.base import EmbeddingBatch
from app.embeddings.index import EmbeddingIndex, IndexMetadata
from app.main import app
from app.api.catalog import get_candidate_embedding_runtime
from app.quotes.models import RawQuoteItem


def _source(
    session: Session,
    *,
    status: CleanStatus = CleanStatus.INCLUDED,
) -> tuple[SourceDocument, RawQuoteItem]:
    document = SourceDocument(logical_name="quotes/api-sample.xlsx")
    variant = SourceVariant(
        document=document,
        path="quotes/api-sample.xlsx",
        sha256="b" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    raw = RawQuoteItem(
        source_variant=variant,
        source_sheet="Sheet1",
        source_row=7,
        source_cells="A7:G7",
        item_name_raw="Bearing",
        spec_raw="6204 ZZ",
        unit_raw="EA",
        unit_price_raw="120",
        parser_name="xlsx",
        parser_version="1",
    )
    session.add_all(
        [
            document,
            CleanDecision(
                raw_item=raw,
                status=status,
                reason_code="VALID",
                item_name_norm="BEARING",
                spec_norm="6204 ZZ",
                unit_norm="EA",
                unit_price=Decimal("120"),
                rule_version="clean-v1",
            ),
        ]
    )
    session.commit()
    return document, raw


def _create_standard_item(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/catalog/standard-items",
        json={
            "canonical_name": "BALL BEARING",
            "canonical_spec": "6204-ZZ",
            "canonical_unit": "EA",
            "aliases": ["BEARING"],
            "created_by": "buyer-1",
            "reason_detail": "create approved canonical item",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_catalog_workspace_lists_current_items_and_cas_context(
    client: TestClient,
    api_session: Session,
) -> None:
    document, raw = _source(api_session)
    item = _create_standard_item(client)
    rejected = client.post(
        f"/api/catalog/raw-items/{raw.id}/memberships",
        json={
            "standard_item_id": None,
            "status": "REJECTED",
            "expected_current_decision_id": None,
            "candidate_score": None,
            "method": "MANUAL_NO_MATCH",
            "evidence": {},
            "decided_by": "buyer-1",
            "reason_detail": "no suitable candidate",
        },
    )
    metadata = client.post(
        f"/api/catalog/documents/{document.id}/metadata",
        json={
            "supplier_name": "SUPPLIER A",
            "quote_date": "2026-07-01",
            "project_name": "PUNE LINE",
            "expected_current_version_id": None,
            "decided_by": "buyer-1",
            "reason_detail": "read from quote header",
        },
    )
    assert rejected.status_code == 201
    assert metadata.status_code == 201

    listing = client.get("/api/catalog/standard-items?limit=20")
    unmatched = client.get("/api/catalog/unmatched?limit=20")
    candidates = client.get(
        f"/api/catalog/raw-items/{raw.id}/candidates"
    )

    assert listing.status_code == 200
    assert listing.json() == {
        "items": [
            {
                "id": item["id"],
                "current_version": item["current_version"],
                "member_count": 0,
            }
        ],
        "next_cursor": None,
        "limit": 20,
    }
    assert unmatched.status_code == 200
    assert unmatched.json()["items"][0][
        "current_membership_decision_id"
    ] == rejected.json()["id"]
    assert candidates.status_code == 200
    assert candidates.json()["current_membership_decision_id"] == (
        rejected.json()["id"]
    )
    assert candidates.json()["current_document_metadata"] == metadata.json()


def test_create_and_match_is_atomic_when_membership_cas_is_stale(
    client: TestClient,
    api_session: Session,
) -> None:
    _, raw = _source(api_session)
    raw_id = raw.id
    rejected = client.post(
        f"/api/catalog/raw-items/{raw_id}/memberships",
        json={
            "standard_item_id": None,
            "status": "REJECTED",
            "expected_current_decision_id": None,
            "candidate_score": None,
            "method": "MANUAL_NO_MATCH",
            "evidence": {},
            "decided_by": "buyer-1",
            "reason_detail": "no suitable candidate",
        },
    )
    before = api_session.scalar(select(func.count(StandardItem.id)))
    api_session.rollback()

    stale = client.post(
        f"/api/catalog/raw-items/{raw_id}/standard-item",
        json={
            "canonical_name": "BEARING",
            "canonical_spec": "6204 ZZ",
            "canonical_unit": "EA",
            "aliases": [],
            "created_by": "buyer-2",
            "reason_detail": "create and group from reviewed source",
            "expected_current_decision_id": None,
        },
    )

    assert rejected.status_code == 201
    assert stale.status_code == 409
    assert stale.json()["detail"]["error_code"] == "STALE_CATALOG_DECISION"
    api_session.expire_all()
    assert api_session.scalar(select(func.count(StandardItem.id))) == before
    api_session.rollback()

    created = client.post(
        f"/api/catalog/raw-items/{raw_id}/standard-item",
        json={
            "canonical_name": "BEARING",
            "canonical_spec": "6204 ZZ",
            "canonical_unit": "EA",
            "aliases": [],
            "created_by": "buyer-2",
            "reason_detail": "create and group from reviewed source",
            "expected_current_decision_id": rejected.json()["id"],
        },
    )
    assert created.status_code == 201
    assert created.json()["standard_item"]["current_version"][
        "canonical_name"
    ] == "BEARING"
    assert created.json()["membership"]["status"] == "MATCHED"


def test_candidate_api_returns_evidence_without_auto_matching(
    client: TestClient,
    api_session: Session,
) -> None:
    _, raw = _source(api_session)
    item = _create_standard_item(client)

    response = client.get(f"/api/catalog/raw-items/{raw.id}/candidates")

    assert response.status_code == 200
    payload = response.json()
    assert payload["match_status"] == "CANDIDATE"
    assert payload["candidates"][0]["standard_item_id"] == item["id"]
    assert payload["source"]["path"] == "quotes/api-sample.xlsx"
    assert payload["source"]["row"] == 7
    assert payload["current_cleansing_decision"]["status"] == "INCLUDED"
    assert payload["candidates"][0]["embedding_status"] == "DISABLED"
    assert (
        api_session.scalar(select(func.count(ItemMembershipDecision.id))) == 0
    )


class _StaticEmbeddingClient:
    def embed(self, texts) -> EmbeddingBatch:
        return EmbeddingBatch(
            vectors=np.array([[1.0, 0.0] for _ in texts], dtype=np.float32),
            model="office-model",
            dimension=2,
        )


def test_candidate_api_reports_injected_embedding_result(
    client: TestClient,
    api_session: Session,
) -> None:
    _, raw = _source(api_session)
    item = _create_standard_item(client)
    runtime = CandidateEmbeddingRuntime(
        client=_StaticEmbeddingClient(),
        index=EmbeddingIndex(
            item_ids=np.array([item["id"]]),
            vectors=np.array([[1.0, 0.0]], dtype=np.float32),
            metadata=IndexMetadata(
                model="office-model",
                dimension=2,
                item_count=1,
                catalog_fingerprint="injected-test",
                normalization_version="match-v1",
                created_at=datetime.now(timezone.utc),
            ),
        ),
        model="office-model",
    )
    app.dependency_overrides[get_candidate_embedding_runtime] = (
        lambda: runtime
    )

    response = client.get(f"/api/catalog/raw-items/{raw.id}/candidates")

    assert response.status_code == 200
    candidate = response.json()["candidates"][0]
    assert candidate["embedding_status"] == "AVAILABLE"
    assert candidate["embedding_model"] == "office-model"
    assert candidate["embedding_score"] == "1.000000"


def test_match_approval_uses_optimistic_concurrency(
    client: TestClient,
    api_session: Session,
) -> None:
    _, raw = _source(api_session)
    item = _create_standard_item(client)
    body = {
        "standard_item_id": item["id"],
        "status": "MATCHED",
        "expected_current_decision_id": None,
        "candidate_score": "0.920000",
        "method": "MANUAL_CANDIDATE",
        "evidence": {"matched_tokens": ["6204-ZZ"]},
        "decided_by": "buyer-1",
        "reason_detail": "model and unit confirmed",
    }

    first = client.post(
        f"/api/catalog/raw-items/{raw.id}/memberships",
        json=body,
    )
    stale = client.post(
        f"/api/catalog/raw-items/{raw.id}/memberships",
        json=body,
    )

    assert first.status_code == 201, first.text
    assert stale.status_code == 409
    assert stale.json()["detail"]["error_code"] == "STALE_CATALOG_DECISION"
    assert stale.json()["detail"]["current_decision_id"] == first.json()["id"]


def test_rejected_or_not_included_rows_are_never_matched(
    client: TestClient,
    api_session: Session,
) -> None:
    _, raw = _source(api_session, status=CleanStatus.EXCLUDED)
    item = _create_standard_item(client)

    response = client.post(
        f"/api/catalog/raw-items/{raw.id}/memberships",
        json={
            "standard_item_id": item["id"],
            "status": "MATCHED",
            "expected_current_decision_id": None,
            "candidate_score": None,
            "method": "MANUAL",
            "evidence": {},
            "decided_by": "buyer-1",
            "reason_detail": "attempted override",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "RAW_ITEM_NOT_INCLUDED"


@pytest.mark.parametrize(
    "later_status",
    [CleanStatus.EXCLUDED, CleanStatus.REVIEW_REQUIRED],
)
def test_members_include_provenance_and_only_currently_included_rows(
    client: TestClient,
    api_session: Session,
    later_status: CleanStatus,
) -> None:
    _, raw = _source(api_session)
    item = _create_standard_item(client)
    approved = client.post(
        f"/api/catalog/raw-items/{raw.id}/memberships",
        json={
            "standard_item_id": item["id"],
            "status": "MATCHED",
            "expected_current_decision_id": None,
            "candidate_score": "0.920000",
            "method": "MANUAL_CANDIDATE",
            "evidence": {"matched_tokens": ["6204-ZZ"]},
            "decided_by": "buyer-1",
            "reason_detail": "verified source row",
        },
    )
    assert approved.status_code == 201

    before = client.get(
        f"/api/catalog/standard-items/{item['id']}/members"
    )

    assert before.status_code == 200
    member = before.json()["members"][0]
    assert member["source"] == {
        "document_id": raw.source_variant.document_id,
        "logical_name": "quotes/api-sample.xlsx",
        "variant_id": raw.source_variant_id,
        "path": "quotes/api-sample.xlsx",
        "sha256": "b" * 64,
        "security_state": "UNLOCKED",
        "selected_for_parsing_at_ingest": True,
        "sheet": "Sheet1",
        "page": None,
        "row": 7,
        "cells": "A7:G7",
        "parser_name": "xlsx",
        "parser_version": "1",
        "parser_warnings": [],
    }
    assert member["current_cleansing_decision"]["status"] == "INCLUDED"

    api_session.add(
        CleanDecision(
            raw_item_id=raw.id,
            status=later_status,
            reason_code="LATER_REVIEW",
            item_name_norm="BEARING",
            spec_norm="6204 ZZ",
            unit_norm="EA",
            unit_price=Decimal("120"),
            rule_version="clean-v2",
        )
    )
    api_session.commit()

    after = client.get(
        f"/api/catalog/standard-items/{item['id']}/members"
    )
    assert after.status_code == 200
    assert after.json()["members"] == []


def test_members_use_stable_raw_item_cursor_pagination(
    client: TestClient,
    api_session: Session,
) -> None:
    _, first_raw = _source(api_session)
    item = _create_standard_item(client)
    raw_items = [first_raw]
    for row_number in (8, 9):
        raw = RawQuoteItem(
            source_variant=first_raw.source_variant,
            source_sheet="Sheet1",
            source_row=row_number,
            source_cells=f"A{row_number}:G{row_number}",
            item_name_raw="Bearing",
            spec_raw="6204 ZZ",
            unit_raw="EA",
            unit_price_raw="120",
            parser_name="xlsx",
            parser_version="1",
        )
        api_session.add(
            CleanDecision(
                raw_item=raw,
                status=CleanStatus.INCLUDED,
                reason_code="VALID",
                item_name_norm="BEARING",
                spec_norm="6204 ZZ",
                unit_norm="EA",
                unit_price=Decimal("120"),
                rule_version="clean-v1",
            )
        )
        raw_items.append(raw)
    api_session.commit()
    for raw in raw_items:
        response = client.post(
            f"/api/catalog/raw-items/{raw.id}/memberships",
            json={
                "standard_item_id": item["id"],
                "status": "MATCHED",
                "expected_current_decision_id": None,
                "candidate_score": None,
                "method": "MANUAL",
                "evidence": {},
                "decided_by": "buyer-1",
                "reason_detail": "pagination fixture",
            },
        )
        assert response.status_code == 201

    first_page = client.get(
        f"/api/catalog/standard-items/{item['id']}/members?limit=2"
    )
    first_payload = first_page.json()
    second_page = client.get(
        f"/api/catalog/standard-items/{item['id']}/members",
        params={
            "limit": 2,
            "after_id": first_payload["next_cursor"],
        },
    )
    second_payload = second_page.json()

    assert first_page.status_code == 200
    assert [row["raw_item_id"] for row in first_payload["members"]] == [
        raw_items[0].id,
        raw_items[1].id,
    ]
    assert first_payload["next_cursor"] == raw_items[1].id
    assert first_payload["limit"] == 2
    assert second_page.status_code == 200
    assert [row["raw_item_id"] for row in second_payload["members"]] == [
        raw_items[2].id
    ]
    assert second_payload["next_cursor"] is None


def test_catalog_payload_bounds_are_enforced(
    client: TestClient,
    api_session: Session,
) -> None:
    _, raw = _source(api_session)
    oversized_alias = client.post(
        "/api/catalog/standard-items",
        json={
            "canonical_name": "BEARING",
            "canonical_spec": "6204",
            "canonical_unit": "EA",
            "aliases": ["X" * 501],
            "created_by": "buyer-1",
            "reason_detail": "invalid alias",
        },
    )
    item = _create_standard_item(client)
    nested: object = "value"
    for _ in range(10):
        nested = {"nested": nested}
    deep_evidence = client.post(
        f"/api/catalog/raw-items/{raw.id}/memberships",
        json={
            "standard_item_id": item["id"],
            "status": "MATCHED",
            "expected_current_decision_id": None,
            "candidate_score": None,
            "method": "MANUAL",
            "evidence": {"root": nested},
            "decided_by": "buyer-1",
            "reason_detail": "invalid evidence",
        },
    )
    oversized_evidence = client.post(
        f"/api/catalog/raw-items/{raw.id}/memberships",
        json={
            "standard_item_id": item["id"],
            "status": "MATCHED",
            "expected_current_decision_id": None,
            "candidate_score": None,
            "method": "MANUAL",
            "evidence": {"payload": "X" * 70_000},
            "decided_by": "buyer-1",
            "reason_detail": "invalid evidence size",
        },
    )

    assert oversized_alias.status_code == 422
    assert deep_evidence.status_code == 422
    assert oversized_evidence.status_code == 422


def test_item_metadata_and_document_metadata_append_versions(
    client: TestClient,
    api_session: Session,
) -> None:
    document, _ = _source(api_session)
    item = _create_standard_item(client)
    item_update = client.post(
        f"/api/catalog/standard-items/{item['id']}/versions",
        json={
            "canonical_name": "DEEP GROOVE BALL BEARING",
            "canonical_spec": "6204-ZZ",
            "canonical_unit": "EA",
            "aliases": ["BEARING", "BALL BEARING"],
            "expected_current_version_id": item["current_version"]["id"],
            "created_by": "buyer-2",
            "reason_detail": "clarify canonical name",
        },
    )
    first_metadata = client.post(
        f"/api/catalog/documents/{document.id}/metadata",
        json={
            "supplier_name": "SUPPLIER A",
            "quote_date": "2026-07-01",
            "project_name": "PUNE LINE",
            "expected_current_version_id": None,
            "decided_by": "buyer-1",
            "reason_detail": "read from quote header",
        },
    )
    second_metadata = client.post(
        f"/api/catalog/documents/{document.id}/metadata",
        json={
            "supplier_name": "SUPPLIER A CO.",
            "quote_date": "2026-07-01",
            "project_name": "PUNE LINE",
            "expected_current_version_id": first_metadata.json()["id"],
            "decided_by": "buyer-2",
            "reason_detail": "correct legal supplier name",
        },
    )

    assert item_update.status_code == 201, item_update.text
    assert item_update.json()["version_number"] == 2
    assert first_metadata.status_code == 201, first_metadata.text
    assert second_metadata.status_code == 201, second_metadata.text
    assert second_metadata.json()["version_number"] == 2
    assert second_metadata.json()["reason_detail"] == (
        "correct legal supplier name"
    )
    assert (
        api_session.scalar(select(func.count(StandardItemVersion.id))) == 2
    )
    assert (
        api_session.scalar(select(func.count(DocumentMetadataVersion.id))) == 2
    )


def test_stale_metadata_write_is_atomic(
    client: TestClient,
    api_session: Session,
) -> None:
    document, _ = _source(api_session)
    body = {
        "supplier_name": "SUPPLIER A",
        "quote_date": None,
        "project_name": None,
        "expected_current_version_id": None,
        "decided_by": "buyer-1",
        "reason_detail": "initial metadata",
    }
    first = client.post(
        f"/api/catalog/documents/{document.id}/metadata",
        json=body,
    )
    stale = client.post(
        f"/api/catalog/documents/{document.id}/metadata",
        json=body,
    )

    assert first.status_code == 201
    assert stale.status_code == 409
    assert stale.json()["detail"]["error_code"] == "STALE_CATALOG_DECISION"
    assert stale.json()["detail"]["current_version_id"] == first.json()["id"]
    assert (
        api_session.scalar(select(func.count(DocumentMetadataVersion.id))) == 1
    )
