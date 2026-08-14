# 시장가 판정 신뢰성 통일 설계

## 배경

GitHub 이슈 감사(#6, #7, 그리고 #12 통합 체크리스트의 "수동/자동 시장가 조회 모수 불일치" 항목)에서
확인된 세 문제는 전부 `backend/app/market/service.py`의 `MarketLookupService.lookup()` 메서드
하나의 서로 다른 부분에서 비롯된다. 별개 스펙으로 나누지 않고 하나로 묶어서 고친다.

1. **전역 고정 임계치 사용** (#6, P0): 표준 DB 매칭 품목은 분석 실행(run)에 저장된
   `review_percent`/`high_percent`를 쓰는데, 시장가 판정은 항상 전역 설정값
   (`price_variance_high_percent`=20%, `price_variance_review_percent`=10%)만 씀. 사용자가
   화면에서 기준을 바꿔도 시장가 품목엔 반영 안 됨.
2. **"주의"(REVIEW) 구간 누락** (#7, P0): `review < |편차| <= high` 구간에서 `if/elif` 분기가
   전부 빠져나가 초기값 `REVIEW_REQUIRED`(판정 대기)로 남는다. "정보 부족"과 "정보는 있지만
   주의 필요"가 뒤섞임.
3. **수동/자동 조회 상품 모수 불일치** (#12 체크리스트, P0 후보): 자동 배치 조회는
   `automatic_price_eligible`(모델번호·제조사 정확 일치, 재고, MOQ 충족) 필터를 통과한 상품만
   min/median/max/편차 계산에 쓰지만, 수동 "실시간 재조회"는 느슨한 매칭만 통과하면 전부 씀 —
   같은 품목인데 조회 방식에 따라 판정이 달라질 수 있다.

## 범위

**바뀌는 것**: `backend/app/analysis/service.py`의 `_assessment` 함수(공개 전환),
`backend/app/market/service.py`의 판정·필터링 로직, `backend/app/market/schemas.py`의
`MarketAssessment`/요청 스키마, `backend/app/api/market.py`의 엔드포인트 시그니처,
`frontend/src/api/client.ts`의 타입과 조회 함수, `frontend/src/pages/QuoteAnalysisPage.tsx`의
호출부·라벨·집계, 관련 테스트.

**안 바뀌는 것**: 표준 DB 매칭 품목의 판정 로직 자체(`_assessment` 호출부는 그대로, 함수만
재사용), 목표가(TARGET) 탭, 정제 검토, 그룹핑 검토, 표준 DB 구축 — 전부 무관.

## 변경 내용

### 1. 판정 함수 공유

`backend/app/analysis/service.py`의 `_assessment(percent, *, review_percent, high_percent)`
(현재 974-988행, `REVIEW` 구간을 이미 올바르게 처리하는 로직)를 앞의 밑줄을 떼어
`assess_variance`로 이름을 바꿔 공개 함수로 전환한다. 내부 호출부(742행 부근)도 새 이름으로
갱신한다.

`backend/app/market/service.py`는 `from app.analysis.service import assess_variance`로 이
함수를 가져와, 183-201행의 자체 HIGH/LOW/WITHIN_RANGE 인라인 분기(REVIEW 구간이 빠진 버전)를
완전히 삭제하고 `assess_variance(variance, review_percent=review_percent, high_percent=high_percent)`
호출로 교체한다. (순환 import 없음 — `analysis/service.py`는 `market` 패키지를 import하지 않음,
반대 방향으로 `api/market.py`가 이미 `analysis.service`를 import 중이라 이 방향 의존은 기존
패턴과 일치.)

`backend/app/market/schemas.py`의 `MarketAssessment` 타입에 `"REVIEW"` 추가:

```python
MarketAssessment = Literal["LOW", "WITHIN_RANGE", "REVIEW", "HIGH", "REVIEW_REQUIRED"]
```

### 2. 임계치를 분석 실행(run)에서 로드

`MarketBatchLookupRequest`(schemas.py)에 필수 필드 추가:

```python
class MarketBatchLookupRequest(BaseModel):
    analysis_run_id: int
    raw_item_ids: list[int] = Field(min_length=1, max_length=100)
    force_refresh: bool = False
```

`POST /lookup/{raw_item_id}` 엔드포인트(`api/market.py`)에 필수 쿼리 파라미터
`analysis_run_id: int = Query(...)` 추가.

두 엔드포인트 모두 `session.get(QuoteAnalysisRun, analysis_run_id)`로 run을 조회해
(없으면 404) `run.review_percent`/`run.high_percent`를 꺼내
`MarketLookupService.lookup_raw_item(..., review_percent=run.review_percent,
high_percent=run.high_percent)`로 전달한다.

`MarketLookupService.lookup_raw_item`/`.lookup()` 시그니처에
`review_percent: Decimal | None = None, high_percent: Decimal | None = None` 파라미터를 추가하고,
`None`이면 기존처럼 `self.settings.price_variance_*`로 폴백한다 (품목·run 문맥이 없는
`precollect` 엔드포인트가 이 폴백 경로를 계속 씀 — `precollect`는 그대로 두고 새 파라미터를
넘기지 않는다).

### 3. 상품 모수 통일 + 죽은 파라미터 제거

`lookup()`의 172-176행:

```python
priced_products = (
    [product for product in products if product.automatic_price_eligible]
    if automatic
    else products
)
```

을 조건 없이 항상 필터링하도록 교체:

```python
priced_products = [
    product for product in products if product.automatic_price_eligible
]
```

이렇게 되면 `automatic` 파라미터는 `lookup()`/`lookup_raw_item()` 안에서 더 이상 어떤
분기에도 쓰이지 않는다 (grep으로 확인 완료 — 이 필터링이 유일한 사용처). 이번 수정이 만든
죽은 매개변수이므로 함께 제거한다: `lookup()`, `lookup_raw_item()`,
`api/market.py`의 `_automatic_lookup()` 세 곳에서 `automatic`/`automatic=True` 인자를 뺀다.
`products`(필터 전 전체 목록)는 그대로 응답에 남아 화면 증빙·상품 목록 표시용으로 계속 쓰인다.

### 4. 프론트엔드

`frontend/src/api/client.ts`:
- `MarketAssessment` 타입에 `"REVIEW"` 추가.
- `lookupMarketPrice(rawItemId, analysisRunId, forceRefresh?, signal?)`,
  `lookupMarketPriceBatch(rawItemIds, analysisRunId, forceRefresh?, signal?)`로 시그니처 변경,
  요청에 `analysis_run_id` 포함.

`frontend/src/pages/QuoteAnalysisPage.tsx`:
- 호출부(1011행 `lookupMarketPrice(line.raw_item_id, forceRefresh)`, 186행 근처
  `lookupMarketPriceBatch(...)`)에 `analysis.run_id` 전달.
- `marketAssessmentLabel`(1315행)과 초기 라벨 맵(28행 근처)에 `REVIEW: "주의"` 추가.
- 집계 함수(`count`/`metrics.review`/`metrics.market`/`metrics.pending`, 1347-1395행 부근)에서
  시장가 `REVIEW` 판정을 "주의" 카운트에 포함하고 "판정 대기"(순수 `REVIEW_REQUIRED`)와
  분리한다.

### 5. 테스트

- `backend/tests/market/test_market_service.py`: 경계값 테스트(정확히 -20%, -10%, +10%, +20%
  및 그 안팎), run마다 다른 `review_percent`/`high_percent`를 넘겼을 때 판정이 달라지는지,
  수동 호출(`automatic` 인자 없이)과 자동 배치 호출이 이제 동일한 `priced_products` 모수를
  쓰는지(같은 상품 집합 넣고 두 경로 결과 비교) 검증.
- `backend/tests/api/test_market_api.py`(있으면) 또는 관련 API 테스트: `analysis_run_id` 없이
  호출 시 422/필수 필드 에러, 존재하지 않는 run_id면 404.
- `frontend/src/pages/QuoteAnalysisPage.test.tsx`: `lookupMarketPrice`/`lookupMarketPriceBatch`
  호출 시 `analysis_run_id`(또는 카멜케이스 매핑) 포함 여부, "REVIEW" 판정의 "주의" 라벨·집계
  반영.

## 확인된 비영향 범위

- 목표가(TARGET) 탭, `target_price.py` — 완전 무관
- 정제 검토, 그룹핑 검토, 표준 DB 구축 — 완전 무관
- `precollect` 엔드포인트 — 폴백 경로 그대로 유지, 동작 변경 없음
