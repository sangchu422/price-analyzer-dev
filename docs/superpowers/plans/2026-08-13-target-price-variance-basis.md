# 목표가 대비(variance) 계산 기준 통일 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "구매 목표가"(TARGET) 탭에서 편차(대비) 계산을 개당 단가 기준에서 총 구매금액(단가×수량) 기준으로 통일하고, 개당 차액은 별도 필드로 보존해 화면에 참고용으로 노출한다. 아울러 목표가 산정 제외 품목이 있을 때 합계 구매금액과 비교 대상 구매금액이 다른 기준으로 섞이는 문제도 서브토탈 행으로 명시한다.

**Architecture:** 백엔드 `target_price.py`의 두 계산 함수(`_target_line`, `_target_line_from_cpi`)가 `variance_amount`/`variance_percent`를 금액 기준으로 재계산하고 신규 필드 `unit_variance_amount`(개당 차액)를 추가 반환한다. 이 값은 새 DB 컬럼(`target_unit_variance_amount`)에 저장되고, API 응답 스키마·엑셀 수출에 그대로 노출된다. 프론트엔드는 헤더 라벨을 명확히 하고 새 컬럼과 서브토탈 행을 추가한다.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (backend), React + TypeScript + Vitest (frontend), pytest (backend tests).

**설계 근거:** `docs/superpowers/specs/2026-08-13-target-price-variance-basis-design.md` 참고.

---

### Task 1: 계산 로직 수정 — `backend/app/analysis/target_price.py`

**Files:**
- Modify: `backend/app/analysis/target_price.py:105-116` (dataclass), `:663-699` (`_target_line`), `:702-899` (`_target_line_from_cpi`), `:392-410` (`create_analysis_run`의 ORM 매핑)
- Test: `backend/tests/analysis/test_target_price.py`

- [ ] **Step 1: `TargetLineResult` dataclass에 `unit_variance_amount` 필드 추가**

`backend/app/analysis/target_price.py:105-116`을 다음으로 교체:

```python
@dataclass(frozen=True)
class TargetLineResult:
    raw_item_id: int
    status: str
    target_unit_price: Decimal | None
    target_amount: Decimal | None
    variance_amount: Decimal | None
    variance_percent: Decimal | None
    used_observation_count: int
    excluded_observation_count: int
    reason: str
    evidence: tuple[TargetEvidenceResult, ...]
    unit_variance_amount: Decimal | None = None
```

(마지막에 기본값 `None`을 가진 필드로 추가 — 기존 9곳의 `TargetLineResult(...)` 포지셔널 생성 호출은 전부 그대로 두면 자동으로 `None`이 채워짐. AVAILABLE 상태를 반환하는 2곳만 아래에서 keyword로 실제 값을 넘긴다.)

- [ ] **Step 2: 실패하는 테스트부터 작성 — 레거시 PPI 경로(`_target_line`)**

`backend/tests/analysis/test_target_price.py`의 `test_target_price_uses_only_source_confirmed_exact_dates` 함수(라인 90-108)를 다음으로 교체(끝에 두 개 assert 추가):

```python
def test_target_price_uses_only_source_confirmed_exact_dates() -> None:
    result = _target_line(
        _matched_line(),
        (
            _observation(1, "SOURCE_CONFIRMED"),
            _observation(2, "REFERENCE_BACKFILL"),
        ),
        {"201601": Decimal("80"), "202606": Decimal("120")},
        "202606",
        Decimal("120"),
    )

    assert result.status == "AVAILABLE"
    assert result.target_unit_price == Decimal("150.000000")
    assert result.target_amount == Decimal("300.000000")
    assert result.variance_amount == Decimal("60.000000")
    assert result.unit_variance_amount == Decimal("30.000000")
    assert result.variance_percent == Decimal("20.000000")
    assert result.used_observation_count == 1
    assert result.excluded_observation_count == 1
    assert result.evidence[0].source_period == "201601"
```

`_matched_line()`(같은 파일 24-57행)의 `quantity=Decimal("2")`, `quote_unit_price=Decimal("180")`, `quote_amount=Decimal("360")`을 이용한 검산: `target_unit=150`, `target_amount=150*2=300`. 새 `variance_amount`(금액 기준)는 `360-300=60`, 새 `unit_variance_amount`(개당 기준, 기존 `variance_amount`가 가졌던 값)는 `180-150=30`. `variance_percent`는 수량이 상쇄되어 기존과 동일한 `20.000000`(=60/300*100=30/150*100).

