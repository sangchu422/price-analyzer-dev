# 표준 DB 근거 신뢰도 판정 — supplier_count 기반 전환 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "근거 품질"(SINGLE_OBSERVATION/MULTI_OBSERVATION) 판정 기준을 `observation_count`
(행 개수)에서 `supplier_count`(서로 다른 공급사 수)로 바꾸고, 두 화면(표준 DB 탐색기,
`/analysis`)에 공급사 수를 노출한다.

**Architecture:** `StandardPriceVersion.supplier_count`는 이미 저장되어 있는 필드다
(`_draft_from_evidence_rows`가 계산). 판정 함수 `evidence_quality()`(하나만 존재,
`backend/app/standard_database/read_service.py`)의 입력 인자를 `observation_count`에서
`supplier_count`로 바꾸고, 이 함수를 호출하는 4곳(`read_service.py` 프로퍼티,
`api/pricing.py` 2곳)의 인자를 맞춰 바꾼다. 함수를 안 쓰고 판정 로직을 직접
인라인으로 중복 구현한 2곳(`api/catalog.py`의 근거 상세 엔드포인트,
`analysis/service.py`의 `_classify_line`)은 공유 함수를 쓰도록 고쳐서 판정 기준을
한곳으로 모은다. 통계 계산(min/median/average/max)과 `observation_count` 자체 값은
손대지 않으므로 표준 DB 재구축이 필요 없다.

**Tech Stack:** FastAPI + SQLAlchemy (backend), React + TypeScript + Vitest (frontend),
pytest.

**설계 근거:** `docs/superpowers/specs/2026-08-14-standard-evidence-supplier-count-design.md`

---

### Task 1: 판정 함수 전환 — `backend/app/standard_database/read_service.py`

**Files:**
- Modify: `backend/app/standard_database/read_service.py:78-106` (판정 함수·프로퍼티),
  `:231-246` (필터)
- Test: `backend/tests/api/test_standard_database_explorer_api.py`

- [ ] **Step 1: 실패하는 테스트 먼저 — 같은 공급사 2건은 SINGLE_OBSERVATION이어야 함**

`backend/tests/api/test_standard_database_explorer_api.py` 파일 끝에 추가:

```python
def test_evidence_quality_reflects_distinct_suppliers_not_row_count(
    client: TestClient,
    api_session: Session,
) -> None:
    _historical_row(
        api_session,
        row=101,
        name="GASKET",
        spec="G-100",
        unit="EA",
        price="10",
        supplier="SUPPLIER Z",
        maker="NOK",
    )
    _historical_row(
        api_session,
        row=102,
        name="GASKET",
        spec="G-100",
        unit="EA",
        price="10",
        supplier="SUPPLIER Z",
        maker="NOK",
    )
    api_session.flush()
    build_standard_database(api_session)
    api_session.commit()

    response = client.get(
        "/api/catalog/standard-items",
        params={"limit": 20, "search": "GASKET"},
    )

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["observation_count"] == 2
    assert item["supplier_count"] == 1
    assert item["evidence_quality"] == "SINGLE_OBSERVATION"

    filtered = client.get(
        "/api/catalog/standard-items",
        params={
            "limit": 20,
            "search": "GASKET",
            "evidence_quality": "MULTI_OBSERVATION",
        },
    )
    assert filtered.json()["items"] == []
```

(`_historical_row`와 `build_standard_database`는 이 파일에 이미 import되어 있음 — 상단
import 블록과 112행 `_built_catalog` 정의를 참고. `item["supplier_count"]`는 Task 3에서
응답에 추가하기 전까지는 `KeyError` 없이 그냥 `None`으로 나오거나 응답에 아예 없어서
이 assert가 실패한다 — 지금 단계에서는 실패해도 정상, Task 3까지 끝나야 통과한다. 이
스텝에서는 `item["evidence_quality"] == "SINGLE_OBSERVATION"` assert가 먼저 실패하는지만
확인한다: 지금 코드는 observation_count=2를 그대로 MULTI_OBSERVATION으로 판정하기
때문에 실패해야 정상.)

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/api/test_standard_database_explorer_api.py::test_evidence_quality_reflects_distinct_suppliers_not_row_count -v`
Expected: FAIL — `assert "MULTI_OBSERVATION" == "SINGLE_OBSERVATION"`에서 깨짐 (또는
`supplier_count` 키가 없어서 `AssertionError: assert None == 1`).

- [ ] **Step 2: `evidence_quality()` 함수와 호출부를 supplier_count 기준으로 전환**

`backend/app/standard_database/read_service.py:103-106`(현재):

```python
def evidence_quality(observation_count: int) -> EvidenceQuality:
    if observation_count == 1:
        return EvidenceQuality.SINGLE_OBSERVATION
    return EvidenceQuality.MULTI_OBSERVATION
