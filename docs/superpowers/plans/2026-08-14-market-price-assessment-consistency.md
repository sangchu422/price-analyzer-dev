# 시장가 판정 신뢰성 통일 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 시장가(DeviceMart·Mouser) 판정이 (1) 표준 DB 판정과 같은 함수로 "주의"(REVIEW) 구간까지
계산하고, (2) 분석 실행(run)에 저장된 개별 임계치를 쓰고, (3) 수동/자동 조회가 같은 상품 모수로
판정하도록 통일한다.

**Architecture:** `analysis/service.py`의 기존 `_assessment` 함수(REVIEW 구간 포함, 이미 검증된
로직)를 공개 함수로 전환해 `market/service.py`가 재사용한다. 시장가 조회 API에
`analysis_run_id`를 필수로 받아 서버가 그 run에 저장된 `review_percent`/`high_percent`를
로드한다. `automatic_price_eligible` 필터를 수동/자동 구분 없이 항상 적용한다(제조사 미기록
품목은 수동 조회도 가격을 못 찾게 되는 것을 포함해, 의도된 트레이드오프로 확정됨).

**Tech Stack:** FastAPI + SQLAlchemy (backend), React + TypeScript + Vitest (frontend), pytest.

**설계 근거:** `docs/superpowers/specs/2026-08-14-market-price-assessment-consistency-design.md`.

---

### Task 1: 판정 함수 공유 — `backend/app/analysis/service.py`, `backend/app/market/service.py`, `backend/app/market/schemas.py`

**Files:**
- Modify: `backend/app/analysis/service.py:742-746` (호출부), `:974-988` (함수 정의)
- Modify: `backend/app/market/service.py:1-33` (import), `:183-201` (판정 로직)
- Modify: `backend/app/market/schemas.py:12` (`MarketAssessment` 타입)

- [ ] **Step 1: `_assessment`를 `assess_variance`로 공개 전환**

`backend/app/analysis/service.py:974`의 `def _assessment(` 을 `def assess_variance(`로 바꾸고,
`:742`의 호출부 `assessment = _assessment(` 을 `assessment = assess_variance(`로 바꾼다. 함수
본문(975-988행)은 그대로 둔다 (이미 REVIEW 구간을 올바르게 처리하는 로직):

```python
def assess_variance(
    percent: Decimal,
    *,
    review_percent: Decimal,
    high_percent: Decimal,
) -> Assessment:
    if percent < -high_percent:
        return "LOW"
    if percent < -review_percent:
        return "REVIEW"
    if percent <= review_percent:
        return "WITHIN_RANGE"
    if percent <= high_percent:
        return "REVIEW"
    return "HIGH"
```

- [ ] **Step 2: 백엔드 전체 테스트 실행 — 통과 확인**

Run: `cd backend && ..\.venv\Scripts\python.exe -m pytest tests/analysis -q`
Expected: 전체 PASS (이름만 바뀌었으므로 동작 변화 없음). `_assessment`를 직접 import하는 곳이
없는지 미리 확인했음 (`grep -rn "_assessment(" backend` 결과 이 두 곳뿐).

- [ ] **Step 3: `MarketAssessment` 타입에 REVIEW 추가**

`backend/app/market/schemas.py:12`:

```python
MarketAssessment = Literal["LOW", "WITHIN_RANGE", "REVIEW", "HIGH", "REVIEW_REQUIRED"]
```

- [ ] **Step 4: `market/service.py`가 공유 함수를 쓰도록 교체**

`backend/app/market/service.py:12-13` 부근 import 블록에 추가:

```python
from app.analysis.service import assess_variance
```

`backend/app/market/service.py:183-201`(현재 아래 코드)을:

```python
        variance = None
        assessment = "REVIEW_REQUIRED"
        if (
            market_model_tokens(query)
            and quote_unit_price is not None
            and middle
            and middle > 0
        ):
            variance = (
                (quote_unit_price - middle) / middle * Decimal("100")
            )
            high = self.settings.price_variance_high_percent
            review = self.settings.price_variance_review_percent
            if variance > high:
                assessment = "HIGH"
            elif variance < -high:
                assessment = "LOW"
            elif abs(variance) <= review:
                assessment = "WITHIN_RANGE"
```

다음으로 교체 (인라인 분기 삭제, 공유 함수 호출로 대체 — `review_percent`/`high_percent`는
Task 2에서 이 메서드 시그니처에 추가할 파라미터를 가리킴, 지금 단계에서는 우선
`self.settings.price_variance_*`를 그대로 지역 변수에 담아 써서 이 스텝만으로도 완결되게
한다):

```python
        variance = None
        assessment = "REVIEW_REQUIRED"
        review = self.settings.price_variance_review_percent
        high = self.settings.price_variance_high_percent
        if (
            market_model_tokens(query)
            and quote_unit_price is not None
            and middle
            and middle > 0
        ):
            variance = (
                (quote_unit_price - middle) / middle * Decimal("100")
            )
            assessment = assess_variance(
                variance,
                review_percent=review,
                high_percent=high,
            )
```

- [ ] **Step 5: 실패하는 테스트부터 — REVIEW 경계값**

`backend/tests/market/test_market_service.py`에 새 테스트 추가 (파일 끝에):