- [ ] **Step 3: 테스트 실행 — 실패 확인**

Run: `cd backend && ..\.venv\Scripts\python.exe -m pytest tests/analysis/test_target_price.py::test_target_price_uses_only_source_confirmed_exact_dates -v`
Expected: FAIL — `AttributeError: 'TargetLineResult' object has no attribute 'unit_variance_amount'` 또는 `assert Decimal('30.000000') == Decimal('40.000000')`류 값 불일치.

- [ ] **Step 4: `_target_line` 계산 수정**

`backend/app/analysis/target_price.py:689-699`을 다음으로 교체:

```python
    target_unit = Decimal(str(median([item.adjusted_unit_price for item in evidence]))).quantize(MONEY_QUANTUM)
    target_amount = None if line.quantity is None else (target_unit * line.quantity).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    unit_variance_amount = None
    variance_amount = None
    variance_percent = None
    if line.quote_unit_price is not None:
        unit_variance_amount = (line.quote_unit_price - target_unit).quantize(MONEY_QUANTUM)
    if line.quote_amount is not None and target_amount is not None:
        variance_amount = (line.quote_amount - target_amount).quantize(MONEY_QUANTUM)
        if target_amount != 0:
            variance_percent = (variance_amount / target_amount * Decimal("100")).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    reason = f"원본 날짜가 확인된 과거 단가 {len(evidence)}건을 {target_period[:4]}년 {int(target_period[4:])}월 물가 수준으로 보정했습니다."
    if len(evidence) == 1:
        reason += " 근거가 1건이므로 신뢰도가 낮습니다."
    return TargetLineResult(
        line.raw_item_id, "AVAILABLE", target_unit, target_amount, variance_amount, variance_percent,
        len(evidence), excluded, reason, tuple(evidence),
        unit_variance_amount=unit_variance_amount,
    )
```

- [ ] **Step 5: 테스트 실행 — 통과 확인**

Run: `cd backend && ..\.venv\Scripts\python.exe -m pytest tests/analysis/test_target_price.py::test_target_price_uses_only_source_confirmed_exact_dates -v`
Expected: PASS

- [ ] **Step 6: CPI 경로(`_target_line_from_cpi`)에도 동일 검증 테스트 추가**

`backend/tests/analysis/test_target_price.py`의 `test_cpi_target_compounds_confirmed_annual_rates_and_rounds_to_krw` 함수(라인 156-181)를 다음으로 교체(끝에 두 개 assert 추가):

```python
def test_cpi_target_compounds_confirmed_annual_rates_and_rounds_to_krw() -> None:
    result = _target_line_from_cpi(
        _matched_line(),
        (
            _observation(
                1,
                "SOURCE_CONFIRMED",
                unit_price=Decimal("1000000"),
            ),
        ),
        _cpi_rates_2017_to_2025(),
        "2025",
        77,
    )

    assert result.status == "AVAILABLE"
    assert result.target_unit_price == Decimal("1216544")
    assert result.target_amount == Decimal("2433088")
    assert result.variance_amount == Decimal("-2432728")
    assert result.unit_variance_amount == Decimal("-1216364")
    assert result.evidence[0].source_period == "2016"
    assert result.evidence[0].inflation is not None
    assert result.evidence[0].inflation.sync_run_id == 77
    assert result.evidence[0].inflation.factor == Decimal("1.216544")
    assert result.evidence[0].inflation.cumulative_percent == Decimal("21.654370")
    assert [rate.year for rate in result.evidence[0].inflation.annual_rates] == [
        str(year) for year in range(2017, 2026)
    ]
```

검산: `_matched_line()`의 `quote_unit_price=180`, `quote_amount=360`(quantity=2). `target_unit=1216544`, `target_amount=2433088`. `variance_amount`(금액 기준) = `360-2433088=-2432728`. `unit_variance_amount`(개당 기준) = `180-1216544=-1216364`.

- [ ] **Step 7: 테스트 실행 — 실패 확인**

Run: `cd backend && ..\.venv\Scripts\python.exe -m pytest tests/analysis/test_target_price.py::test_cpi_target_compounds_confirmed_annual_rates_and_rounds_to_krw -v`
Expected: FAIL — `AttributeError` 또는 값 불일치.

