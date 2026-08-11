import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  ApiError,
  createQuoteAnalysisRun,
  getStandardEvidence,
  lookupMarketPrice,
  submitIncomingBid,
  type AnalysisAssessment,
  type AnalysisLine,
  type QuoteAnalysisRun,
  type MarketLookupResult,
  type SubmissionResponse,
} from "../api/client";

type WorkflowStage = "IDLE" | "PARSING" | "ANALYZING";
type ResultFilter =
  | "ALL"
  | "MATCHED"
  | "MARKET"
  | "PENDING"
  | AnalysisAssessment;

const assessmentLabels: Record<AnalysisAssessment, string> = {
  NOT_APPLICABLE: "판정 제외",
  REVIEW_REQUIRED: "판정 대기",
  LOW: "저가",
  WITHIN_RANGE: "적정",
  REVIEW: "주의",
  HIGH: "고가",
};

export function QuoteAnalysisPage() {
  const [file, setFile] = useState<File | null>(null);
  const [submittedBy, setSubmittedBy] = useState("");
  const [reviewPercent, setReviewPercent] = useState(10);
  const [highPercent, setHighPercent] = useState(20);
  const [stage, setStage] = useState<WorkflowStage>("IDLE");
  const [submission, setSubmission] = useState<SubmissionResponse | null>(null);
  const [analysis, setAnalysis] = useState<QuoteAnalysisRun | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [resultFilter, setResultFilter] = useState<ResultFilter>("ALL");
  const [marketResults, setMarketResults] = useState<
    Record<number, MarketLookupResult>
  >({});
  const [validationError, setValidationError] = useState("");
  const uploadController = useRef<AbortController | null>(null);
  const analysisController = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const busy = stage !== "IDLE";

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      uploadController.current?.abort();
      analysisController.current?.abort();
    };
  }, []);

  const startAnalysis = async (retryAccepted = false) => {
    if (!file || !submittedBy.trim()) {
      setValidationError("견적서 파일과 접수자를 모두 입력해 주세요.");
      return;
    }
    if (
      !Number.isFinite(reviewPercent)
      || !Number.isFinite(highPercent)
      || reviewPercent < 0
      || highPercent < reviewPercent
    ) {
      setValidationError("판정 기준은 0 이상이며 고가·저가 기준이 적정 범위보다 커야 합니다.");
      return;
    }
    setValidationError("");
    setError(null);
    setAnalysis(null);
    setMarketResults({});
    uploadController.current?.abort();
    analysisController.current?.abort();
    if (!retryAccepted || submission === null) {
      setSubmission(null);
      setStage("PARSING");
    } else {
      setStage("ANALYZING");
    }
    try {
      let accepted = retryAccepted ? submission : null;
      if (accepted === null) {
        const controller = new AbortController();
        uploadController.current = controller;
        accepted = await submitIncomingBid(
          file,
          submittedBy.trim(),
          controller.signal,
        );
        if (!mounted.current) return;
        uploadController.current = null;
        setSubmission(accepted);
      }
      setStage("ANALYZING");
      const controller = new AbortController();
      analysisController.current = controller;
      const result = await createQuoteAnalysisRun({
        documentId: accepted.document_id,
        createdBy: submittedBy.trim(),
        reviewPercent,
        highPercent,
        signal: controller.signal,
      });
      if (!mounted.current) return;
      analysisController.current = null;
      setAnalysis(result);
      setStage("IDLE");
    } catch (caught) {
      if (isAbortError(caught)) {
        if (mounted.current) setStage("IDLE");
        return;
      }
      setError(caught);
      setStage("IDLE");
    }
  };

  const metrics = useMemo(
    () => summarize(
      analysis?.lines ?? [],
      submission?.raw_item_count ?? 0,
      marketResults,
    ),
    [analysis, submission, marketResults],
  );
  const visibleLines = useMemo(
    () => (analysis?.lines ?? []).filter(
      (line) => lineMatches(
        line,
        resultFilter,
        marketResults[line.raw_item_id],
      ),
    ),
    [analysis, resultFilter, marketResults],
  );

  return (
    <main className="workspace-page analysis-page">
      <header className="page-heading analysis-page-heading">
        <div>
          <p className="section-kicker">Incoming quote review</p>
          <h1>신규 견적 분석</h1>
        </div>
        <p>
          업체 견적서를 접수하면 과거 견적 기반 표준 DB와 비교합니다.
          매칭되지 않은 품목은 가격을 추정하지 않고 판정대기로 남깁니다.
        </p>
      </header>

      <section className="quote-intake" aria-labelledby="intake-title">
        <div className="intake-title">
          <span>01</span>
          <div>
            <h2 id="intake-title">견적서 접수</h2>
            <p>.xlsx, .xls, .pdf · 최대 25MB</p>
          </div>
        </div>
        <label className="file-field">
          <span>신규 견적서</span>
          <input
            type="file"
            aria-label="신규 견적서"
            accept=".xlsx,.xls,.pdf"
            disabled={busy}
            onChange={(event) => {
              uploadController.current?.abort();
              analysisController.current?.abort();
              setFile(event.target.files?.[0] ?? null);
              setSubmission(null);
              setAnalysis(null);
              setMarketResults({});
              setError(null);
              setStage("IDLE");
            }}
          />
          <strong>{file?.name ?? "파일을 선택해 주세요"}</strong>
        </label>
        <label className="submitter-field">
          <span>접수자</span>
          <input
            aria-label="접수자"
            value={submittedBy}
            disabled={busy}
            placeholder="이름 또는 담당 조직"
            onChange={(event) => setSubmittedBy(event.target.value)}
          />
        </label>
        <div className="analysis-threshold-fields" aria-label="가격 판정 기준">
          <label>
            <span>적정 범위(±%)</span>
            <input
              type="number"
              min="0"
              step="1"
              value={reviewPercent}
              disabled={busy}
              onChange={(event) => setReviewPercent(Number(event.target.value))}
            />
          </label>
          <label>
            <span>고가·저가 기준(±%)</span>
            <input
              type="number"
              min={reviewPercent}
              step="1"
              value={highPercent}
              disabled={busy}
              onChange={(event) => setHighPercent(Number(event.target.value))}
            />
          </label>
        </div>
        <button
          className="primary-action stable-action"
          type="button"
          disabled={busy}
          onClick={() => void startAnalysis()}
        >
          {busy ? "분석 진행 중" : "견적 분석 시작"}
        </button>
      </section>

      {(validationError || error !== null) && (
        <div className="analysis-error" role="alert">
          <div>
            <strong>{error instanceof ApiError ? error.errorCode ?? "REQUEST_FAILED" : "입력을 확인해 주세요"}</strong>
            <span>{error instanceof Error ? error.message : validationError}</span>
          </div>
          {error !== null && (
            <button
              type="button"
              onClick={() => void startAnalysis(submission !== null)}
            >
              다시 시도
            </button>
          )}
        </div>
      )}

      <WorkflowProgress stage={stage} complete={analysis !== null} />

      {stage !== "IDLE" && (
        <div className="analysis-progress-message" role="status" aria-live="polite">
          {stage === "PARSING"
            ? "견적서를 업로드하고 품목을 파싱하는 중입니다."
            : "표준 DB와 견적 품목을 비교하는 중입니다."}
        </div>
      )}

      {!analysis && stage === "IDLE" && (
        <section className="analysis-empty">
          <span>READY</span>
          <h2>새 견적서를 첨부하면 분석 결과가 여기에 표시됩니다.</h2>
          <p>기존 견적을 선택하는 방식이 아니라, 접수한 파일 한 건을 즉시 분석합니다.</p>
        </section>
      )}

      {analysis && submission && (
        <AnalysisResults
          analysis={analysis}
          submission={submission}
          metrics={metrics}
          lines={visibleLines}
          filter={resultFilter}
          onFilter={setResultFilter}
          marketResults={marketResults}
          onMarketResult={(result) => setMarketResults((current) => ({
            ...current,
            [result.raw_item_id]: result,
          }))}
        />
      )}
    </main>
  );
}