```python
def test_market_assessment_uses_review_band_at_exact_boundaries() -> None:
    from app.market.service import MarketLookupService

    cases = [
        (Decimal("-25"), "LOW"),
        (Decimal("-20"), "LOW"),
        (Decimal("-19.999999"), "REVIEW"),
        (Decimal("-10"), "WITHIN_RANGE"),
        (Decimal("-9.999999"), "WITHIN_RANGE"),
        (Decimal("0"), "WITHIN_RANGE"),
        (Decimal("10"), "WITHIN_RANGE"),
        (Decimal("10.000001"), "REVIEW"),
        (Decimal("20"), "REVIEW"),
        (Decimal("20.000001"), "HIGH"),
        (Decimal("25"), "HIGH"),
    ]
    for percent, expected in cases:
        from app.analysis.service import assess_variance

        assert (
            assess_variance(
                percent,
                review_percent=Decimal("10"),
                high_percent=Decimal("20"),
            )
            == expected
        ), f"{percent}% expected {expected}"
```

(이건 `assess_variance` 자체의 경계값 검증이므로 `_assessment`가 이미 `analysis/service.py`
쪽 테스트에서 검증됐다면 중복일 수 있음 — `backend/tests/analysis/` 안에 `_assessment` 또는
`assess_variance`를 이미 이런 식으로 테스트하는 파일이 있는지 먼저 확인하고, 있으면 이 새
테스트는 생략하고 그 파일을 그대로 재사용/재검증만 한다.)

Run: `cd backend && ..\.venv\Scripts\python.exe -m pytest tests/market/test_market_service.py -v`
(먼저 `assess_variance` import 확인 후 통과할 것 — Step 4까지 끝나면 이 테스트는 처음부터
통과해야 정상이다. 만약 실패한다면 Step 4 구현이 잘못된 것이니 고친다.)

- [ ] **Step 6: 기존 시장가 테스트 재확인**

`backend/tests/market/test_market_service.py::test_generic_family_search_remains_review_required`
(232-251행)를 다시 읽어보고 여전히 통과하는지 확인 — 이 테스트는 `market_model_tokens(query)`가
비어서 애초에 `assess_variance` 호출 자체가 스킵되는 케이스이므로 이번 변경으로 깨지지 않아야
한다. 만약 깨진다면 원인을 파악해 보고한다(BLOCKED로 에스컬레이션, 억지로 테스트값을 맞추지
말 것).

Run: `cd backend && ..\.venv\Scripts\python.exe -m pytest tests/market/ -v`
Expected: 전체 PASS.

- [ ] **Step 7: 커밋**

```bash
git add backend/app/analysis/service.py backend/app/market/service.py backend/app/market/schemas.py backend/tests/market/test_market_service.py
git commit -m "fix(market): share standard-price assessment function, add REVIEW band"
```

---

### Task 2: 임계치를 분석 실행(run)에서 로드 — `backend/app/market/service.py`, `backend/app/market/schemas.py`, `backend/app/api/market.py`

**Files:**
- Modify: `backend/app/market/service.py:61-105` (`lookup_raw_item`, `lookup` 시그니처)
- Modify: `backend/app/market/schemas.py:88-90` (`MarketBatchLookupRequest`)
- Modify: `backend/app/api/market.py:64-116` (두 엔드포인트)

- [ ] **Step 1: `MarketLookupService.lookup`/`lookup_raw_item`에 임계치 파라미터 추가**

`backend/app/market/service.py`의 `lookup_raw_item` 시그니처(61-67행)에
`review_percent: Decimal | None = None, high_percent: Decimal | None = None` 추가하고, 내부에서
`self.lookup(...)` 호출(85-93행)에 그대로 전달:

```python
    def lookup_raw_item(
        self,
        raw_item_id: int,
        *,
        force_refresh: bool = False,
        automatic: bool = False,
        review_percent: Decimal | None = None,
        high_percent: Decimal | None = None,
    ) -> MarketLookupResponse:
        raw_item = self.session.get(RawQuoteItem, raw_item_id)
        if raw_item is None:
            raise MarketLookupError("견적 항목을 찾을 수 없습니다.")
        decision = self.session.scalar(
            select(CleanDecision)
            .where(CleanDecision.raw_item_id == raw_item_id)
            .order_by(CleanDecision.id.desc())
        )
        if decision is None or decision.status is not CleanStatus.INCLUDED:
            raise MarketLookupError("정제가 완료된 포함 항목만 조회할 수 있습니다.")
        query = self._query(
            decision.item_name_norm or raw_item.item_name_raw,
            decision.spec_norm or raw_item.spec_raw,
            decision.maker_norm or raw_item.maker_raw,
        )
        if not query:
            raise MarketLookupError("시장가 검색에 사용할 품명 또는 사양이 없습니다.")
        return self.lookup(
            query,
            quote_unit_price=decision.unit_price,
            quantity=decision.quantity,
            force_refresh=force_refresh,
            raw_item_id=raw_item_id,
            automatic=automatic,
            required_manufacturer=decision.maker_norm,
            review_percent=review_percent,
            high_percent=high_percent,
        )
```

(이번 태스크에서는 `automatic` 파라미터를 손대지 않고 그대로 유지 — `review_percent`/
`high_percent`만 추가한다. `automatic` 제거는 Task 3에서 한 번에 정리한다. 이렇게 순서를
나누는 이유: Task 2와 Task 3은 서로 다른 관심사(임계치 소스 vs 상품 필터 범위)이고, 두
관심사를 한 커밋에 섞으면 나중에 `git blame`/`git revert`로 원인을 추적하기 어려워진다.)