- [ ] **Step 8: `_target_line_from_cpi` 계산 수정**

`backend/app/analysis/target_price.py:856-899`을 다음으로 교체:

```python
    # Price assessment uses the standard-price median as a neutral benchmark.
    # A purchase target instead represents the most aggressive price the
    # company has actually achieved, after putting all observations on the
    # same CPI basis.  Keep every observation for audit, but select the lowest
    # adjusted unit price as the negotiation target.
    evidence.sort(
        key=lambda item: (
            item.adjusted_unit_price,
            -item.quote_date.toordinal(),
            item.raw_item_id,
        )
    )
    target_unit = evidence[0].adjusted_unit_price.quantize(
        KRW_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    target_amount = (
        None
        if line.quantity is None
        else (target_unit * line.quantity).quantize(
            KRW_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
    )
    unit_variance_amount = None
    variance_amount = None
    variance_percent = None
    if line.quote_unit_price is not None:
        unit_variance_amount = (line.quote_unit_price - target_unit).quantize(
            KRW_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
    if line.quote_amount is not None and target_amount is not None:
        variance_amount = (line.quote_amount - target_amount).quantize(
            KRW_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        if target_amount != 0:
            variance_percent = (
                variance_amount / target_amount * Decimal("100")
            ).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    reason = (
        f"원본 날짜가 확인된 과거 단가 {len(evidence)}건을 "
        f"{latest_confirmed_year}년 확정 소비자물가로 보정한 뒤 "
        "가장 낮은 금액을 협상 목표로 채택했습니다."
    )
    if rate_gap_count:
        reason += " 필요한 연간 등락률이 누락된 과거 근거는 계산에서 제외했습니다."
    if len(evidence) == 1:
        reason += " 근거가 1건뿐이므로 협상 시 신뢰도가 낮습니다."
    return TargetLineResult(
        line.raw_item_id,
        "AVAILABLE",
        target_unit,
        target_amount,
        variance_amount,
        variance_percent,
        len(evidence),
        excluded,
        reason,
        tuple(evidence),
        unit_variance_amount=unit_variance_amount,
    )
```

- [ ] **Step 9: 테스트 실행 — 통과 확인**

Run: `cd backend && ..\.venv\Scripts\python.exe -m pytest tests/analysis/test_target_price.py -v`
Expected: 전체 PASS (기존 6개 테스트 + 새 assert 포함).

- [ ] **Step 10: `create_analysis_run`의 ORM 매핑에 새 필드 연결**

`backend/app/analysis/target_price.py:392-410`의 `QuoteAnalysisLineResult(...)` 생성 블록에서 `target_variance_percent=target.variance_percent,` 다음 줄에 아래 한 줄 추가:

```python
        target_variance_percent=target.variance_percent,
        target_unit_variance_amount=target.unit_variance_amount,
        target_used_observation_count=target.used_observation_count,
```

(이 시점에는 `QuoteAnalysisLineResult` 모델에 아직 `target_unit_variance_amount` 컬럼이 없으므로 Task 3 완료 전까지는 이 줄이 `TypeError: invalid keyword argument`로 실패한다 — Task 3와 이어서 진행.)

- [ ] **Step 11: 커밋**

```bash
git add backend/app/analysis/target_price.py backend/tests/analysis/test_target_price.py
git commit -m "fix(target-price): compute variance on purchase-amount basis

목표가 대비(variance)를 개당 단가 기준에서 총 구매금액(단가x수량)
기준으로 통일. 기존 개당 차액은 unit_variance_amount로 보존."
```

(Step 10에서 만든 참조는 Task 3에서 컬럼을 추가해야 실제로 동작하므로, 이 커밋 시점엔 `target_price.py`의 `create_analysis_run` 변경분을 함께 커밋하되 Task 3까지 마친 뒤 실행 검증한다. 커밋 자체는 순서상 문제 없음 — Python은 런타임에만 실패함.)

---

### Task 2: DB 컬럼 + 모델 — `backend/app/analysis/models.py`, Alembic 마이그레이션

**Files:**
- Modify: `backend/app/analysis/models.py:149-150`
- Create: `backend/alembic/versions/0014_target_unit_variance_amount.py`

- [ ] **Step 1: 모델에 컬럼 추가**

`backend/app/analysis/models.py:149-150`을 다음으로 교체:

```python
    target_variance_amount: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    target_variance_percent: Mapped[Decimal | None] = mapped_column(ExactDecimal())
    target_unit_variance_amount: Mapped[Decimal | None] = mapped_column(ExactDecimal())
```

- [ ] **Step 2: 마이그레이션 파일 생성**

`backend/alembic/versions/0014_target_unit_variance_amount.py` 새로 작성:

```python
"""Add target_unit_variance_amount for per-unit reference variance.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quote_analysis_line_result",
        sa.Column("target_unit_variance_amount", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("quote_analysis_line_result", "target_unit_variance_amount")
```

- [ ] **Step 3: 마이그레이션 적용**

Run: `cd backend && ..\.venv\Scripts\python.exe -m alembic upgrade head`
Expected: `Running upgrade 0013 -> 0014, Add target_unit_variance_amount for per-unit reference variance.` 출력, 에러 없음.

- [ ] **Step 4: Task 1에서 미뤄뒀던 테스트 전체 재실행 — 통과 확인**

Run: `cd backend && ..\.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: 전체 PASS. (Task 1 Step 10에서 추가한 `target_unit_variance_amount=target.unit_variance_amount` 키워드가 이제 실제 컬럼과 매칭되어 정상 동작.)

- [ ] **Step 5: 커밋**

```bash
git add backend/app/analysis/models.py backend/alembic/versions/0014_target_unit_variance_amount.py
git commit -m "feat(db): add target_unit_variance_amount column

과거 런은 NULL로 남고 새로 '분석 실행'한 런부터 값이 채워짐
(immutable 스냅샷 설계 유지, 백필 없음)."
```

---

### Task 3: API 응답 스키마 + 엑셀 수출 — `backend/app/api/analysis.py`

**Files:**
- Modify: `backend/app/api/analysis.py:219-236` (`TargetLineResponse`), `:547-561` (`get_analysis_run`의 target_lines 매핑), `:613-635` (엑셀 수출)

- [ ] **Step 1: `TargetLineResponse` 스키마에 필드 추가**

`backend/app/api/analysis.py:219-236`을 다음으로 교체:

```python
class TargetLineResponse(BaseModel):
    raw_item_id: int
    status: Literal[
        "AVAILABLE",
        "DATE_UNAVAILABLE",
        "INDEX_UNAVAILABLE",
        "RATE_GAP",
        "MARKET_REFERENCE_REQUIRED",
        "NOT_APPLICABLE",
    ]
    target_unit_price: Decimal | None
    target_amount: Decimal | None
    variance_amount: Decimal | None
    variance_percent: Decimal | None
    unit_variance_amount: Decimal | None = None
    used_observation_count: int
    excluded_observation_count: int
    reason: str
    evidence: list[TargetEvidenceResponse]
```

(`AnalysisRunResponse`와 `StoredAnalysisRunResponse` 둘 다 이 클래스를 `target_lines: list[TargetLineResponse]`로 재사용하므로, 이 한 곳만 고치면 두 엔드포인트 모두 반영된다 — `api/analysis.py:286`, `:304` 확인 완료.)

- [ ] **Step 2: `get_analysis_run`(과거 런 조회)의 dict 매핑에 필드 추가**

`backend/app/api/analysis.py:547-561`을 다음으로 교체:

```python
        "target_lines": [
            {
                "raw_item_id": line.raw_item_id,
                "status": line.target_status,
                "target_unit_price": line.target_unit_price,
                "target_amount": line.target_amount,
                "variance_amount": line.target_variance_amount,
                "variance_percent": line.target_variance_percent,
                "unit_variance_amount": line.target_unit_variance_amount,
                "used_observation_count": line.target_used_observation_count,
                "excluded_observation_count": line.target_excluded_observation_count,
                "reason": line.target_reason,
                "evidence": evidence_by_line.get(line.id, []),
            }
            for line in lines
        ],