```

을 다음으로 교체:

```python
def evidence_quality(supplier_count: int) -> EvidenceQuality:
    if supplier_count <= 1:
        return EvidenceQuality.SINGLE_OBSERVATION
    return EvidenceQuality.MULTI_OBSERVATION
```

(`== 1`을 `<= 1`로 바꾼 이유: `supplier_count`가 0인 데이터는 실무상 없어야 하지만,
"공급사가 1곳 이하면 신뢰도 낮음"이라는 의미를 경계값에서도 안전하게 표현한다.)

`:78-82`(현재):

```python
    @property
    def evidence_quality(self) -> EvidenceQuality | None:
        if self.current_price is None:
            return None
        return evidence_quality(self.current_price.observation_count)
```

을 다음으로 교체:

```python
    @property
    def evidence_quality(self) -> EvidenceQuality | None:
        if self.current_price is None:
            return None
        return evidence_quality(self.current_price.supplier_count)
```

- [ ] **Step 3: 근거 품질 필터도 supplier_count 기준으로 전환**

`backend/app/standard_database/read_service.py:231-246`(현재):

```python
            if (
                quality is EvidenceQuality.SINGLE_OBSERVATION
                and (
                    price is None
                    or price.observation_count != 1
                )
            ):
                continue
            if (
                quality is EvidenceQuality.MULTI_OBSERVATION
                and (
                    price is None
                    or price.observation_count <= 1
                )
            ):
                continue
```

을 다음으로 교체:

```python
            if (
                quality is EvidenceQuality.SINGLE_OBSERVATION
                and (
                    price is None
                    or price.supplier_count > 1
                )
            ):
                continue
            if (
                quality is EvidenceQuality.MULTI_OBSERVATION
                and (
                    price is None
                    or price.supplier_count <= 1
                )
            ):
                continue
```

- [ ] **Step 4: 새 테스트 재실행 — 여전히 supplier_count 키 관련 부분은 실패할 수 있음**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/api/test_standard_database_explorer_api.py::test_evidence_quality_reflects_distinct_suppliers_not_row_count -v`
Expected: `evidence_quality` 관련 assert는 통과, `item["supplier_count"] == 1` assert는
아직 실패 (API 응답에 `supplier_count` 필드가 없어서 `None`) — Task 3에서 해결한다.
지금 실패하는 이유가 정확히 이 assert 한 줄인지 확인하고 다음 태스크로 넘어간다.

- [ ] **Step 5: 기존 테스트 회귀 확인**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/api/test_standard_database_explorer_api.py -v`
Expected: `test_standard_catalog_explorer_exposes_current_price_and_provenance`와
`test_standard_catalog_explorer_returns_single_observation_evidence_links`는 계속 통과
(각각 공급사 2곳/1곳인 픽스처라 판정 결과가 안 바뀜). 새로 추가한 테스트만
`supplier_count` assert에서 실패.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/standard_database/read_service.py backend/tests/api/test_standard_database_explorer_api.py
git commit -m "fix(standard-db): base evidence quality on distinct suppliers, not row count"
```

---

### Task 2: `api/pricing.py` 호출부 전환

**Files:**
- Modify: `backend/app/api/pricing.py:243-247` (`_draft_payload`), `:326-330`
  (`_version_payload`)
- Test: `backend/tests/api/test_pricing_api.py` (기존 테스트로 검증, 새 테스트 불필요)

- [ ] **Step 1: `_draft_payload`의 evidence_quality 인자 전환**

`backend/app/api/pricing.py:243-246`(현재):