`lookup` 시그니처(95-105행)에도 동일하게 추가:

```python
    def lookup(
        self,
        query: str,
        *,
        quote_unit_price: Decimal | None = None,
        quantity: Decimal | None = None,
        force_refresh: bool = False,
        raw_item_id: int = 0,
        automatic: bool = False,
        required_manufacturer: str | None = None,
        review_percent: Decimal | None = None,
        high_percent: Decimal | None = None,
    ) -> MarketLookupResponse:
```

Task 1에서 만든 지역 변수 할당(`review = self.settings.price_variance_review_percent` /
`high = self.settings.price_variance_high_percent`)을 다음으로 교체 — 파라미터가 있으면 그걸
쓰고, 없으면 기존처럼 설정값 폴백:

```python
        review = (
            self.settings.price_variance_review_percent
            if review_percent is None
            else review_percent
        )
        high = (
            self.settings.price_variance_high_percent
            if high_percent is None
            else high_percent
        )
```

- [ ] **Step 2: 실패하는 테스트 — run별 다른 임계치로 판정이 갈림**

`backend/tests/market/test_market_service.py`에 추가:

```python
def test_market_assessment_uses_caller_supplied_thresholds_not_global_defaults() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(project_root=None, market_evidence_folder="evidence")  # 기본 10/20%
    device = FakeAdapter(MarketSource.DEVICEMART, "115")
    mouser = FakeAdapter(MarketSource.MOUSER, "115")

    with Session(engine, expire_on_commit=False) as session:
        result_default = MarketLookupService(
            session, settings, [device, mouser],
        ).lookup(
            "OMRON E3Z-D61",
            quote_unit_price=Decimal("100"),
        )
        result_custom = MarketLookupService(
            session, settings, [device, mouser],
        ).lookup(
            "OMRON E3Z-D61",
            quote_unit_price=Decimal("100"),
            force_refresh=True,
            review_percent=Decimal("30"),
            high_percent=Decimal("40"),
        )

    # -13% 편차: 기본 10%/20% 기준으론 REVIEW, 완화된 30%/40% 기준으론 WITHIN_RANGE
    assert result_default.variance_percent is not None
    assert result_custom.variance_percent is not None
```

(이 테스트는 실제 계산값을 미리 손으로 정확히 못 박기보다, TDD로 실행해서 나온 실제
`variance_percent`/`assessment` 값을 보고 위 주석의 기대(같은 편차인데 임계치에 따라 판정이
달라짐)와 일치하는지 확인한 뒤, 정확한 `assert result_default.assessment == "..."` /
`assert result_custom.assessment == "..."` 두 줄을 채워 넣는다 — quote_unit_price=100,
market median=115이면 편차는 (100-115)/115*100 ≈ -13.04%이므로 기본 10/20% 기준으론 REVIEW,
30/40% 기준으론 WITHIN_RANGE가 되어야 한다. `market_model_tokens("OMRON E3Z-D61")`가 토큰을
인식하는지도 먼저 확인 — 인식 못 하면 다른 모델형 쿼리 문자열로 바꾼다.)

Run 먼저 실패 확인(`review_percent`/`high_percent` 파라미터가 없어 TypeError), Step 1 구현 후
재실행해 통과 확인.

- [ ] **Step 3: `MarketBatchLookupRequest`에 `analysis_run_id` 추가**

`backend/app/market/schemas.py:88-90`:

```python
class MarketBatchLookupRequest(BaseModel):
    analysis_run_id: int
    raw_item_ids: list[int] = Field(min_length=1, max_length=100)
    force_refresh: bool = False
```

- [ ] **Step 4: API 엔드포인트가 run을 조회해서 임계치를 전달하도록 수정**

`backend/app/api/market.py` 상단 import에 추가:

```python
from app.analysis.models import QuoteAnalysisRun
```

`lookup_market_price`(64-79행)를 다음으로 교체 — `analysis_run_id`를 필수 쿼리 파라미터로
받고, run을 조회해 없으면 404, 있으면 그 임계치를 전달:

```python
@router.post(
    "/lookup/{raw_item_id}",
    response_model=MarketLookupResponse,
)
def lookup_market_price(
    raw_item_id: int,
    analysis_run_id: int = Query(...),
    force_refresh: bool = Query(False),
    session: Session = Depends(get_session),
) -> MarketLookupResponse:
    run = session.get(QuoteAnalysisRun, analysis_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="분석 실행 이력을 찾을 수 없습니다.")
    try:
        return _service(session).lookup_raw_item(
            raw_item_id,
            force_refresh=force_refresh,
            review_percent=run.review_percent,
            high_percent=run.high_percent,
        )
    except MarketLookupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
```

`_automatic_lookup`(82-116행)의 시그니처와 `lookup_market_prices_automatically`(119-177행)를
수정 — run을 한 번만 조회해서 워커에 스레드-세이프하게 전달(review_percent/high_percent는
`Decimal`이라 immutable, 여러 스레드에 값으로 넘기는 건 안전함):