function WorkflowProgress({
  stage,
  complete,
}: {
  stage: WorkflowStage;
  complete: boolean;
}) {
  const active = stage === "PARSING" ? 2 : stage === "ANALYZING" ? 3 : complete ? 4 : 0;
  return (
    <ol className="workflow-progress" aria-label="견적 분석 진행 단계">
      {["파일 업로드", "견적서 파싱", "가격 분석"].map((label, index) => (
        <li
          key={label}
          className={active > index + 1 ? "is-complete" : active === index + 1 ? "is-active" : ""}
        >
          <span>{String(index + 1).padStart(2, "0")}</span>
          <strong>{label}</strong>
        </li>
      ))}
    </ol>
  );
}

function AnalysisResults({
  analysis,
  submission,
  metrics,
  lines,
  filter,
  onFilter,
  marketResults,
  onMarketResult,
}: {
  analysis: QuoteAnalysisRun;
  submission: SubmissionResponse;
  metrics: ReturnType<typeof summarize>;
  lines: AnalysisLine[];
  filter: ResultFilter;
  onFilter: (filter: ResultFilter) => void;
  marketResults: Record<number, MarketLookupResult>;
  onMarketResult: (result: MarketLookupResult) => void;
}) {
  const [activeTab, setActiveTab] = useState<"THRESHOLD" | "TARGET">("THRESHOLD");
  return (
    <section className="analysis-results">
      <header className="result-heading">
        <div>
          <p className="section-kicker">접수 견적 #{analysis.document.id}</p>
          <h2>{analysis.document.display_name}</h2>
          <p>
            <strong>{`총 ${submission.raw_item_count}개 품목`}</strong>
          </p>
        </div>
        <div className={`overall-assessment is-${metrics.overallTone}`}>
          <span>종합 판정</span>
          <strong>{metrics.overall}</strong>
          <small>평가 금액 커버리지 {formatPercent(metrics.coverage)}</small>
        </div>
      </header>

      <div className="analysis-mode-tabs" role="tablist" aria-label="견적 분석 방식">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "THRESHOLD"}
          className={activeTab === "THRESHOLD" ? "is-active" : ""}
          onClick={() => setActiveTab("THRESHOLD")}
        >
          가격 적정성
          <small>설정한 임계값으로 고가·적정·저가 판정</small>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "TARGET"}
          className={activeTab === "TARGET" ? "is-active" : ""}
          onClick={() => setActiveTab("TARGET")}
        >
          구매 목표가
          <small>과거 단가를 현재 생산자물가로 보정</small>
        </button>
      </div>

      {activeTab === "THRESHOLD" ? (
        <>

      <div className="decision-summary" aria-label="분석 요약">
        <div>
          <span>매칭 완료</span>
          <strong>{metrics.matched}건</strong>
        </div>
        <div>
          <span>시장가 확인 필요</span>
          <strong>{`${metrics.market}건`}</strong>
        </div>
        <div>
          <span>판정 대기</span>
          <strong>{metrics.pending}건</strong>
        </div>
        <div className="is-high">
          <span>가격 판정</span>
          <strong>{`고가 ${metrics.high}건`}</strong>
        </div>
        <div className="is-within">
          <span>가격 판정</span>
          <strong>{`적정 ${metrics.within}건`}</strong>
        </div>
        <div className="is-review">
          <span>가격 판정</span>
          <strong>{`주의 ${metrics.review}건`}</strong>
        </div>
        <div>
          <span>가격 판정</span>
          <strong>저가 {metrics.low}건</strong>
        </div>
      </div>

      <p className="price-policy-note">
        {analysis.price_policy?.description ??
          "표준 대비 ±10% 이내 적정, ±10% 초과~±20% 주의, ±20% 초과 고가·저가"}
        . 판정 대기는 정제 미완료 또는 기준가가 없어 가격을 판단하지 못한 품목입니다.
      </p>

      <div className="market-roadmap">
        <strong>{`시장가 확인 필요 ${metrics.market}건`}</strong>
        <span>DeviceMart·Mouser 캐시 우선 조회</span>
        <p>캐시가 없거나 만료된 품목만 실시간 조회하며, 실패하면 가격을 만들지 않고 판정대기로 유지합니다.</p>
      </div>

      <div className="result-toolbar">
        <div>
          <h3>품목별 판정</h3>
          <span>표시 {lines.length}건 / 전체 {analysis.lines.length}건</span>
        </div>
        <label>
          <span>결과 필터</span>
          <select value={filter} onChange={(event) => onFilter(event.target.value as ResultFilter)}>
            <option value="ALL">전체 품목</option>
            <option value="MATCHED">표준 DB 매칭</option>
            <option value="MARKET">시장가 확인 필요</option>
            <option value="PENDING">판정 대기</option>
            <option value="HIGH">고가</option>
            <option value="WITHIN_RANGE">적정</option>
            <option value="REVIEW">주의</option>
            <option value="LOW">저가</option>
          </select>
        </label>
      </div>

      <div className="analysis-table-scroll">
        <table className="analysis-result-table">
          <thead>
            <tr>
              <th>품목 / 사양</th>
              <th>단위·수량</th>
              <th>견적 단가</th>
              <th>견적 금액</th>
              <th>참조 기준가</th>
              <th>참조 최소·평균·최대</th>
              <th>편차 금액</th>
              <th>편차율</th>
              <th>매칭 / 근거</th>
              <th>가격 판정</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <AnalysisRow
                key={line.raw_item_id}
                line={line}
                market={marketResults[line.raw_item_id] ?? null}
                onMarketResult={onMarketResult}
              />
            ))}
          </tbody>
        </table>
        {lines.length === 0 && <p className="inline-state">선택한 조건에 맞는 품목이 없습니다.</p>}
      </div>
        </>
      ) : (
        <TargetPriceResults analysis={analysis} />
      )}
    </section>
  );
}