```python
        "observation_count": draft.observation_count,
        "evidence_quality": evidence_quality(
            draft.observation_count
        ).value,
```

을 다음으로 교체:

```python
        "observation_count": draft.observation_count,
        "evidence_quality": evidence_quality(
            draft.supplier_count
        ).value,
```

- [ ] **Step 2: `_version_payload`의 evidence_quality 인자 전환**

`backend/app/api/pricing.py:326-329`(현재):

```python
        "observation_count": version.observation_count,
        "evidence_quality": evidence_quality(
            version.observation_count
        ).value,
```

을 다음으로 교체:

```python
        "observation_count": version.observation_count,
        "evidence_quality": evidence_quality(
            version.supplier_count
        ).value,
```

- [ ] **Step 3: 기존 테스트로 검증**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/api/test_pricing_api.py -v`
Expected: 전체 PASS — `test_pricing_api_draft_approval_and_history`의 픽스처는 공급사
"A"/"B" 2곳이라 `supplier_count == 2`(118행에 이미 assert됨)이고 `evidence_quality`도
여전히 `"MULTI_OBSERVATION"`으로 나와야 정상이다. 만약 실패하면 Step 1~2 교체가 잘못된
것이니 고친다.

- [ ] **Step 4: 커밋**

```bash
git add backend/app/api/pricing.py
git commit -m "fix(pricing): pass supplier_count to the shared evidence-quality function"
```

---

### Task 3: `api/catalog.py` — supplier_count 노출 + 중복 판정 로직 제거

**Files:**
- Modify: `backend/app/api/catalog.py:52-59` (import), `:248-254`
  (`StandardItemSummaryResponse`), `:310-314` (`StandardEvidenceResponse`),
  `:651-681` (`_explorer_summary_payload`), `:961-970` (`get_standard_item_evidence`)
- Test: `backend/tests/api/test_standard_database_explorer_api.py`

- [ ] **Step 1: `evidence_quality` 함수를 import**

`backend/app/api/catalog.py:52-59`(현재):

```python
from app.standard_database.read_service import (
    EvidenceQuality,
    StandardBuildProvenance,
    StandardExplorerNotFound,
    StandardExplorerSummary,
    list_standard_explorer_items,
    standard_item_evidence,
)
```

을 다음으로 교체:

```python
from app.standard_database.read_service import (
    EvidenceQuality,
    StandardBuildProvenance,
    StandardExplorerNotFound,
    StandardExplorerSummary,
    evidence_quality,
    list_standard_explorer_items,
    standard_item_evidence,
)
```

- [ ] **Step 2: 응답 스키마에 `supplier_count` 필드 추가**

`backend/app/api/catalog.py:248-254`(현재):

```python
class StandardItemSummaryResponse(StandardItemResponse):
    member_count: int
    observation_count: int | None
    current_price_version_id: int | None
    captured_price_version_id: int | None
    operational_status: str
    evidence_quality: EvidenceQuality | None
```

을 다음으로 교체:

```python
class StandardItemSummaryResponse(StandardItemResponse):
    member_count: int
    observation_count: int | None
    supplier_count: int | None
    current_price_version_id: int | None
    captured_price_version_id: int | None
    operational_status: str
    evidence_quality: EvidenceQuality | None
```

`backend/app/api/catalog.py:310-314`(현재):

```python
class StandardEvidenceResponse(BaseModel):
    standard_item_id: int
    standard_price_version_id: int
    observation_count: int
    evidence_quality: EvidenceQuality
```

을 다음으로 교체:

```python
class StandardEvidenceResponse(BaseModel):
    standard_item_id: int
    standard_price_version_id: int
    observation_count: int
    supplier_count: int
    evidence_quality: EvidenceQuality