```python
def _automatic_lookup(
    raw_item_id: int,
    force_refresh: bool,
    bind: object,
    review_percent: Decimal,
    high_percent: Decimal,
) -> MarketBatchItemResponse:
    with Session(
        bind=bind,
        autoflush=False,
        expire_on_commit=False,
    ) as worker_session:
        try:
            result = _service(worker_session).lookup_raw_item(
                raw_item_id,
                force_refresh=force_refresh,
                automatic=True,
                review_percent=review_percent,
                high_percent=high_percent,
            )
        except MarketLookupError as exc:
            return MarketBatchItemResponse(
                raw_item_id=raw_item_id,
                status="SOURCE_UNAVAILABLE",
                detail=str(exc),
            )
        detail_by_outcome = {
            "CACHE_HIT": "저장된 시장가 근거를 적용했습니다.",
            "LIVE_HIT": "DeviceMart·Mouser에서 시장가 근거를 수집했습니다.",
            "REFERENCE_ONLY": "유사 상품은 찾았지만 자동 판정 조건을 충족하지 못했습니다.",
            "NO_REFERENCE": "두 출처에서 일치하는 시장가 근거를 찾지 못했습니다.",
            "SOURCE_UNAVAILABLE": "사용 가능한 시장가 출처가 없거나 조회에 실패했습니다.",
        }
        return MarketBatchItemResponse(
            raw_item_id=raw_item_id,
            status=result.outcome,
            detail=detail_by_outcome[result.outcome],
            result=result,
        )


@router.post("/lookup-batch", response_model=MarketBatchLookupResponse)
def lookup_market_prices_automatically(
    request: MarketBatchLookupRequest,
    session: Session = Depends(get_session),
) -> MarketBatchLookupResponse:
    run = session.get(QuoteAnalysisRun, request.analysis_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="분석 실행 이력을 찾을 수 없습니다.")
    raw_ids = list(dict.fromkeys(request.raw_item_ids))
    eligibility = market_lookup_eligibilities(session, raw_ids)
    by_id: dict[int, MarketBatchItemResponse] = {}
    eligible_ids: list[int] = []
    for item in eligibility:
        if item.status == "ELIGIBLE":
            eligible_ids.append(item.raw_item_id)
            continue
        by_id[item.raw_item_id] = MarketBatchItemResponse(
            raw_item_id=item.raw_item_id,
            status=item.status,
            detail=item.detail,
        )
    if eligible_ids:
        bind = session.get_bind()
        max_workers = _market_worker_count(bind, len(eligible_ids))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _automatic_lookup,
                    raw_id,
                    request.force_refresh,
                    bind,
                    run.review_percent,
                    run.high_percent,
                ): raw_id
                for raw_id in eligible_ids
            }
            for future in as_completed(futures):
                raw_id = futures[future]
                try:
                    by_id[raw_id] = future.result()
                except Exception as exc:
                    by_id[raw_id] = MarketBatchItemResponse(
                        raw_item_id=raw_id,
                        status="SOURCE_UNAVAILABLE",
                        detail="시장가 조회 중 일시적인 저장 오류가 발생했습니다. 다시 조회해 주세요.",
                    )
    items = [by_id[raw_id] for raw_id in raw_ids]
    unavailable_statuses = {
        "NO_REFERENCE",
        "SOURCE_UNAVAILABLE",
        "CLEANING_REQUIRED",
        "EXCLUDED",
        "NOT_FOUND",
    }
    return MarketBatchLookupResponse(
        items=items,
        completed=sum(item.status not in unavailable_statuses for item in items),
        unavailable=sum(item.status in unavailable_statuses for item in items),
    )
```

(주석에 있던 SQLite 동시성 설명 코멘트는 그대로 유지 — 위 코드는 로직만 바뀌고 그 주석
내용은 지웠으니, 실제 적용 시 원래 82-116행 사이에 있던 주석(`# Each automatic lookup
persists...`)을 `with ThreadPoolExecutor(...)` 바로 위에 그대로 옮겨 붙인다.)

Need `from decimal import Decimal` import 확인 — `api/market.py`에 이미 있는지 확인하고 없으면
추가.

- [ ] **Step 5: `precollect` 엔드포인트는 그대로 둔다**

`api/market.py`의 `precollect_market_prices`(180-199행)는 수정하지 않는다 — `review_percent`/
`high_percent`를 넘기지 않으므로 `lookup()`의 `None` 폴백(전역 설정값)을 그대로 쓴다.

- [ ] **Step 6: API 레벨 테스트**

`backend/tests/api/test_market_api.py`가 있으면 열어서 기존 테스트가 `analysis_run_id` 없이
호출하는지 확인 — 있다면 전부 실제 `QuoteAnalysisRun`을 먼저 만들고 그 `run_id`를 요청에
포함하도록 고친다 (테스트 DB에 run을 만드는 기존 헬퍼가 있는지 다른 테스트 파일에서 먼저
찾아보고 재사용). 파일이 없으면 이 스텝은 생략하고 스텝 7의 통합 테스트만으로 커버한다.

새 테스트 추가 (파일 있으면 그 파일에, 없으면 새로 만들지 말고 `test_market_service.py`에
API 레이어 대신 서비스 레이어로 동등하게 검증):

```python
def test_lookup_raw_item_without_thresholds_falls_back_to_settings(tmp_path) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(project_root=tmp_path, market_evidence_folder="evidence")
    device = FakeAdapter(MarketSource.DEVICEMART, "100")

    with Session(engine, expire_on_commit=False) as session:
        raw = _raw_item(session)
        result = MarketLookupService(
            session, settings, [device],
        ).lookup_raw_item(raw.id)

    # review_percent/high_percent를 안 넘기면 여전히 동작해야 함 (폴백 확인)
    assert result.raw_item_id == raw.id
```