function TargetPriceResults({ analysis }: { analysis: QuoteAnalysisRun }) {
  const lineById = useMemo(
    () => new Map(analysis.lines.map((line) => [line.raw_item_id, line])),
    [analysis.lines],
  );
  const coverage = analysis.lines.length === 0
    ? 0
    : analysis.target_available_count / analysis.lines.length;
  const targetDifference =
    analysis.quote_total_amount !== null && analysis.target_total_amount !== null
      ? Number(analysis.quote_total_amount) - Number(analysis.target_total_amount)
      : null;

  return (
    <section className="target-price-panel" role="tabpanel">
      <div className="target-price-summary">
        <div>
          <span>목표가 산정</span>
          <strong>{analysis.target_available_count}건</strong>
          <small>전체 품목의 {formatPercent(coverage)}</small>
        </div>
        <div>
          <span>산정 제외·대기</span>
          <strong>{analysis.target_unavailable_count}건</strong>
          <small>날짜·지수·표준 DB 근거 부족</small>
        </div>
        <div>
          <span>산정 품목 목표금액</span>
          <strong>{formatMoney(analysis.target_total_amount)}</strong>
          <small>계산 가능한 품목만 합산</small>
        </div>
        <div className={targetDifference !== null && targetDifference > 0 ? "is-saving" : ""}>
          <span>목표가 대비 차액</span>
          <strong>{targetDifference === null ? "—" : formatSignedMoney(String(targetDifference))}</strong>
          <small>양수이면 협상 절감 여지</small>
        </div>
      </div>

      <div className="inflation-basis-card">
        <div>
          <span>물가보정 기준</span>
          <strong>
            {analysis.target_period
              ? `${analysis.target_period.slice(0, 4)}년 ${Number(analysis.target_period.slice(4))}월 생산자물가지수 ${analysis.target_index_value}`
              : "저장된 생산자물가지수 없음"}
          </strong>
          <small>한국은행 생산자물가지수 총지수 · 2020=100</small>
        </div>
        <a href={analysis.inflation_source_url} target="_blank" rel="noreferrer">
          KOSIS 공식 통계 보기
        </a>
      </div>

      <p className="target-policy-note">
        원본 견적서 본문이나 머리말에서 날짜가 직접 확인된 과거 단가만 각각 물가보정한 뒤 중앙값을 계산합니다.
        팀 엑셀 보완 날짜와 파일명 추정 날짜는 사용하지 않으며, 시장가는 목표가에 섞지 않습니다.
      </p>

      <div className="analysis-table-scroll">
        <table className="analysis-result-table target-price-table">
          <thead>
            <tr>
              <th>품목 / 사양</th>
              <th>견적 단가</th>
              <th>구매 목표 단가</th>
              <th>목표 금액</th>
              <th>목표가 대비</th>
              <th>산정 근거</th>
            </tr>
          </thead>
          <tbody>
            {analysis.target_lines.map((target) => {
              const line = lineById.get(target.raw_item_id);
              return (
                <tr key={target.raw_item_id}>
                  <td>
                    <strong>{line?.item_name ?? "품명 없음"}</strong>
                    <span>{line?.spec ?? "원본에 규격 없음"}</span>
                  </td>
                  <td className="numeric">{formatMoney(line?.quote_unit_price ?? null)}</td>
                  <td className="numeric target-unit-price">{formatMoney(target.target_unit_price)}</td>
                  <td className="numeric">{formatMoney(target.target_amount)}</td>
                  <td className="numeric">
                    <strong>{formatSignedMoney(target.variance_amount)}</strong>
                    <span>{formatSignedPercent(target.variance_percent)}</span>
                  </td>
                  <td>
                    <details className="target-evidence-details">
                      <summary>
                        {target.status === "AVAILABLE"
                          ? `원본 ${target.used_observation_count}건${target.used_observation_count === 1 ? " · 신뢰도 낮음" : ""}`
                          : targetStatusLabel(target.status)}
                      </summary>
                      <p>{target.reason}</p>
                      {target.evidence.map((evidence) => (
                        <a
                          key={evidence.raw_item_id}
                          href={`/api/documents/variants/${evidence.source_variant_id}/file${evidence.source_page ? `#page=${evidence.source_page}` : ""}`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          <span>{evidence.source_logical_name.split(/[\\/]/).at(-1)}</span>
                          <small>{evidence.quote_date} · {formatMoney(evidence.original_unit_price)} → {formatMoney(evidence.adjusted_unit_price)}</small>
                        </a>
                      ))}
                    </details>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function targetStatusLabel(status: QuoteAnalysisRun["target_lines"][number]["status"]) {
  return {
    AVAILABLE: "산정 완료",
    DATE_UNAVAILABLE: "원본 견적일 확인 필요",
    INDEX_UNAVAILABLE: "물가지수 갱신 필요",
    MARKET_REFERENCE_REQUIRED: "표준 DB 없음 · 시장가 별도 확인",
    NOT_APPLICABLE: "산정 제외",
  }[status];
}

function AnalysisRow({
  line,
  market,
  onMarketResult,
}: {
  line: AnalysisLine;
  market: MarketLookupResult | null;
  onMarketResult: (result: MarketLookupResult) => void;
}) {
  const [marketError, setMarketError] = useState("");
  const [marketLoading, setMarketLoading] = useState(false);
  const hasPriceEvidence =
    line.match_status === "MATCHED" &&
    line.standard_item_id !== null &&
    line.standard_price_version_id !== null;
  const standardEvidence = useQuery({
    queryKey: [
      "analysis-standard-evidence",
      line.standard_item_id,
      line.standard_price_version_id,
    ],
    queryFn: ({ signal }) =>
      getStandardEvidence({
        standardItemId: line.standard_item_id!,
        priceVersionId: line.standard_price_version_id!,
        signal,
      }),
    enabled: false,
    staleTime: Number.POSITIVE_INFINITY,
  });

  const requestMarket = async (forceRefresh = false) => {
    setMarketLoading(true);
    setMarketError("");
    try {
      onMarketResult(await lookupMarketPrice(line.raw_item_id, forceRefresh));
    } catch (error) {
      setMarketError(error instanceof Error ? error.message : "시장가 조회에 실패했습니다.");
    } finally {
      setMarketLoading(false);
    }
  };
  const referencePrice = market?.median_price ?? line.reference_price;
  const minimumPrice = market?.minimum_price ?? line.minimum_price;
  const middlePrice = market?.median_price ?? line.average_price;
  const maximumPrice = market?.maximum_price ?? line.maximum_price;
  const varianceAmount =
    market?.median_price && line.quote_unit_price
      ? String(Number(line.quote_unit_price) - Number(market.median_price))
      : line.variance_amount;
  const rowAssessment = market?.assessment ?? line.assessment;

  return (
    <Fragment>
    <tr>
      <td>
        <strong>{line.item_name ?? "품명 없음"}</strong>
        <span>{line.spec ?? analysisSpecLabel(line.spec_source_status)}</span>
      </td>
      <td className="numeric">{line.unit ?? "—"} · {formatNumber(line.quantity)}</td>
      <td className="numeric">{formatMoney(line.quote_unit_price)}</td>
      <td className="numeric">{formatMoney(line.quote_amount)}</td>
      <td className="numeric reference-basis">{formatMoney(referencePrice)}</td>
      <td
        className="reference-range reference-evidence-trigger"
        tabIndex={hasPriceEvidence ? 0 : undefined}
        onMouseEnter={() => {
          if (hasPriceEvidence && !standardEvidence.data) void standardEvidence.refetch();
        }}
        onFocus={() => {
          if (hasPriceEvidence && !standardEvidence.data) void standardEvidence.refetch();
        }}
      >
        {line.match_status === "MATCHED" || market ? (
          <>
            <strong>{formatMoney(minimumPrice)}</strong>
            <span>{formatMoney(middlePrice)}</span>
            <strong>{formatMoney(maximumPrice)}</strong>
            {hasPriceEvidence && (
              <span className="reference-evidence-count">
                근거 {line.standard_observation_count ?? 0}건
                {line.evidence_quality === "SINGLE_OBSERVATION" ? " · 신뢰도 낮음" : ""}
              </span>
            )}
          </>
        ) : "—"}
        {hasPriceEvidence && (
          <div className="reference-evidence-popover" role="tooltip">
            <strong>표준단가 원본 근거</strong>
            {standardEvidence.isFetching && <span>불러오는 중…</span>}
            {standardEvidence.data?.observations.slice(0, 4).map((row) => (
              <a
                href={`/api/documents/variants/${row.source.variant_id}/file${row.source.page ? `#page=${row.source.page}` : ""}`}
                target="_blank"
                rel="noreferrer"
                key={row.raw_item_id}
              >
                <span>{row.source.logical_name.split(/[\\/]/).at(-1)}</span>
                <strong>{formatMoney(row.unit_price)}</strong>
              </a>
            ))}
            {standardEvidence.isError && <span>근거를 불러오지 못했습니다.</span>}
          </div>
        )}
      </td>
      <td className="numeric">{formatSignedMoney(varianceAmount)}</td>
      <td className="numeric">
        {formatSignedPercent(market?.variance_percent ?? line.variance_percent)}
      </td>
      <td>
        <div className="line-evidence">
          <span className={`match-badge is-${line.match_status.toLowerCase()}`}>
            {matchStatusLabel(line.match_status)}
          </span>
          {hasPriceEvidence ? (
            <>
              <a
                href={`/standard-prices?item_id=${line.standard_item_id}&version_id=${line.standard_price_version_id}`}
                aria-label="표준 가격 근거 보기"
              >
                표준 DB #{line.standard_item_id} · v{line.standard_price_version_id}
              </a>
              <small>내부 표준가격 적용 · 시장가 조회 생략</small>
            </>
          ) : null}
          {line.market_price_lookup_status === "FUTURE_MARKET_LOOKUP" && (
            <>
              <small className="market-required">시장가 확인 필요</small>
              <button
                className="market-lookup-button stable-action"
                type="button"
                disabled={marketLoading}
                aria-busy={marketLoading}
                onClick={() => void requestMarket(false)}
              >
                {marketLoading ? "조회 중…" : market ? "캐시 다시 보기" : "시장가 조회"}
              </button>
            </>
          )}
          <small>
            {line.source.sheet ?? "파일"} · {line.source.row ? `${line.source.row}행` : line.source.page ? `${line.source.page}쪽` : "위치 없음"}
          </small>
        </div>
      </td>
      <td>
        <div className="row-status">
          <span className={`assessment is-${rowAssessment.toLowerCase()}`}>
            {market
              ? marketAssessmentLabel(market.assessment)
              : assessmentLabels[line.assessment]}
          </span>
        </div>
      </td>
    </tr>
    {(market || marketError) && (
      <tr className="market-detail-row">
        <td colSpan={10}>
          {marketError ? (
            <div className="market-error">
              <span>{marketError}</span>
              <button type="button" onClick={() => void requestMarket(true)}>
                실시간 재조회
              </button>
            </div>
          ) : market ? (
            <MarketResultPanel
              result={market}
              onRefresh={() => void requestMarket(true)}
              loading={marketLoading}
            />
          ) : null}
        </td>
      </tr>
    )}
    </Fragment>
  );
}

function analysisSpecLabel(status: AnalysisLine["spec_source_status"]) {
  if (status === "SOURCE_BLANK") return "원문에 규격 없음";
  if (status === "PARSER_UNMAPPED") return "견적서에서 규격 위치 확인 필요";
  return "원본에서 규격을 확인하지 못함";
}

function MarketResultPanel({
  result,
  onRefresh,
  loading,
}: {
  result: MarketLookupResult;
  onRefresh: () => void;
  loading: boolean;
}) {
  return (
    <section className="market-result-panel" aria-label="시장가 비교 결과">
      <header>
        <div>
          <span className={`market-state is-${result.cache_state.toLowerCase()}`}>
            {marketStateLabel(result.cache_state)}
          </span>
          <strong>{result.query}</strong>
        </div>
        <div className="market-summary">
          <span>시장가 범위</span>
          <strong>
            {formatMoney(result.minimum_price)} – {formatMoney(result.maximum_price)}
          </strong>
          <span className={`assessment is-${result.assessment.toLowerCase()}`}>
            {marketAssessmentLabel(result.assessment)}
          </span>
          <button type="button" disabled={loading} onClick={onRefresh}>
            실시간 갱신
          </button>
        </div>
      </header>
      {result.products.length > 0 ? (
        <div className="market-product-grid">
          {result.products.map((product) => (
            <article key={product.observation_id} className="market-product-card">
              <div className="market-product-image">
                {product.image_evidence_url || product.image_url ? (
                  <img
                    src={product.image_evidence_url ?? product.image_url ?? ""}
                    alt={`${product.title} 상품 이미지`}
                    loading="lazy"
                  />
                ) : (
                  <span>NO IMAGE</span>
                )}
              </div>
              <div className="market-product-copy">
                <span className={`source-badge is-${product.source.toLowerCase()}`}>
                  {product.source === "DEVICEMART" ? "DeviceMart" : "Mouser"}
                </span>
                <strong>{product.title}</strong>
                <small>
                  {[product.manufacturer, product.model_number].filter(Boolean).join(" · ") || "제조사·모델 정보 없음"}
                </small>
                <div className="market-product-price">
                  {formatMoney(product.applicable_unit_price)}
                </div>
                <small>
                  재고 {product.stock_quantity ?? product.stock_text ?? "미표시"} · MOQ {product.moq ?? "미표시"}
                </small>
                <small>수집 {formatCollectedAt(product.collected_at)}</small>
                <div className="market-evidence-links">
                  <a href={product.product_url} target="_blank" rel="noreferrer">
                    원본 상품 보기
                  </a>
                  <a href={product.raw_evidence_url} target="_blank" rel="noreferrer">
                    수집 증빙 보기
                  </a>
                  {product.screenshot_evidence_url && (
                    <a href={product.screenshot_evidence_url} target="_blank" rel="noreferrer">
                      화면 증빙
                    </a>
                  )}
                </div>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <p className="market-empty">현재 사용 가능한 KRW 시장가가 없어 판정대기로 유지합니다.</p>
      )}
      {result.source_failures.length > 0 && (
        <ul className="market-source-failures">
          {result.source_failures.map((failure) => (
            <li key={failure.source}>
              {failure.source}: {failure.detail}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function marketStateLabel(state: MarketLookupResult["cache_state"]) {
  return {
    CACHE: "저장된 시장가",
    LIVE: "실시간 수집",
    PARTIAL: "일부 출처 수집",
    UNAVAILABLE: "조회 불가",
  }[state];
}

function marketAssessmentLabel(assessment: MarketLookupResult["assessment"]) {
  return {
    LOW: "시장가 대비 저가",
    WITHIN_RANGE: "시장가 범위 적정",
    HIGH: "시장가 대비 고가",
    REVIEW_REQUIRED: "판정 대기",
  }[assessment];
}

function formatCollectedAt(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("ko-KR", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}

function summarize(
  lines: AnalysisLine[],
  rawCount: number,
  marketResults: Record<number, MarketLookupResult>,
) {
  const effectiveAssessment = (line: AnalysisLine) =>
    marketResults[line.raw_item_id]?.assessment ?? line.assessment;
  const count = (assessment: string) =>
    lines.filter((line) => effectiveAssessment(line) === assessment).length;
  const matched = lines.filter((line) => line.match_status === "MATCHED").length;
  const market = lines.filter((line) =>
    line.market_price_lookup_required
    && (!marketResults[line.raw_item_id]
      || marketResults[line.raw_item_id].assessment === "REVIEW_REQUIRED")
  ).length;
  const pending = count("REVIEW_REQUIRED");
  const assessedAmount = lines.reduce(
    (sum, line) => sum + (
      line.match_status === "MATCHED"
      || (marketResults[line.raw_item_id]
        && marketResults[line.raw_item_id].assessment !== "REVIEW_REQUIRED")
        ? numericAmount(line)
        : 0
    ),
    0,
  );
  const totalAmount = lines.reduce((sum, line) => sum + numericAmount(line), 0);
  const high = count("HIGH");
  const review = count("REVIEW");
  return {
    rawCount,
    matched,
    market,
    pending,
    high,
    within: count("WITHIN_RANGE"),
    low: count("LOW"),
    review,
    coverage: totalAmount > 0 ? assessedAmount / totalAmount : 0,
    overall: high > 0 ? "고가 품목 검토 필요" : review > 0 || pending > 0 ? "주의·판정 대기 포함" : "적정 범위",
    overallTone: high > 0 ? "high" : review > 0 || pending > 0 ? "pending" : "within",
  };
}

function numericAmount(line: AnalysisLine) {
  const amount = line.quote_amount === null ? Number.NaN : Number(line.quote_amount);
  if (Number.isFinite(amount)) return amount;
  const quantity = line.quantity === null ? Number.NaN : Number(line.quantity);
  const price = line.quote_unit_price === null ? Number.NaN : Number(line.quote_unit_price);
  return Number.isFinite(quantity) && Number.isFinite(price) ? quantity * price : 0;
}

function lineMatches(
  line: AnalysisLine,
  filter: ResultFilter,
  market?: MarketLookupResult,
) {
  const assessment = market?.assessment ?? line.assessment;
  if (filter === "ALL") return true;
  if (filter === "MATCHED") return line.match_status === "MATCHED";
  if (filter === "MARKET") return line.market_price_lookup_required;
  if (filter === "PENDING") return assessment === "REVIEW_REQUIRED";
  return assessment === filter;
}

function formatMoney(value: string | null) {
  if (value === null) return "—";
  const number = Number(value);
  return Number.isFinite(number)
    ? `${new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 }).format(number)}원`
    : "—";
}

function formatNumber(value: string | null) {
  if (value === null) return "—";
  const number = Number(value);
  return Number.isFinite(number)
    ? new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 }).format(number)
    : "—";
}

function formatPercent(value: number) {
  return new Intl.NumberFormat("ko-KR", {
    style: "percent",
    maximumFractionDigits: 1,
  }).format(value);
}

function formatSignedMoney(value: string | null) {
  if (value === null) return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const formatted = new Intl.NumberFormat("ko-KR", {
    maximumFractionDigits: 2,
  }).format(Math.abs(number));
  return `${number > 0 ? "+" : number < 0 ? "−" : ""}${formatted}원`;
}

function formatSignedPercent(value: string | null) {
  if (value === null) return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const formatted = new Intl.NumberFormat("ko-KR", {
    maximumFractionDigits: 2,
  }).format(Math.abs(number));
  return `${number > 0 ? "+" : number < 0 ? "−" : ""}${formatted}%`;
}

function matchStatusLabel(status: AnalysisLine["match_status"]) {
  const labels: Record<AnalysisLine["match_status"], string> = {
    MATCHED: "표준 DB 근거 매칭",
    MATCHED_NO_PRICE: "표준단가 없음",
    NO_MATCH: "매칭 없음",
    CANDIDATE: "유사 후보 검토",
    EXCLUDED: "정제 제외",
    REVIEW_REQUIRED: "정제 판정대기",
  };
  return labels[status];
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}