```

- [ ] **Step 3: 목록 응답(`_explorer_summary_payload`)에 supplier_count 채우기**

`backend/app/api/catalog.py:651-660`(현재):

```python
def _explorer_summary_payload(
    summary: StandardExplorerSummary,
) -> dict[str, object]:
    price = summary.current_price
    observation_count = None if price is None else price.observation_count
    return {
        "id": summary.current_version.standard_item_id,
        "current_version": _version_payload(summary.current_version),
        "member_count": summary.member_count,
        "observation_count": observation_count,
```

을 다음으로 교체:

```python
def _explorer_summary_payload(
    summary: StandardExplorerSummary,
) -> dict[str, object]:
    price = summary.current_price
    observation_count = None if price is None else price.observation_count
    supplier_count = None if price is None else price.supplier_count
    return {
        "id": summary.current_version.standard_item_id,
        "current_version": _version_payload(summary.current_version),
        "member_count": summary.member_count,
        "observation_count": observation_count,
        "supplier_count": supplier_count,
```

- [ ] **Step 4: 근거 상세 응답(`get_standard_item_evidence`)의 중복 판정 로직 제거**

`backend/app/api/catalog.py:961-970`(현재):

```python
    return {
        "standard_item_id": standard_item_id,
        "standard_price_version_id": price.id,
        "observation_count": price.observation_count,
        "evidence_quality": (
            EvidenceQuality.SINGLE_OBSERVATION.value
            if price.observation_count == 1
            else EvidenceQuality.MULTI_OBSERVATION.value
        ),
        "provenance": _build_provenance_payload(provenance),
```

을 다음으로 교체:

```python
    return {
        "standard_item_id": standard_item_id,
        "standard_price_version_id": price.id,
        "observation_count": price.observation_count,
        "supplier_count": price.supplier_count,
        "evidence_quality": evidence_quality(price.supplier_count).value,
        "provenance": _build_provenance_payload(provenance),
```

- [ ] **Step 5: Task 1의 새 테스트 재실행 — 이제 전체 통과해야 함**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/api/test_standard_database_explorer_api.py::test_evidence_quality_reflects_distinct_suppliers_not_row_count -v`
Expected: PASS.

- [ ] **Step 6: 기존 근거 상세 테스트에 supplier_count assert 추가**

`backend/tests/api/test_standard_database_explorer_api.py`의
`test_standard_catalog_explorer_returns_single_observation_evidence_links`(351행 부근)
에서 373행:

```python
    assert payload["observation_count"] == 1
```

바로 다음 줄에 추가:

```python
    assert payload["supplier_count"] == 1
```

`test_standard_catalog_explorer_exposes_current_price_and_provenance`(274행 부근)에서
299행:

```python
    assert item["observation_count"] == 2
```

바로 다음 줄에 추가:

```python
    assert item["supplier_count"] == 2
```

- [ ] **Step 7: 전체 재실행**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/api/test_standard_database_explorer_api.py -v`
Expected: 전체 PASS, 0 failure.

- [ ] **Step 8: 커밋**

```bash
git add backend/app/api/catalog.py backend/tests/api/test_standard_database_explorer_api.py
git commit -m "feat(standard-db): expose supplier_count, reuse shared evidence-quality function"
```

---

### Task 4: `/analysis` 화면 판정도 동일 기준으로 — `backend/app/analysis/service.py`

**Files:**
- Modify: `backend/app/analysis/service.py:36-39` (import), `:766-771`
  (`_classify_line`)
- Test: `backend/tests/api/test_analysis_api.py`

`_classify_line`가 계산한 `evidence_quality` 값을 실제로 검증하는 테스트가 지금
하나도 없다 (`tests/analysis/test_target_price.py`의 리터럴은 다른 테스트용 하드코딩
픽스처일 뿐 이 계산 경로를 거치지 않음). `/analysis` 화면의 "신뢰도 낮음" 표시가
Task 1과 같은 버그를 갖고 있었으므로, 고치기 전에 이 경로도 실제로 검증하는
회귀 테스트를 먼저 추가한다.

- [ ] **Step 1: 실패하는 테스트 먼저 — MATCHED 라인도 공급사 기준으로 판정해야 함**

`backend/tests/api/test_analysis_api.py` 상단 import에 두 줄 추가.

`backend/tests/api/test_analysis_api.py:1-3`(현재):

```python
from __future__ import annotations

from decimal import Decimal
```

을 다음으로 교체:

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal
```

`backend/tests/api/test_analysis_api.py:10`(현재):

```python
from app.catalog.models import ItemMembershipDecision, StandardPriceVersion
```

을 다음으로 교체:

```python
from app.catalog.models import (
    DocumentMetadataVersion,
    ItemMembershipDecision,
    StandardPriceVersion,
)
```

파일 끝에 새 테스트 추가:

```python
def test_matched_line_evidence_quality_reflects_distinct_suppliers(
    client: TestClient,
    api_session: Session,
) -> None:
    for row, supplier in [(1, "SUPPLIER Z"), (2, "SUPPLIER Z")]:
        historical = SourceDocument(logical_name=f"historical-{row}.xlsx")
        variant = SourceVariant(
            document=historical,
            path=f"historical-{row}.xlsx",
            sha256=f"{row:064x}",
            extension=".xlsx",
            security_state="UNLOCKED",
            selected_for_parsing_at_ingest=True,
        )
        raw = RawQuoteItem(
            source_variant=variant,
            source_sheet="Sheet1",
            source_row=1,
            item_name_raw="CUSTOM ITEM 1",
            spec_raw="ZZ-1",
            unit_raw="EA",
            unit_price_raw="80",
            parser_name="xlsx",
            parser_version="reader-v1",
        )
        api_session.add_all(
            [
                CleanDecision(
                    raw_item=raw,
                    status=CleanStatus.INCLUDED,
                    reason_code="VALID",
                    item_name_norm="CUSTOM ITEM 1",
                    spec_norm="ZZ-1",
                    unit_norm="EA",
                    unit_price=Decimal("80"),
                    rule_version="clean-v1",
                ),
                DocumentMetadataVersion(
                    source_document=historical,
                    version_number=1,
                    supplier_name=supplier,
                    quote_date=date(2026, 7, row),
                    project_name=None,
                    decided_by="data-owner",
                ),
            ]
        )
        api_session.flush()
        api_session.add(
            QuoteDocumentRole(
                document_id=historical.id,
                purpose=QuoteDocumentPurpose.HISTORICAL_REFERENCE,
                decided_by="data-owner",
                reason_detail="training evidence",
            )
        )
    build_standard_database(api_session)
    api_session.commit()
    incoming = _document(api_session, rows=1)

    response = client.get(
        f"/api/analysis/documents/{incoming.id}?limit=100"
    )

    assert response.status_code == 200
    line = response.json()["lines"][0]
    assert line["match_status"] == "MATCHED"
    assert line["standard_observation_count"] == 2
    assert line["evidence_quality"] == "SINGLE_OBSERVATION"
```

(두 이력 문서 모두 품명 "CUSTOM ITEM 1"/규격 "ZZ-1"/단위 "EA"로 같은 표준품목에
묶이고, 공급사는 둘 다 "SUPPLIER Z"로 같다 — `test_incoming_exact_key_uses_standard_price_without_membership_write`(184행)의 패턴을 그대로 따르되 이력 행을 2개로 늘리고
`DocumentMetadataVersion`으로 공급사를 명시한 것이 차이점.)

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/api/test_analysis_api.py::test_matched_line_evidence_quality_reflects_distinct_suppliers -v`
Expected: FAIL — 지금 코드는 `standard_observation_count == 2`를 그대로
`MULTI_OBSERVATION`으로 판정하므로 마지막 assert에서 깨진다.

- [ ] **Step 2: 공유 함수 import 추가**

`backend/app/analysis/service.py:36-39`(현재):

```python
from app.standard_database.operational import (
    current_standard_member_counts,
    operational_standard_prices,
)
```

을 다음으로 교체:

```python
from app.standard_database.operational import (
    current_standard_member_counts,
    operational_standard_prices,
)
from app.standard_database.read_service import evidence_quality
```

- [ ] **Step 3: 중복 인라인 판정 로직 제거**

`backend/app/analysis/service.py:766-771`(현재):

```python
            evidence_quality=(
                "SINGLE_OBSERVATION"
                if price.observation_count == 1
                else "MULTI_OBSERVATION"
            ),
```

을 다음으로 교체:

```python
            evidence_quality=evidence_quality(price.supplier_count).value,
```

- [ ] **Step 4: 새 테스트 재실행 — 통과 확인**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/api/test_analysis_api.py::test_matched_line_evidence_quality_reflects_distinct_suppliers -v`
Expected: PASS.

- [ ] **Step 5: 분석 관련 전체 테스트 실행**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest tests/analysis tests/api/test_analysis_api.py -v`
Expected: 전체 PASS. (`tests/analysis/test_target_price.py`의 52행 픽스처는
`AnalysisLine`에 하드코딩된 리터럴 값이라 이 변경과 무관하게 그대로 통과해야 한다 —
만약 실패하면 원인을 먼저 파악하고 보고한다.)

- [ ] **Step 6: 커밋**

```bash
git add backend/app/analysis/service.py backend/tests/api/test_analysis_api.py
git commit -m "fix(analysis): reuse shared evidence-quality function based on supplier_count"
```

---

### Task 5: 백엔드 전체 검증

- [ ] **Step 1: 전체 스위트 실행**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest -q`
Expected: 전체 PASS, 실패 0건. (다른 모듈에서 `evidence_quality(`을 observation_count로
호출하는 곳이 더 있는지 이 전체 실행으로 확인됨 — 있다면 원인 보고 후 같은 패턴으로
고친다.)

---

### Task 6: 프론트 타입 — `frontend/src/api/client.ts`

**Files:**
- Modify: `frontend/src/api/client.ts:145-171` (`StandardItemSummary`), `:381-398`
  (`StandardEvidence`)

- [ ] **Step 1: `StandardItemSummary`에 `supplier_count` 추가**

`frontend/src/api/client.ts:154-156`(현재):

```typescript
  member_count: number;
  observation_count: number | null;
  evidence_quality: EvidenceQuality | null;
```

을 다음으로 교체:

```typescript
  member_count: number;
  observation_count: number | null;
  supplier_count?: number | null;
  evidence_quality: EvidenceQuality | null;
```

- [ ] **Step 2: `StandardEvidence`에 `supplier_count` 추가**

`frontend/src/api/client.ts:381-385`(현재):

```typescript
export interface StandardEvidence {
  standard_item_id: number;
  standard_price_version_id: number;
  observation_count: number;
  evidence_quality: EvidenceQuality;
```

을 다음으로 교체:

```typescript
export interface StandardEvidence {
  standard_item_id: number;
  standard_price_version_id: number;
  observation_count: number;
  supplier_count?: number | null;
  evidence_quality: EvidenceQuality;
```

(옵셔널로 두는 이유: 이 필드를 모르는 기존 목 데이터·캐시 응답이 있어도 타입 에러
없이 동작해야 하고, 렌더링 쪽에서 `?? observation_count`로 폴백하기 때문이다.)

- [ ] **Step 3: 타입 체크**

Run: `cd frontend && npm.cmd run build`
Expected: 이 시점에는 아직 이 필드를 쓰는 코드가 없으므로 에러 없이 통과해야 한다.

- [ ] **Step 4: 커밋**

```bash
git add frontend/src/api/client.ts
git commit -m "feat(types): add optional supplier_count to standard-item API types"
```

---

### Task 7: `EvidenceBadge` 컴포넌트 — 공급사 수 표시

**Files:**
- Modify: `frontend/src/components/EvidenceBadge.tsx`
- Test: `frontend/src/pages/StandardPricesPage.test.tsx`

- [ ] **Step 1: 실패하는 테스트 먼저 — 라벨에 공급사 수가 포함되어야 함**

`frontend/src/pages/StandardPricesPage.test.tsx:167`(현재):

```typescript
  expect(screen.getAllByText("근거 1건").length).toBeGreaterThan(0);
```

을 다음으로 교체:

```typescript
  expect(
    screen.getAllByText("근거 1건 · 공급사 1곳").length,
  ).toBeGreaterThan(0);
```

Run: `cd frontend && npm.cmd test -- --run src/pages/StandardPricesPage.test.tsx`
Expected: FAIL — 실제 렌더링 텍스트가 아직 "근거 1건"이라 새 문자열과 안 맞음.

- [ ] **Step 2: `EvidenceBadge`에 supplierCount 표시 추가**

`frontend/src/components/EvidenceBadge.tsx` 전체를 다음으로 교체:

```typescript
import type { EvidenceQuality } from "../api/client";

export function EvidenceBadge({
  quality,
  count,
  supplierCount,
}: {
  quality: EvidenceQuality | null;
  count: number;
  supplierCount?: number | null;
}) {
  if (quality === null) {
    return <span className="evidence-badge is-empty">가격 근거 없음</span>;
  }
  const suppliers = supplierCount ?? count;
  return (
    <span
      className={`evidence-badge ${
        quality === "SINGLE_OBSERVATION" ? "is-single" : "is-multiple"
      }`}
    >
      {`근거 ${count}건 · 공급사 ${suppliers}곳`}
    </span>
  );
}
```

- [ ] **Step 3: 테스트 재실행 — 통과 확인**

Run: `cd frontend && npm.cmd test -- --run src/pages/StandardPricesPage.test.tsx`
Expected: PASS. (`supplierCount`를 아직 아무도 안 넘기므로 `count`로 폴백돼서
"근거 1건 · 공급사 1곳"이 나온다 — `sensor` 픽스처의 `observation_count: 1`과 같은 값.)

- [ ] **Step 4: 커밋**

```bash
git add frontend/src/components/EvidenceBadge.tsx frontend/src/pages/StandardPricesPage.test.tsx
git commit -m "feat(ui): show supplier count in the evidence badge"
```

---

### Task 8: `StandardPricesPage.tsx` — supplier_count 배선 + 필터 라벨

**Files:**
- Modify: `frontend/src/pages/StandardPricesPage.tsx:301-302` (필터 옵션),
  `:566-568` (observationCount 계산), `:597-600` (상세 헤더 배지), `:748-751`
  (버전 이력 배지), `:792-799` (`formatObservationCount`)
- Test: `frontend/src/pages/StandardPricesPage.test.tsx`

- [ ] **Step 1: 실패하는 테스트 먼저 — 목록 테이블에도 공급사 수가 보여야 함**

`frontend/src/pages/StandardPricesPage.test.tsx`의 `sensor` 픽스처(35-65행) 중
`current_version` 앞에 `supplier_count: 1,` 한 줄을 추가할 필요는 없다 (Task 7의 폴백이
이미 처리함). 대신 목록 테이블 셀도 같은 포맷을 쓰는지 검증하는 assert를 98번째
`it(...)` 테스트("renders the standard DB as a grouped price table...") 안, 167행
근처(Task 7에서 이미 고친 줄) 다음 줄에 추가:

```typescript
  expect(
    screen.getAllByText("1건 · 공급사 1곳").length,
  ).toBeGreaterThan(0);
```

Run: `cd frontend && npm.cmd test -- --run src/pages/StandardPricesPage.test.tsx`
Expected: FAIL — 목록 테이블 셀은 아직 `formatObservationCount`가 옛 포맷("1건")을
반환하므로 새 문자열과 안 맞음.

- [ ] **Step 2: `formatObservationCount`에 공급사 수 추가**

`frontend/src/pages/StandardPricesPage.tsx:792-799`(현재):

```typescript
function formatObservationCount(item: StandardItemSummary) {
  if (item.observation_count !== null) {
    return `${item.observation_count.toLocaleString("ko-KR")}건`;
  }
  return item.operational_status === "NO_ELIGIBLE_EVIDENCE"
    ? "근거 없음"
    : "재구축 필요";
}
```

을 다음으로 교체:

```typescript
function formatObservationCount(item: StandardItemSummary) {
  if (item.observation_count !== null) {
    const suppliers = item.supplier_count ?? item.observation_count;
    return `${item.observation_count.toLocaleString("ko-KR")}건 · 공급사 ${suppliers.toLocaleString("ko-KR")}곳`;
  }
  return item.operational_status === "NO_ELIGIBLE_EVIDENCE"
    ? "근거 없음"
    : "재구축 필요";
}
```

- [ ] **Step 3: 테스트 재실행 — 통과 확인**

Run: `cd frontend && npm.cmd test -- --run src/pages/StandardPricesPage.test.tsx`
Expected: PASS.

- [ ] **Step 4: 상세 헤더·버전 이력 배지에 supplierCount 전달**

`frontend/src/pages/StandardPricesPage.tsx:566-568`(현재):

```typescript
  const observationCount = pinned
    ? snapshotVersion?.observation_count ?? 0
    : item.observation_count ?? 0;
```

을 다음으로 교체:

```typescript
  const observationCount = pinned
    ? snapshotVersion?.observation_count ?? 0
    : item.observation_count ?? 0;
  const supplierCount = pinned
    ? snapshotVersion?.supplier_count ?? 0
    : item.supplier_count ?? 0;
```

`:597-600`(현재):

```tsx
        <EvidenceBadge
          quality={evidenceQuality}
          count={observationCount}
        />
```

을 다음으로 교체:

```tsx
        <EvidenceBadge
          quality={evidenceQuality}
          count={observationCount}
          supplierCount={supplierCount}
        />
```

`:748-751`(현재):

```tsx
              <EvidenceBadge
                quality={version.evidence_quality}
                count={version.observation_count}
              />
```

을 다음으로 교체:

```tsx
              <EvidenceBadge
                quality={version.evidence_quality}
                count={version.observation_count}
                supplierCount={version.supplier_count}
              />
```

- [ ] **Step 5: 근거 품질 필터 옵션 라벨을 실제 기준에 맞게 수정**

`frontend/src/pages/StandardPricesPage.tsx:300-303`(현재):

```tsx
            <option value="">전체</option>
            <option value="SINGLE_OBSERVATION">근거 1건</option>
            <option value="MULTI_OBSERVATION">근거 2건 이상</option>
```

을 다음으로 교체:

```tsx
            <option value="">전체</option>
            <option value="SINGLE_OBSERVATION">공급사 1곳</option>
            <option value="MULTI_OBSERVATION">공급사 2곳 이상</option>
```

- [ ] **Step 6: 전체 프론트 테스트 + 빌드 + 린트**

Run:
```bash
cd frontend
call npm.cmd test -- --run
call npm.cmd run lint
call npm.cmd run build
```
Expected: 전부 클린.

- [ ] **Step 7: 커밋**

```bash
git add frontend/src/pages/StandardPricesPage.tsx frontend/src/pages/StandardPricesPage.test.tsx
git commit -m "feat(ui): wire supplier_count through standard-DB list, detail, and filter"
```

---

### Task 9: 전체 검증 + 브라우저 확인 + Push

**Files:** 없음 (검증만)

- [ ] **Step 1: 백엔드 전체 테스트**

Run: `cd backend && ../.venv/Scripts/python.exe -m pytest -q`
Expected: 전체 PASS.

- [ ] **Step 2: 프론트 전체 테스트 + 빌드 + 린트**

Run:
```bash
cd frontend
call npm.cmd test -- --run
call npm.cmd run lint
call npm.cmd run build
```
Expected: 전부 클린.

- [ ] **Step 3: 실행 중인 서버 재기동 후 육안 확인**

통계값이 안 바뀌므로 alembic 마이그레이션·표준 DB 재구축 없이 백엔드만 재기동하면
된다 (`scripts\start-local.bat`가 이미 떠 있는 프론트는 그대로 두고, 이미 떠 있는
백엔드 프로세스만 재시작). 브라우저에서 `/standard-prices`로 이동해:
- 목록 "근거" 컬럼에 "N건 · 공급사 M곳" 형태로 보이는지
- "근거 품질" 필터를 "공급사 1곳"으로 걸었을 때, 같은 문서/공급사에서 나온 반복 항목이
  거기로 옮겨졌는지 (이전엔 "근거 2건 이상"에 있었을 항목)
- 표준 품목 상세의 배지와 "가격 변경 이력"의 배지에도 같은 포맷이 보이는지

확인한다.

- [ ] **Step 4: 커밋 로그 확인 후 push**

Run: `cd "C:\Users\WIA\Desktop\price_analyzer" && git log --oneline -10`
Expected: Task 1~8의 커밋들이 순서대로 보임.

Run: `cd "C:\Users\WIA\Desktop\price_analyzer" && git push personal feature/market-price-integration`
Expected: 정상 push. (이 작업이 다른 브랜치에 있어야 한다면 사용자에게 먼저 확인받는다
— 지금까지의 세션은 `feature/market-price-integration` 브랜치에서 진행되어 왔다.)