Run: `cd backend && ..\.venv\Scripts\python.exe -m pytest tests/market/ tests/api/ -q`
Expected: 전체 PASS.

- [ ] **Step 7: 커밋**

```bash
git add backend/app/market/service.py backend/app/market/schemas.py backend/app/api/market.py backend/tests/market/test_market_service.py
git commit -m "feat(market): load review/high percent from the analysis run"
```

---

### Task 3: 상품 모수 통일 + 죽은 `automatic` 파라미터 제거 — `backend/app/market/service.py`, `backend/app/api/market.py`

**Files:**
- Modify: `backend/app/market/service.py:61-105` (`lookup_raw_item`, `lookup`), `:172-176` (필터링)
- Modify: `backend/app/api/market.py` (Task 2에서 만든 `_automatic_lookup`/`lookup_market_prices_automatically`)
- Test: `backend/tests/market/test_market_service.py`

- [ ] **Step 1: 실패하는 테스트 — 수동 호출도 이제 eligible-only 씀**

`backend/tests/market/test_market_service.py`에 추가:

```python
def test_manual_and_automatic_lookup_use_the_same_eligible_only_population() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    settings = Settings(project_root=None, market_evidence_folder="evidence")
    device = FakeAdapter(MarketSource.DEVICEMART, "100")

    with Session(engine, expire_on_commit=False) as session:
        raw = _raw_item(session)  # maker_norm 없음 → 이 상품은 절대 eligible 안 됨
        manual = MarketLookupService(
            session, settings, [device],
        ).lookup_raw_item(raw.id)

    # 제조사가 기록되지 않은 품목은 수동 조회도 이제 median을 못 찾아야 한다
    # (이 트레이드오프는 설계 문서에서 사용자가 확정함)
    assert manual.median_price is None
    assert manual.assessment == "REVIEW_REQUIRED"
```

(`_raw_item` 픽스처가 `maker_norm`을 안 주므로 이 테스트가 바로 원하는 상태를 재현한다. 먼저
실행해서 Step 2 구현 전에는 실패하는지 확인 — 지금 코드는 `automatic` 기본값 `False`라 필터
없이 median을 찾아버리므로 `manual.median_price`가 `Decimal("100")`으로 나와 실패해야 정상.)

- [ ] **Step 2: 항상 eligible-only 필터링**

`backend/app/market/service.py:172-176`(현재):

```python
        priced_products = (
            [product for product in products if product.automatic_price_eligible]
            if automatic
            else products
        )
```

을:

```python
        priced_products = [
            product for product in products if product.automatic_price_eligible
        ]
```

로 교체.

- [ ] **Step 3: 재실행 — 통과 확인**

Run: `cd backend && ..\.venv\Scripts\python.exe -m pytest tests/market/test_market_service.py::test_manual_and_automatic_lookup_use_the_same_eligible_only_population -v`
Expected: PASS.

- [ ] **Step 4: 이제 아무 데도 안 쓰이는 `automatic` 파라미터 제거**

Grep으로 확인: `grep -rn "automatic" backend/app/market/service.py backend/app/api/market.py` —
`lookup()`의 시그니처와 그 함수 본문 안에서 `automatic` 변수를 참조하는 곳이 Step 2 교체
이후로 없어졌는지 확인한다. 있으면 다음을 지운다:

- `backend/app/market/service.py`의 `lookup_raw_item` 시그니처에서 `automatic: bool = False,`
  파라미터와, `self.lookup(...)` 호출부에 있던 `automatic=automatic,` 인자.
- `lookup()` 시그니처에서 `automatic: bool = False,` 파라미터.
- `backend/app/api/market.py`의 `_automatic_lookup` 안 `lookup_raw_item(..., automatic=True)`
  호출에서 `automatic=True,` 인자.

**주의**: `_automatic_lookup`이라는 함수 이름 자체와 `lookup_market_prices_automatically`
엔드포인트 이름은 그대로 둔다 (이건 "배치로 자동 실행되는 조회"라는 의미이지 상품 필터
엄격도를 뜻하는 이름이 아니었으므로 이름 변경 불필요 — 실제로 필터 엄격도를 뜻했던 건 오직
`.lookup()` 내부의 `automatic` 매개변수뿐이었다).

- [ ] **Step 5: 기존 테스트에서 `automatic=True`를 넘기던 곳 정리**

`backend/tests/market/test_market_service.py`에서 `automatic=True`를 인자로 넘기는 호출을
찾는다 (`test_missing_mouser_adapter_keeps_devicemart_reference_and_reports_setup`,
`test_both_market_sources_failing_remains_source_unavailable` — 295행, 318행 부근). 이제
`lookup_raw_item`에 `automatic` 파라미터가 없으므로 이 인자를 제거한다:

```python
        result = MarketLookupService(
            session,
            settings,
            [FakeAdapter(MarketSource.DEVICEMART, "100")],
        ).lookup_raw_item(raw.id)
```

(이 두 테스트는 원래 `automatic=True`였지만 상품 필터와 무관한 것(어댑터 실패/설정 누락
시나리오)을 검증하므로, 인자만 빼면 그대로 통과해야 한다 — 확인 후 안 되면 원인을 보고한다.)

- [ ] **Step 6: 기존 캐시 재사용 테스트 수정 — eligible 상품이 되도록 픽스처 보강**