```

(라이브 생성 경로 `_analysis_run_payload`, `api/analysis.py:796-809`는 `line.__dict__`를 그대로 스프레드하므로 Task 1에서 dataclass에 `unit_variance_amount`를 추가한 시점에 이미 자동으로 포함됨 — 별도 수정 불필요.)

- [ ] **Step 3: 엑셀 수출에 컬럼 추가**

`backend/app/api/analysis.py:613-635`을 다음으로 교체:

```python
    headers = [
        "품명", "규격", "단위", "수량", "개당 단가", "구매 금액",
        "구매 목표 단가(개당)", "목표 금액", "목표가 대비 금액", "목표가 대비 비율(%)",
        "목표가 대비 개당차액",
        "산정 상태",
    ]
    rows = []
    for line in lines:
        target = target_by_raw_item_id.get(line.raw_item_id)
        rows.append([
            line.item_name or "",
            line.spec or "",
            line.unit or "",
            line.quantity,
            line.quote_unit_price,
            line.quote_amount,
            target.target_unit_price if target else None,
            target.target_amount if target else None,
            target.target_variance_amount if target else None,
            target.target_variance_percent if target else None,
            target.target_unit_variance_amount if target else None,
            _TARGET_STATUS_LABELS.get(target.target_status, target.target_status)
            if target
            else "—",
        ])
    return build_xlsx_response(
        sheet_title="구매 목표가 분석 결과",
        headers=headers,
        rows=rows,
```

(마지막 줄 `rows=rows,` 다음 줄에 원래 있던 `filename=...` 인자는 그대로 유지 — 이 블록 바로 아래 줄은 건드리지 않는다.)

- [ ] **Step 4: 백엔드 테스트 전체 재실행**

Run: `cd backend && ..\.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: 전체 PASS.

- [ ] **Step 5: 커밋**

```bash
git add backend/app/api/analysis.py
git commit -m "feat(api): expose unit_variance_amount in target-price response and export"
```

---

### Task 4: 프론트엔드 타입 — `frontend/src/api/client.ts`

**Files:**
- Modify: `frontend/src/api/client.ts:618-635`

- [ ] **Step 1: `TargetPriceLine` 인터페이스에 필드 추가**

`frontend/src/api/client.ts:618-635`을 다음으로 교체:

```typescript
export interface TargetPriceLine {
  raw_item_id: number;
  status:
    | "AVAILABLE"
    | "DATE_UNAVAILABLE"
    | "INDEX_UNAVAILABLE"
    | "RATE_GAP"
    | "MARKET_REFERENCE_REQUIRED"
    | "NOT_APPLICABLE";
  target_unit_price: string | null;
  target_amount: string | null;
  variance_amount: string | null;
  variance_percent: string | null;
  unit_variance_amount?: string | null;
  used_observation_count: number;
  excluded_observation_count: number;
  reason: string;
  evidence: TargetPriceEvidence[];
}
```

- [ ] **Step 2: 커밋**

```bash
git add frontend/src/api/client.ts
git commit -m "feat(types): add unit_variance_amount to TargetPriceLine"
```

---

### Task 5: 프론트엔드 UI — `frontend/src/pages/QuoteAnalysisPage.tsx`

**Files:**
- Modify: `frontend/src/pages/QuoteAnalysisPage.tsx:733-772` (표 헤더/본문/합계)

- [ ] **Step 1: 헤더 라벨 변경 + 신규 컬럼 헤더 추가**

`frontend/src/pages/QuoteAnalysisPage.tsx:733-744`을 다음으로 교체:

```jsx
        <table className="analysis-result-table target-price-table" aria-label="구매 목표가 품목별 산정">
          <thead>
            <tr>
              <th>품목 / 사양</th>
              <th>수량</th>
              <th>개당 단가</th>
              <th>구매 금액</th>
              <th>구매 목표 단가(개당)</th>
              <th>목표 금액</th>
              <th>목표가 대비</th>
              <th>목표가 대비(개당)</th>
              <th>산정 근거</th>
            </tr>
          </thead>
```

- [ ] **Step 2: 본문 행에 신규 셀 추가**

`frontend/src/pages/QuoteAnalysisPage.tsx:817-824`(TargetPriceRow 내부)을 다음으로 교체:

```jsx
      <td className="numeric">{formatMoney(line?.quote_unit_price ?? null)}</td>
      <td className="numeric">{formatMoney(line?.quote_amount ?? null)}</td>
      <td className="numeric target-unit-price">{formatMoney(target.target_unit_price)}</td>
      <td className="numeric">{formatMoney(target.target_amount)}</td>
      <td className="numeric">
        <strong>{formatSignedMoney(target.variance_amount)}</strong>
        <span>{formatSignedPercent(target.variance_percent)}</span>
      </td>
      <td className="numeric">{formatSignedMoney(target.unit_variance_amount ?? null)}</td>
```

- [ ] **Step 3: 합계(tfoot)에 서브토탈 행 추가 + 신규 컬럼 칸 추가**

`frontend/src/pages/QuoteAnalysisPage.tsx:757-769`을 다음으로 교체:

```jsx
        <tfoot>
          <tr className="target-covered-quote-row">
            <td colSpan={3}>산정 대상 구매금액(목표가 있는 품목만)</td>
            <td className="numeric"><strong>{formatMoney(String(targetCoveredQuoteAmount))}</strong></td>
            <td className="numeric">—</td>
            <td className="numeric">—</td>
            <td className="numeric">—</td>
            <td className="numeric">—</td>
            <td aria-label="산정 대상 구매금액 서브토탈">—</td>
          </tr>
          <tr className="target-total-row">
            <td colSpan={3}>합계</td>
            <td className="numeric"><strong>{formatMoney(String(totalQuoteAmount))}</strong></td>
            <td className="numeric"><strong>{formatMoney(numberString(totalTargetUnitPrice))}</strong></td>
            <td className="numeric"><strong>{formatMoney(numberString(totalTargetAmount))}</strong></td>
            <td className="numeric">
              <strong>{formatSignedMoney(numberString(totalTargetVariance))}</strong>
              <span>{formatSignedPercent(numberString(totalTargetVariancePercent))}</span>
            </td>
            <td className="numeric">—</td>
            <td aria-label="합계 산정 근거 없음">—</td>
          </tr>
        </tfoot>
```

(`targetCoveredQuoteAmount`는 `TargetPriceResults` 컴포넌트에 이미 정의돼 있음 — `QuoteAnalysisPage.tsx:654-661`. 새 코드 불필요, 기존 변수를 tfoot에서 참조만 하면 됨.)

- [ ] **Step 4: 프론트 타입 체크**

Run: `cd frontend && npm.cmd run build`
Expected: 타입 에러 없이 빌드 성공 (TargetPriceRow의 `target.unit_variance_amount` 참조가 Task 4에서 추가한 타입과 일치하는지 확인).

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/pages/QuoteAnalysisPage.tsx
git commit -m "feat(ui): clarify target-price labels, add unit-variance column and covered-amount subtotal"
```

---

### Task 6: 프론트엔드 테스트 갱신 — `frontend/src/pages/QuoteAnalysisPage.test.tsx`

**Files:**
- Modify: `frontend/src/pages/QuoteAnalysisPage.test.tsx:116-159` (target_lines 픽스처), `:264-286` (TARGET 탭 assertion)

배경: 이 파일의 `target_lines` 픽스처는 백엔드를 실제로 실행하지 않고 "백엔드가 이렇게 응답한다"고 가정한 목(mock) 데이터다. Task 1~3에서 백엔드 계산 기준이 바뀌었으므로, 이 목 데이터도 새 계산 결과와 일치하도록 갱신해야 정직한 테스트가 된다. `_matched_line`류 헬퍼가 아니라 raw fixture이므로 직접 손으로 계산한다.

**검산 (`line()` 헬퍼 기본값 기준, `quantity: "2.000000"`, `quote_unit_price: "130.000000"`, `quote_amount: "260.000000"`, `target_unit_price: "90.000000"`, `target_amount: "180.000000"`):**
- 개당 차액(`unit_variance_amount`, 신규) = `130 - 90 = 40`
- 금액 차액(`variance_amount`, 기존 필드지만 이제 금액 기준) = `260 - 180 = 80`
- `variance_percent`는 수량이 상쇄되어 기존 값 `44.444444`과 동일 (변경 없음)
- 서브토탈 "산정 대상 구매금액" = AVAILABLE인 7개 라인(`raw_item_id` 1~7)의 `quote_amount` 합 = `260 × 7 = 1820`

- [ ] **Step 1: 픽스처 갱신**

`frontend/src/pages/QuoteAnalysisPage.test.tsx:116-124`을 다음으로 교체:

```typescript
  target_lines: Array.from({ length: 9 }, (_, index) => ({
    raw_item_id: index + 1,
    status: index < 7 ? "AVAILABLE" : "DATE_UNAVAILABLE",
    target_unit_price: index < 7 ? "90.000000" : null,
    target_amount: index < 7 ? "180.000000" : null,
    variance_amount: index < 7 ? "80.000000" : null,
    variance_percent: index < 7 ? "44.444444" : null,
    unit_variance_amount: index < 7 ? "40.000000" : null,
    used_observation_count: index < 7 ? 2 : 0,
    excluded_observation_count: index < 7 ? 0 : 2,
```

- [ ] **Step 2: TARGET 탭 assertion에 신규 검증 추가**

`frontend/src/pages/QuoteAnalysisPage.test.tsx:264-271`을 다음으로 교체:

```typescript
  await user.click(screen.getByRole("tab", { name: /구매 목표가/ }));
  expect(
    screen.getByRole("columnheader", { name: "구매 목표 단가(개당)" }),
  ).toBeVisible();
  expect(
    screen.getByRole("columnheader", { name: "목표가 대비(개당)" }),
  ).toBeVisible();
  const coveredRow = document.querySelector(".target-covered-quote-row");
  expect(coveredRow).not.toBeNull();
  expect(coveredRow).toHaveTextContent("1,820원");
  const totalRow = document.querySelector(".target-total-row");
  expect(totalRow).not.toBeNull();
  expect(totalRow).toHaveTextContent("2,080원");
  expect(totalRow).toHaveTextContent("630원");
  expect(totalRow).toHaveTextContent("1,260원");
  expect(totalRow).toHaveTextContent("+560원");
  expect(totalRow).toHaveTextContent("(+44.44%)");
```

(그 아래 `expect(screen.queryByText("물가보정 기준")).not.toBeInTheDocument();`부터 이어지는 나머지 라인은 그대로 둔다.)

- [ ] **Step 3: 프론트 테스트 실행 — 통과 확인**

Run: `cd frontend && npm.cmd test -- --run src/pages/QuoteAnalysisPage.test.tsx`
Expected: 전체 PASS.

- [ ] **Step 4: 프론트 테스트 전체 스위트 실행 (회귀 확인)**

Run: `cd frontend && npm.cmd test -- --run`
Expected: 전체 PASS. (다른 파일이 `TargetPriceLine` 타입/픽스처를 공유하지 않는지 확인 — 실패하면 어떤 파일인지 보고 동일한 방식으로 갱신.)

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/pages/QuoteAnalysisPage.test.tsx
git commit -m "test(ui): update target-price fixture and assertions for amount-basis variance"
```

---

### Task 7: 전체 검증 + 브라우저 확인

**Files:** 없음 (검증만)

- [ ] **Step 1: 백엔드 전체 테스트**

Run: `cd backend && ..\.venv\Scripts\python.exe -m pytest -q`
Expected: 전체 PASS, 실패 0건.

- [ ] **Step 2: 프론트 전체 테스트 + 빌드 + 린트**

Run:
```bash
cd frontend
call npm.cmd test -- --run
call npm.cmd run lint
call npm.cmd run build
```
Expected: 셋 다 에러 없이 통과.

- [ ] **Step 3: 실제 앱 기동 후 육안 확인**

이미 떠 있는 서버가 있다면 재기동(`scripts\start-local.bat` 재실행 시 alembic upgrade가 자동으로 0014까지 적용됨). 브라우저에서 "신규 견적 분석" → 아무 견적 업로드 → "구매 목표가" 탭 클릭 →
- 헤더가 "구매 목표 단가(개당)" / "목표가 대비(개당)"로 보이는지
- 표 하단에 "산정 대상 구매금액(목표가 있는 품목만)" 행이 "합계" 행 위에 추가로 보이는지
- 목표가 산정된 품목 행의 "목표가 대비" 금액이 "구매 금액 − 목표 금액"과 일치하는지 손으로 한 줄 검산

확인 후 스크린샷으로 남긴다.

- [ ] **Step 4: 최종 커밋 없음 — Task 1~6에서 이미 커밋 완료. push만 남음.**

---

### Task 8: Push

**Files:** 없음

- [ ] **Step 1: 커밋 로그 확인**

Run: `cd "C:\Users\WIA\Desktop\price_analyzer" && git log --oneline -10`
Expected: Task 1~6의 6개 커밋 + 이전 설계 문서 커밋 2개가 순서대로 보임.

- [ ] **Step 2: personal 원격으로 push**

Run: `cd "C:\Users\WIA\Desktop\price_analyzer" && git push personal feature/market-price-integration`
Expected: 정상 push, reject 없음. (다른 사람이 같은 브랜치에 push했다면 `git pull --rebase personal feature/market-price-integration` 먼저 필요 — 이 경우 사용자에게 알리고 확인받는다.)
