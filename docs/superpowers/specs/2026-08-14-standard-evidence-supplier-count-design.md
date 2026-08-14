# 표준 DB 근거 신뢰도 판정 — supplier_count 기반 전환 — Design

## 배경

표준 DB 화면에서 "근거 2건"으로 표시되는 품목의 상당수(공급사 기준 97.8%, 41,070개 중
observation_count=2인 10,145개 샘플 조사)가 실제로는 **같은 공급사**에서 나온 근거였다.
그중 46%는 아예 같은 물리 파일(같은 문서)의 다른 시트/행에서 나온 값이었다 — 프로젝트
견적서가 여러 설비를 한 워크북의 여러 시트(견적서1~4 등)로 담고, 같은 표준부품(베어링,
센서 등)이 여러 설비에 공통으로 들어가면 같은 공급사가 같은 단가로 여러 시트에 반복
기재하기 때문이다.

`backend/app/pricing/service.py`의 `_source_copy_key`/`_semantic_source_identity`를
조사한 결과, 이 행 단위 관측치 보존은 **의도된 설계**임을 확인했다 (커밋
`00c977fc`: "중복 원본 계보를 보존한다", 코드 주석: "a changed value must remain an
independent observation rather than an inferred revision"). 감사 추적 관점에서 각 행을
독립 기록으로 남기는 것은 맞다. 문제는 이 행 개수(`observation_count`)를 그대로
"근거 신뢰도"(SINGLE_OBSERVATION vs MULTI_OBSERVATION) 판정에 쓰고 있다는 점이다 — 같은
공급사의 반복 기재는 가격을 독립적으로 재확인해주지 않는데도 "근거가 여러 건이라 더
믿을 만하다"는 인상을 준다.

이미 `StandardPriceVersion.supplier_count`(서로 다른 공급사 수, `_draft_from_evidence_rows`
에서 계산되어 저장됨)가 정확히 필요한 값을 갖고 있었지만, 표준 DB 탐색기 화면과
`/analysis` 화면 어디에도 노출되지 않고 있었다.

## 목표

- "근거 품질"(SINGLE_OBSERVATION / MULTI_OBSERVATION) 판정 기준을 `observation_count`에서
  `supplier_count`로 바꾼다.
- 표준 DB 화면에 공급사 수를 노출해 사용자가 "근거 2건"이 독립적 확인인지 같은 공급사
  반복인지 구분할 수 있게 한다.
- `/analysis` 화면의 "신뢰도 낮음" 표시도 동일 기준으로 정확하게 만든다.

## 비목표

- 표준단가 통계(min/median/average/max) 계산 로직 변경 — 그대로 둔다. 저장된
  `StandardPriceVersion` 값 자체는 바뀌지 않으므로 **표준 DB 재구축이 필요 없다**.
- `_source_copy_key` 등 관측치 보존(행 단위 감사 추적) 로직 변경 — 의도된 설계이므로
  그대로 둔다.
- `backend/app/analysis/target_price.py`의 "근거 1건이므로 신뢰도가 낮습니다"(구매
  목표가 전용, 별도의 필터링된 관측치 집합 기준) — 다른 개념이라 범위 밖.

## 설계

### 판정 기준 전환

`backend/app/standard_database/read_service.py`:

```python
def evidence_quality(supplier_count: int) -> EvidenceQuality:
    if supplier_count <= 1:
        return EvidenceQuality.SINGLE_OBSERVATION
    return EvidenceQuality.MULTI_OBSERVATION
```

호출부 3곳 모두 `observation_count` 대신 `supplier_count`를 넘기도록 바꾼다:
1. `StandardExplorerSummary.evidence_quality` 프로퍼티
2. `list_standard_explorer_items()`의 SINGLE_OBSERVATION/MULTI_OBSERVATION 필터 분기 2곳

`supplier_count`는 `StandardPriceVersion`에 이미 NOT NULL로 저장되어 있으므로 추가 계산이
필요 없다.

### API 응답에 supplier_count 노출

`backend/app/api/catalog.py`:
- `StandardItemSummaryResponse`에 `supplier_count: int | None` 필드 추가
- `StandardEvidenceResponse`에 `supplier_count: int` 필드 추가
- 두 응답 생성부(약 655행, 960행 부근)에서 `price.supplier_count`를 채워 넣는다

### 중복 로직 제거 — `/analysis` 화면도 같은 기준 적용

`backend/app/analysis/service.py` 767~771행은 `read_service.evidence_quality()`와 같은
판정을 인라인으로 중복 구현하고 있다:

```python
evidence_quality=(
    "SINGLE_OBSERVATION"
    if price.observation_count == 1
    else "MULTI_OBSERVATION"
),
```

이걸 지우고 `read_service.evidence_quality(price.supplier_count).value`를 재사용한다.
이렇게 하면 코드 중복이 없어지고, `/analysis` 화면에서 이미 쓰고 있는 "신뢰도 낮음"
표시(`QuoteAnalysisPage.tsx:1073`)도 자동으로 같은 기준을 따르게 된다. `근거 {N}건` 라벨의
N(=`standard_observation_count`)은 그대로 두고, "· 신뢰도 낮음"이 뜨는 조건만 바뀐다.

### 프론트엔드

`frontend/src/api/client.ts`:
- `StandardItemSummary`, `StandardEvidence` 인터페이스에 `supplier_count: number | null`
  (또는 `StandardEvidence`는 `number`, 항상 존재) 추가

`frontend/src/pages/StandardPricesPage.tsx`:
- 목록 테이블 "근거" 컬럼: 기존 `observation_count`건 표시에 공급사 수를 덧붙인다
  (예: `2건 · 공급사 1곳`)
- "근거 품질" 필터(`<select aria-label="근거 품질">`) 옵션 라벨을 실제 판정 기준에 맞게
  변경: `근거 1건` → `공급사 1곳`, `근거 2건 이상` → `공급사 2곳 이상`
- 원본 근거 상세 테이블(관측치별 목록)은 이미 행마다 공급사명을 보여주므로 구조 변경
  없음

`/analysis` 화면(`QuoteAnalysisPage.tsx`)은 코드 변경 없음 — 판정 기준이 백엔드에서
바뀌므로 "· 신뢰도 낮음" 조건만 자동으로 정확해진다.

## 테스트

- `backend/tests/api/test_standard_database_explorer_api.py`: SINGLE/MULTI 필터·
  `evidence_quality` 응답 값을 `supplier_count` 기준 픽스처로 갱신, 같은 공급사 2행 →
  SINGLE_OBSERVATION이 되는 케이스 추가
- `backend/tests/api/test_pricing_api.py`: `evidence_quality` 관련 어서션 확인·갱신
- `backend/tests/analysis/` 관련 테스트: `/analysis` 라인의 `evidence_quality`가
  `supplier_count` 기준으로 나오는지 확인하는 케이스 추가 (같은 공급사 2건 → 신뢰도
  낮음 유지되는지)
- 프론트 `StandardPricesPage.test.tsx`: 목록 근거 컬럼에 공급사 수 표시, 필터 라벨 변경
  반영
- 전체 스위트: `pytest -q`, `npm test -- --run`, `npm run lint`, `npm run build`

## 배포

통계값이 바뀌지 않으므로 `alembic upgrade`나 표준 DB 재구축(`--refresh-data`) 없이 배포
가능하다. 순수 백엔드 판정 로직 + API 필드 추가 + 프론트 표시 변경.