`test_market_lookup_collects_then_reuses_fresh_cache`(100-137행)는 `FakeAdapter`가 만드는
`CollectedProduct`에 `manufacturer`/`model_number`/`stock_quantity`/`moq`가 전혀 없어서, Step 2
이후로는 이 상품들이 절대 eligible이 될 수 없다 (제조사·모델·재고 정보 자체가 없으므로). 이
테스트의 원래 목적(캐시 재사용 여부, `cache_state` 전환)은 가격 판정과 무관하므로, 픽스처를
"eligible한 상품"으로 보강해서 원래 의도(첫 조회는 LIVE로 실제 median을 찾고, 재조회는 CACHE로
같은 값을 재사용)를 유지한다.

`FakeAdapter.search`(27-50행)의 `CollectedProduct(...)` 생성에 `manufacturer`와
`model_number`를 추가하고, `_raw_item`(61-97행)의 `CleanDecision(...)`에 `maker_norm`을 추가해
서로 일치시킨다. 정확한 값·재고/MOQ 조합은 `_automatic_exclusion_reasons`(market/service.py
368-426행)의 조건을 직접 읽고 그걸 전부 통과하는 값으로 채운다 (모델번호 정확 일치, 제조사
일치, `moq <= quantity`, `stock_quantity >= quantity`). 이 테스트의 raw item quantity는 10
이므로 `stock_quantity`는 10 이상, `moq`는 10 이하로 설정한다.

이 스텝은 TDD로 진행한다: 값을 채운 뒤 테스트를 실행해 실제 `minimum_price`/`maximum_price`
가 무엇으로 나오는지 확인하고, 그 실제 값(원래 기대했던 90/110과 같아야 정상 — tier 계산
로직 자체는 안 바뀌었으므로)으로 기존 assert가 그대로 맞는지 확인한다. 다르게 나오면 원인을
보고하고 억지로 assert 값을 갈아 끼우지 않는다.

- [ ] **Step 7: 전체 재실행**

Run: `cd backend && ..\.venv\Scripts\python.exe -m pytest tests/market/ tests/api/ -v`
Expected: 전체 PASS, 0 failure.

Run: `cd backend && ..\.venv\Scripts\python.exe -m pytest -q`
Expected: 전체 PASS (다른 모듈에서 `MarketLookupService.lookup`/`lookup_raw_item`을
`automatic=` 키워드로 호출하는 곳이 더 있는지 이 전체 실행으로 확인됨).

- [ ] **Step 8: 커밋**

```bash
git add backend/app/market/service.py backend/app/api/market.py backend/tests/market/test_market_service.py
git commit -m "fix(market): always use eligible-only products for pricing, drop dead automatic param"
```

---

### Task 4: 프론트엔드 타입 + API 함수 — `frontend/src/api/client.ts`

**Files:**
- Modify: `frontend/src/api/client.ts:491-495` (`MarketAssessment`), `:1017-1044` (`lookupMarketPrice`/`lookupMarketPriceBatch`)

- [ ] **Step 1: `MarketAssessment`에 REVIEW 추가**

`frontend/src/api/client.ts:491-495`:

```typescript
export type MarketAssessment =
  | "LOW"
  | "WITHIN_RANGE"
  | "REVIEW"
  | "HIGH"
  | "REVIEW_REQUIRED";
```

- [ ] **Step 2: `lookupMarketPrice`/`lookupMarketPriceBatch`에 `analysisRunId` 추가**

`frontend/src/api/client.ts:1017-1029`을:

```typescript
export function lookupMarketPrice(
  rawItemId: number,
  analysisRunId: number,
  forceRefresh = false,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    analysis_run_id: String(analysisRunId),
    force_refresh: String(forceRefresh),
  });
  return requestJson<MarketLookupResult>(
    `/api/market/lookup/${rawItemId}?${params.toString()}`,
    { method: "POST", signal },
  );
}
```

`frontend/src/api/client.ts:1031-1044`을:

```typescript
export function lookupMarketPriceBatch(
  rawItemIds: number[],
  analysisRunId: number,
  forceRefresh = false,
  signal?: AbortSignal,
) {
  return requestJson<MarketBatchLookupResponse>("/api/market/lookup-batch", {
    method: "POST",
    body: JSON.stringify({
      analysis_run_id: analysisRunId,
      raw_item_ids: rawItemIds,
      force_refresh: forceRefresh,
    }),
    signal,
  });
}
```

- [ ] **Step 3: 타입 체크**

Run: `cd frontend && npm.cmd run build`
Expected: 이 시점에서는 호출부(`QuoteAnalysisPage.tsx`)가 아직 옛 시그니처로 호출하므로
TypeScript 에러가 나는 게 정상 — Task 5에서 고친다. 에러 메시지에 호출부 파일:라인이
정확히 나오는지만 확인하고 넘어간다 (빌드가 통과할 필요는 이 태스크에서 없음).

- [ ] **Step 4: 커밋**

```bash
git add frontend/src/api/client.ts
git commit -m "feat(types): add REVIEW assessment and analysisRunId to market lookup functions"
```

(빌드가 아직 깨진 상태로 커밋하는 것을 이례적으로 허용 — 다음 태스크가 바로 이어서 고치며,
두 태스크를 분리해야 각각의 diff가 한 가지 관심사만 담는다. Task 5가 끝나기 전까지는 이
브랜치를 다른 사람과 공유/배포하지 않는다.)

---

