# 구매 목표가 — 편차(대비) 기준 통일 설계

## 배경

"구매 목표가"(TARGET) 탭에서 품목별 "목표가 대비" 컬럼과 하단 합계 행이 서로 다른 기준으로
계산되고 있었다.

- 하단 합계 행(`TargetPriceResults`의 `totalTargetVariance`)은 프론트엔드에서
  `구매금액 합 − 목표금액 합` (금액 기준)으로 별도 계산.
- 품목별 행(`TargetPriceRow`)은 백엔드 `TargetLineResult.variance_amount`를 그대로 표시하는데,
  이 값은 `quote_unit_price − target_unit_price` (개당 기준)으로 계산됨.

합계는 금액 기준, 개별 행은 개당 기준이라 같은 컬럼 안에서 기준이 섞여 보였다. 또한 "구매 목표
단가" 헤더가 개당 값이라는 것을 명시하지 않아 "목표 금액"(총액) 컬럼과 헷갈렸다.

이 프로젝트에서 "목표가"는 궁극적으로 견적 전체의 총 구매금액(개당 단가 × 수량의 합)을 낮추는
협상 목표이므로, 화면에 노출되는 편차는 금액 기준을 기본으로 하고 개당 차액은 참고용 보조 정보로
분리한다.

## 범위

**바뀌는 것**: `backend/app/analysis/target_price.py`의 목표가 산출 로직, 그 값을 노출하는
API 응답/엑셀 수출(`backend/app/api/analysis.py`의 target-price 관련 엔드포인트), 프론트엔드
`QuoteAnalysisPage.tsx`의 "구매 목표가" 탭, 관련 테스트.

**안 바뀌는 것**: "가격 적정성"(THRESHOLD) 탭의 `AnalysisLine.variance_amount/percent`
(`analysis/service.py`), 시장가 판정(`market/service.py`)의 variance, 정제 재평가
(`cleansing/reassess.py`, `cleansing/calculation.py`) — 전부 목표가와 무관한 별개 코드 경로임을
확인함. 표준 DB(품목 기준가 테이블)도 무관, 건드리지 않음.

## 변경 내용

### 1. 계산 로직 — `backend/app/analysis/target_price.py`

`_target_line_from_cpi`(현재 신규 분석이 쓰는 CPI 경로)와 `_target_line`(레거시 PPI 경로,
프로덕션 호출부는 없지만 테스트로 남아있는 동일 반환 구조체 함수) 양쪽 모두 동일하게 수정:

- `variance_amount` / `variance_percent`: 기존 `quote_unit_price − target_unit` 기준을
  `quote_amount − target_amount` (금액 기준)으로 변경.
  - `variance_percent`는 수량이 분자·분모에 동일하게 곱해져 상쇄되므로 개당 기준과 값 자체는
    같지만, 코드는 금액 기준 수식으로 명시적으로 다시 작성한다 (가독성, 향후 유지보수 시 혼동
    방지).
- 신규 필드 `unit_variance_amount` 추가: 기존에 `variance_amount`가 갖고 있던 개당 차액
  (`quote_unit_price − target_unit`)을 이름을 바꿔 보존.
- `TargetLineResult` dataclass에 `unit_variance_amount: Decimal | None` 필드 추가.

### 2. DB / 모델

`QuoteAnalysisLineResult`(`backend/app/analysis/models.py`)에 컬럼
`target_unit_variance_amount` 추가. Alembic 마이그레이션 신규 작성 (0013 다음 버전, nullable
Decimal 컬럼 추가, 백필 없음).

### 3. API / 엑셀 수출 — `backend/app/api/analysis.py`

- 목표가 응답 스키마(라인 ~229-232 부근 `target_unit_price`/`target_amount`/`variance_amount`/
  `variance_percent`가 있는 곳)에 `unit_variance_amount: Decimal | None` 추가.
- 목표가 payload 매핑(라인 ~551-554)에 `unit_variance_amount` 전달 추가.
- 목표가 엑셀 수출(`/api/analysis/runs/{run_id}/target-price-export`, 라인 ~613-635): 헤더에
  "목표가 대비 개당차액" 추가, 값은 `target.target_unit_variance_amount`.

### 4. 프론트엔드 — `frontend/src/pages/QuoteAnalysisPage.tsx`

- 헤더 "구매 목표 단가" → **"구매 목표 단가(개당)"**.
- "목표가 대비" 컬럼: 표시 로직은 그대로 두되, 백엔드가 이제 금액 기준 값을 주므로 별도 코드
  변경 없이 합계 행과 자동으로 정합됨.
- 신규 컬럼 **"목표가 대비(개당)"** 추가: `target.unit_variance_amount`를 금액으로만 표시
  (percent는 금액 기준과 동일한 값이라 중복 표기하지 않음).
- 합계(`tfoot`) 행의 신규 컬럼 칸은 `—` 처리 — 서로 다른 품목의 개당 차액을 단순 합산하는 것은
  의미가 없음.

### 5. 테스트

- `backend/tests/analysis/test_target_price.py`: `_target_line`, `_target_line_from_cpi`
  케이스에 `unit_variance_amount` 검증 추가. 수량이 1이 아닌 케이스(`_matched_line`의
  quantity=2)를 이용해 `variance_amount`가 금액 기준(수량 반영)으로 나오는지 확인하는 어서션
  추가.
- `frontend/src/pages/QuoteAnalysisPage.test.tsx`: 변경된 헤더 텍스트, 신규 컬럼 반영.

### 6. 과거 분석 기록(런) 처리

`QuoteAnalysisRun`/`QuoteAnalysisLineResult`는 견적서를 업로드해 "분석 실행"을 눌렀을 때
생성되는 결과 스냅샷이며, 표준 DB와는 무관하다. 새 마이그레이션으로 추가되는
`target_unit_variance_amount` 컬럼은 기존에 저장된 과거 런에는 `NULL`로 남고 재계산/백필하지
않는다 — immutable 스냅샷 설계를 유지한다. 사용자가 같은 견적서를 다시 "분석 실행"하면 새 런이
생성되며 그 때 새 계산식이 적용된다.

현재 개발 단계에서는 과거 런을 다시 조회할 일이 거의 없고(알고리즘이 안정화된 이후에나 조회
대상이 됨), 지금 단계의 의사결정에 영향 없음을 사용자가 확인함.

## 확인된 비영향 범위

- "가격 적정성"(THRESHOLD) 탭
- 시장가(DeviceMart/Mouser) 조회·판정
- 정제 검토, 정제 재평가
- 표준 DB 구축·매칭