### Task 5: 프론트엔드 UI 호출부 배선 — `frontend/src/pages/QuoteAnalysisPage.tsx`

**Files:**
- Modify: `frontend/src/pages/QuoteAnalysisPage.tsx:186-190` (배치 조회), `:607-613` (`AnalysisRow` 인스턴스), `:968-1017` (`AnalysisRow` 컴포넌트), `:1315-1322` (`marketAssessmentLabel`)

- [ ] **Step 1: 자동 배치 조회 호출에 run_id 전달**

`frontend/src/pages/QuoteAnalysisPage.tsx:186-190`(현재):

```typescript
        return lookupMarketPriceBatch(
          autoMarketLookupIds,
          false,
          controller.signal,
        );
```

을:

```typescript
        return lookupMarketPriceBatch(
          autoMarketLookupIds,
          analysis!.run_id,
          false,
          controller.signal,
        );
```

로 교체. (`analysis`는 이 컴포넌트 최상단 state로, 이 effect가 실행되는 시점엔
`autoMarketLookupIds`가 `analysis?.lines`에서 파생되므로 `analysis`가 반드시 존재함 — 그래도
TypeScript가 null 가능성을 지적하면 `analysis!.run_id` 대신 이 effect 상단에
`if (!analysis) return;` 가드를 추가하는 방식으로 처리해도 된다. 실제 컴파일 에러 메시지를
보고 더 안전한 쪽으로 판단할 것.)

- [ ] **Step 2: `AnalysisRow`에 `analysisRunId` prop 추가**

`frontend/src/pages/QuoteAnalysisPage.tsx:968-978`(컴포넌트 시그니처)을:

```typescript
function AnalysisRow({
  line,
  market,
  marketLookup,
  onMarketResult,
  analysisRunId,
}: {
  line: AnalysisLine;
  market: MarketLookupResult | null;
  marketLookup?: MarketLookupProgressItem;
  onMarketResult: (result: MarketLookupResult) => void;
  analysisRunId: number;
}) {
```

`frontend/src/pages/QuoteAnalysisPage.tsx:1007-1011`(현재):

```typescript
  const requestMarket = async (forceRefresh = false) => {
    setMarketLoading(true);
    setMarketError("");
    try {
      onMarketResult(await lookupMarketPrice(line.raw_item_id, forceRefresh));
```

을:

```typescript
  const requestMarket = async (forceRefresh = false) => {
    setMarketLoading(true);
    setMarketError("");
    try {
      onMarketResult(
        await lookupMarketPrice(line.raw_item_id, analysisRunId, forceRefresh),
      );
```

로 교체.

- [ ] **Step 3: `<AnalysisRow>` 인스턴스에 prop 전달**

`frontend/src/pages/QuoteAnalysisPage.tsx:607-613`(현재):

```jsx
              <AnalysisRow
                key={line.raw_item_id}
                line={line}
                market={marketResults[line.raw_item_id] ?? null}
                marketLookup={marketLookupItems[line.raw_item_id]}
                onMarketResult={onMarketResult}
              />
```

을:

```jsx
              <AnalysisRow
                key={line.raw_item_id}
                line={line}
                market={marketResults[line.raw_item_id] ?? null}
                marketLookup={marketLookupItems[line.raw_item_id]}
                onMarketResult={onMarketResult}
                analysisRunId={analysis.run_id}
              />
```

로 교체. (이 JSX가 렌더되는 시점엔 `analysis`가 이미 non-null임 — 상위 조건부 렌더링으로
보장되는지 실제 코드를 읽고 확인. 아니라면 `analysis?.run_id ?? 0`처럼 방어하지 말고, 왜
non-null이 보장 안 되는지 먼저 파악해서 보고한다 — 값이 없을 때 조용히 0을 보내면 서버가
404를 뱉는 게 나으므로 fallback 값 사용은 금지.)

- [ ] **Step 4: `marketAssessmentLabel`에 REVIEW 추가**

`frontend/src/pages/QuoteAnalysisPage.tsx:1315-1322`(현재):

```typescript
function marketAssessmentLabel(assessment: MarketLookupResult["assessment"]) {
  return {
    LOW: "시장가 대비 저가",
    WITHIN_RANGE: "시장가 범위 적정",
    HIGH: "시장가 대비 고가",
    REVIEW_REQUIRED: "판정 대기",
  }[assessment];
}
```

을:

```typescript
function marketAssessmentLabel(assessment: MarketLookupResult["assessment"]) {
  return {
    LOW: "시장가 대비 저가",
    WITHIN_RANGE: "시장가 범위 적정",
    REVIEW: "시장가 대비 주의",
    HIGH: "시장가 대비 고가",
    REVIEW_REQUIRED: "판정 대기",
  }[assessment];
}
```

로 교체.

- [ ] **Step 5: 집계(summarize)는 코드 변경 없이 동작 확인만**

`frontend/src/pages/QuoteAnalysisPage.tsx`의 `summarize` 함수(1334행 부근)의 `count()`,
`market` 변수는 문자열 비교(`effectiveAssessment(line) === assessment`,
`=== "REVIEW_REQUIRED"`)라 타입에 `"REVIEW"`가 추가되고 백엔드가 실제로 그 값을 반환하기
시작하면 코드 수정 없이도 "주의" 품목이 `count("REVIEW")`에, "시장가 확인 필요"에서는
빠지는 게 자동으로 맞게 된다 — 코드는 건드리지 말고, Task 7의 프론트 테스트로 이 동작을
실제로 검증한다.

- [ ] **Step 6: 빌드 확인**

Run: `cd frontend && npm.cmd run build`
Expected: 이제 에러 없이 성공해야 한다 (Task 4에서 만든 시그니처 불일치가 이 태스크로
해소됨).

Run: `cd frontend && npm.cmd run lint`
Expected: 클린.

- [ ] **Step 7: 커밋**

```bash
git add frontend/src/pages/QuoteAnalysisPage.tsx
git commit -m "feat(ui): wire analysis_run_id into market lookups, label REVIEW assessment"
```

---

### Task 6: 프론트엔드 테스트 갱신 — `frontend/src/pages/QuoteAnalysisPage.test.tsx`

**Files:**
- Modify: `frontend/src/pages/QuoteAnalysisPage.test.tsx` (시장가 관련 fetch mock·assertion 전부)

- [ ] **Step 1: 기존 시장가 관련 테스트 찾기**

`grep -n "lookup-batch\|market/lookup\|MarketLookupResult\|marketAssessmentLabel\|시장가" frontend/src/pages/QuoteAnalysisPage.test.tsx`
로 시장가 관련 fetch mock URL 조립(`/api/market/lookup-batch`, `/api/market/lookup/`)과
요청 바디 assertion을 전부 찾는다. (이 세션 앞부분에서 이미 본 예시: 177-240행 부근의
"uploads a new bid..." 테스트가 `/api/market/lookup-batch`를 모킹하고 요청 바디를
`{ raw_item_ids: [8], force_refresh: false }`로 assert함 — 이제 `analysis_run_id`가
바디에 추가되므로 이 assertion을 갱신해야 함.)

- [ ] **Step 2: 요청 바디 assertion에 `analysis_run_id` 반영**

찾은 각 위치에서 `JSON.parse(String(batch?.init?.body))`류 assertion에 `analysis_run_id:
<해당 테스트의 run_id 픽스처값>` 키를 추가한다 (테스트 픽스처의 `run_id: 14` 같은 고정값을
그대로 사용 — 하드코딩된 값이면 그 값을, `createQuoteAnalysisRun` 목 응답의 `run_id` 필드를
읽어서 쓰는 구조면 그 값을 참조).

단건 조회(`/api/market/lookup/${id}`) URL 조립이 있다면 `analysis_run_id` 쿼리 파라미터가
포함된 URL로 매칭하도록 fetch mock의 URL 비교 조건도 갱신한다 (예:
`url.includes("/api/market/lookup/")`처럼 느슨하게 매칭하는 기존 패턴이면 손댈 필요 없을 수
있음 — 실제 코드를 보고 판단).

- [ ] **Step 3: REVIEW 판정 케이스 테스트 추가**

기존 시장가 목 응답 중 하나(또는 새 테스트)에서 `assessment: "REVIEW"`를 반환하도록 만들고,
화면에 "시장가 대비 주의"가 보이는지, 그리고 "판정 대기"/"시장가 확인 필요" 카운트에는 안
잡히고 "주의" 카운트(`주의 N건`)에 잡히는지 assert하는 테스트를 추가하거나 기존 테스트에
케이스를 보강한다.

- [ ] **Step 4: TDD로 실행 — 실패 확인 후 위 수정 반영, 통과 확인**

Run: `cd frontend && npm.cmd test -- --run src/pages/QuoteAnalysisPage.test.tsx`
Task 5까지 끝난 프로덕션 코드는 이미 `analysis_run_id`를 보내므로, 이 테스트 파일을 고치기
전엔 요청 바디 assertion이 실패해야 정상이다(actual에 새 키가 있는데 expected엔 없음). 위
수정을 반영한 뒤 재실행해 통과 확인.

- [ ] **Step 5: 전체 스위트 + 빌드 + 린트**

Run:
```bash
cd frontend
call npm.cmd test -- --run
call npm.cmd run lint
call npm.cmd run build
```
Expected: 전부 클린.

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/pages/QuoteAnalysisPage.test.tsx
git commit -m "test(ui): assert analysis_run_id is sent to market lookups, cover REVIEW assessment"
```

---

### Task 7: 전체 검증 + 브라우저 확인 + Push

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
Expected: 전부 클린.

- [ ] **Step 3: 실행 중인 서버 재기동 후 육안 확인**

이미 떠 있는 서버가 있으면 종료 후 `scripts\start-local.bat --no-browser`로 재기동 (alembic
마이그레이션은 이번 변경에 포함되지 않으므로 스키마 변경 없음). 브라우저에서 시장가 확인이
필요한 품목이 있는 견적을 분석 실행 → "적정 범위" 슬라이더를 넓게/좁게 바꿔가며 재실행 →
시장가 품목의 판정이 그 설정을 따라 바뀌는지, "주의" 상태가 실제로 표시되는지 확인.

- [ ] **Step 4: 커밋 로그 확인 후 push**

Run: `cd "C:\Users\WIA\Desktop\price_analyzer" && git log --oneline -10`
Expected: Task 1~6의 커밋들이 순서대로 보임.

Run: `cd "C:\Users\WIA\Desktop\price_analyzer" && git push personal feature/market-price-integration`
Expected: 정상 push. 다른 사람이 같은 브랜치에 push했다면 먼저 `git pull --rebase personal
feature/market-price-integration` 필요 — 이 경우 사용자에게 알리고 확인받는다.
