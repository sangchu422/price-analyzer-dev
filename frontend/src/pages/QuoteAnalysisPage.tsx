import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";

import { safeNextCursor, uniqueByRawItemId } from "../api/pagination";
import { LoadingLabel } from "../components/LoadingLabel";
import {
  ApiError,
  activateQuoteAnalysisRun,
  createQuoteAnalysisRun,
  getStandardEvidence,
  lookupMarketPrice,
  lookupMarketPriceBatch,
  submitIncomingBid,
  type AnalysisAssessment,
  type AnalysisLine,
  type MarketLookupResult,
  type EquipmentAnalysisGroup,
  type QuoteAnalysisRun,
  type SubmissionResponse,
} from "../api/client";
import type {
  MarketLookupProgress,
  MarketLookupProgressItem,
  QuoteAnalysisWorkflowState,
  ResultFilter,
  WorkflowStage,
} from "../state/quoteAnalysisState";

const assessmentLabels: Record<AnalysisAssessment, string> = {
  NOT_APPLICABLE: "판정 제외",
  REVIEW_REQUIRED: "판정 대기",
  LOW: "저가",
  WITHIN_RANGE: "적정",
  REVIEW: "주의",
  HIGH: "고가",
};

export function QuoteAnalysisPage({
  workflow,
}: {
  workflow: QuoteAnalysisWorkflowState;
}) {
  useEffect(() => {
    document.title = "신규 견적 분석 · 통합 견적 분석 시스템";
    return () => {
      document.title = "통합 견적 분석 시스템";
    };
  }, []);

  const {
    file,
    setFile,
    submittedBy,
    setSubmittedBy,
    reviewPercent,
    setReviewPercent,
    highPercent,
    setHighPercent,
    stage,
    setStage,
    submission,
    setSubmission,
    analysis,
    setAnalysis,
    error,
    setError,
    resultFilter,
    setResultFilter,
    marketResults,
    setMarketResults,
    marketLookupItems,
    setMarketLookupItems,
    marketLookupProgress,
    setMarketLookupProgress,
    validationError,
    setValidationError,
  } = workflow;
  const uploadController = useRef<AbortController | null>(null);
  const analysisController = useRef<AbortController | null>(null);
  const marketLookupController = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const busy = stage !== "IDLE";

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      uploadController.current?.abort();
      analysisController.current?.abort();
      marketLookupController.current?.abort();
      setStage("IDLE");
    };
  }, [setStage]);

  const startAnalysis = async (retryAccepted = false) => {
    if (!file) {
      setValidationError("견적서 파일을 선택해 주세요.");
      return;
    }
    const actor = submittedBy.trim() || "익명";
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
    setMarketLookupItems({});
    setMarketLookupProgress(null);
    uploadController.current?.abort();
    analysisController.current?.abort();
    marketLookupController.current?.abort();
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
          actor,
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
        createdBy: actor,
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

  const autoMarketLookupIds = useMemo(
    () => (
      analysis?.lines
        .filter((line) => line.market_price_lookup_required)
        .map((line) => line.raw_item_id)
        ?? []
    ),
    [analysis],
  );

  useEffect(() => {
    if (autoMarketLookupIds.length === 0) {
      return;
    }
    const controller = new AbortController();
    marketLookupController.current = controller;
    const pending: Record<number, MarketLookupProgressItem> = {};
    for (const rawItemId of autoMarketLookupIds) {
      pending[rawItemId] = {
        status: "PENDING",
        detail: null,
      };
    }
    void Promise.resolve()
      .then(() => {
        if (controller.signal.aborted) return null;
        setMarketLookupItems(pending);
        setMarketLookupProgress({
          state: "PENDING",
          total: autoMarketLookupIds.length,
          completed: 0,
          unavailable: 0,
        });
        return lookupMarketPriceBatch(
          autoMarketLookupIds,
          analysis!.run_id,
          false,
          controller.signal,
        );
      })
      .then((response) => {
        if (!response || !mounted.current || controller.signal.aborted) return;
        const next: Record<number, MarketLookupProgressItem> = {};
        const resultById: Record<number, MarketLookupResult> = {};
        for (const item of response.items) {
          next[item.raw_item_id] = item;
          if (item.result) resultById[item.raw_item_id] = item.result;
        }
        for (const rawItemId of autoMarketLookupIds) {
          if (!next[rawItemId]) {
            next[rawItemId] = {
              raw_item_id: rawItemId,
              status: "NOT_FOUND",
              detail: "시장가 자동 조회 결과가 없습니다.",
            };
          }
        }
        setMarketLookupItems(next);
        if (Object.keys(resultById).length > 0) {
          setMarketResults((current) => ({ ...current, ...resultById }));
        }
        setMarketLookupProgress({
          state: "COMPLETE",
          total: autoMarketLookupIds.length,
          completed: response.completed,
          unavailable: response.unavailable,
        });
      })
      .catch((caught) => {
        if (controller.signal.aborted || isAbortError(caught)) return;
        const detail = caught instanceof Error
          ? caught.message
          : "시장가 자동 조회에 실패했습니다.";
        const unavailable: Record<number, MarketLookupProgressItem> = {};
        for (const rawItemId of autoMarketLookupIds) {
          unavailable[rawItemId] = {
            raw_item_id: rawItemId,
            status: "SOURCE_UNAVAILABLE",
            detail,
          };
        }
        setMarketLookupItems(unavailable);
        setMarketLookupProgress({
          state: "ERROR",
          total: autoMarketLookupIds.length,
          completed: 0,
          unavailable: autoMarketLookupIds.length,
        });
      });
    return () => {
      controller.abort();
      if (marketLookupController.current === controller) {
        marketLookupController.current = null;
      }
    };
  }, [
    analysis,
    autoMarketLookupIds,
    setMarketLookupItems,
    setMarketLookupProgress,
    setMarketResults,
  ]);

  return (
    <main className="workspace-page analysis-page">
      <header className="page-heading analysis-page-heading">
        <div>
          <p className="section-kicker">신규 견적 접수</p>
          <h1>신규 견적 분석</h1>
        </div>
        <p>
          품목류 또는 상세 품목 기준으로 표준 DB와 비교합니다. 신뢰할 비교군이
          없는 품목은 가격을 만들지 않고 판정대기로 남깁니다.
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
              setMarketLookupItems({});
              setMarketLookupProgress(null);
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
            placeholder="예: 홍길동"
            onChange={(event) => setSubmittedBy(event.target.value)}
          />
          <small className="submitter-helper">미입력 시 익명으로 기록됩니다.</small>
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
          <LoadingLabel>
            {stage === "PARSING"
              ? "견적서를 업로드하고 품목을 파싱하는 중입니다."
              : "표준 DB와 견적 품목을 비교하는 중입니다."}
          </LoadingLabel>
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
          filter={resultFilter}
          onFilter={setResultFilter}
          marketResults={marketResults}
          marketLookupItems={marketLookupItems}
          marketLookupProgress={marketLookupProgress}
          onMarketResult={(result) => setMarketResults((current) => ({
            ...current,
            [result.raw_item_id]: result,
          }))}
          reviewPercent={reviewPercent}
          highPercent={highPercent}
          submittedBy={submittedBy.trim() || "익명"}
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
  filter,
  onFilter,
  marketResults,
  marketLookupItems,
  marketLookupProgress,
  onMarketResult,
  reviewPercent,
  highPercent,
  submittedBy,
}: {
  analysis: QuoteAnalysisRun;
  submission: SubmissionResponse;
  filter: ResultFilter;
  onFilter: (filter: ResultFilter) => void;
  marketResults: Record<number, MarketLookupResult>;
  marketLookupItems: Record<number, MarketLookupProgressItem>;
  marketLookupProgress: MarketLookupProgress | null;
  onMarketResult: (result: MarketLookupResult) => void;
  reviewPercent: number;
  highPercent: number;
  submittedBy: string;
}) {
  const [activeTab, setActiveTab] = useState<"EQUIPMENT" | "THRESHOLD" | "TARGET">("EQUIPMENT");
  const family = analysis.family_analysis;
  const [analysisBasis, setAnalysisBasis] = useState<"FAMILY" | "EXACT">(
    () => family ? "FAMILY" : "EXACT",
  );
  const displayedAnalysis = useMemo<QuoteAnalysisRun>(() => {
    if (analysisBasis !== "FAMILY" || !family) return analysis;
    return {
      ...analysis,
      lines: family.lines,
      target_lines: family.target_lines,
      target_available_count: family.target_available_count,
      target_unavailable_count: family.target_unavailable_count,
      quote_total_amount: family.quote_total_amount,
      target_total_amount: family.target_total_amount,
      equipment_groups: family.equipment_groups,
    };
  }, [analysis, analysisBasis, family]);
  const displayedMarketResults = useMemo<Record<number, MarketLookupResult>>(
    () => analysisBasis === "FAMILY" ? {} : marketResults,
    [analysisBasis, marketResults],
  );
  const metrics = useMemo(
    () => summarize(displayedAnalysis.lines, submission.raw_item_count, displayedMarketResults),
    [displayedAnalysis.lines, displayedMarketResults, submission.raw_item_count],
  );
  const lines = useMemo(
    () => displayedAnalysis.lines.filter((line) => lineMatches(line, filter, displayedMarketResults[line.raw_item_id])),
    [displayedAnalysis.lines, displayedMarketResults, filter],
  );
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

      <div className="analysis-basis-switch" role="group" aria-label="가격 분석 기준">
        <button
          type="button"
          className={analysisBasis === "FAMILY" ? "is-active" : ""}
          aria-pressed={analysisBasis === "FAMILY"}
          disabled={!family}
          onClick={() => setAnalysisBasis("FAMILY")}
        >
          <span>품목류 기준</span>
          <small>{family ? `${family.matched_count}개 품목 분석` : "품목류 결과 준비 중"}</small>
        </button>
        <button type="button" className={analysisBasis === "EXACT" ? "is-active" : ""} aria-pressed={analysisBasis === "EXACT"} onClick={() => setAnalysisBasis("EXACT")}>
          <span>상세 품목 기준</span>
          <small>품명·사양·단위가 같은 근거</small>
        </button>
      </div>

      <div className="analysis-mode-tabs" role="tablist" aria-label="견적 분석 방식">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "EQUIPMENT"}
          className={activeTab === "EQUIPMENT" ? "is-active" : ""}
          onClick={() => setActiveTab("EQUIPMENT")}
        >
          설비별 협상
          <small>갑지 설비명으로 합산하고 품목을 펼쳐 확인</small>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "THRESHOLD"}
          className={activeTab === "THRESHOLD" ? "is-active" : ""}
          onClick={() => setActiveTab("THRESHOLD")}
        >
          가격 적정성
          <small>{analysisBasis === "FAMILY" ? "동일 단위·유사 가격대 품목류 비교" : "상세 품명·사양의 기준가와 비교"}</small>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "TARGET"}
          className={activeTab === "TARGET" ? "is-active" : ""}
          onClick={() => setActiveTab("TARGET")}
        >
          구매 목표가
          <small>{analysisBasis === "FAMILY" ? "유사 가격대의 낮은 중앙값으로 협상선 제시" : "검증된 과거 최저가를 현재 가치로 환산"}</small>
        </button>
      </div>

      {activeTab === "EQUIPMENT" ? (
        <EquipmentResults analysis={displayedAnalysis} submittedBy={submittedBy} analysisBasis={analysisBasis} />
      ) : activeTab === "THRESHOLD" ? (
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
        {analysisBasis === "FAMILY"
          ? `품목류 중앙값 대비 ±${reviewPercent}% 이내 적정, ±${reviewPercent}~${highPercent}% 주의, ±${highPercent}% 초과 고가·저가`
          : analysis.price_policy?.description ??
          "표준 대비 ±10% 이내 적정, ±10% 초과~±20% 주의, ±20% 초과 고가·저가"}
        . 판정 대기는 정제 미완료 또는 기준가가 없어 가격을 판단하지 못한 품목입니다.
      </p>

      {analysisBasis === "EXACT" ? <div className="market-roadmap">
        <strong>{`시장가 확인 필요 ${metrics.market}건`}</strong>
        <span>DeviceMart 캐시 우선 조회</span>
        {marketLookupProgress && (
          <small className="market-batch-progress" role="status">
            {marketLookupProgressLabel(marketLookupProgress)}
          </small>
        )}
        <p>캐시가 없거나 만료된 품목만 실시간 조회하며, 실패하면 가격을 만들지 않고 판정대기로 유지합니다.</p>
      </div> : null}

      <div className="result-toolbar">
        <div>
          <h3>품목별 판정</h3>
          <span>표시 {lines.length}건 / 전체 {displayedAnalysis.lines.length}건</span>
        </div>
        <div className="result-toolbar-actions">
          {analysisBasis === "EXACT" ? (
            <a className="table-export-link" href={`/api/analysis/documents/${analysis.document.id}/export?review_percent=${reviewPercent}&high_percent=${highPercent}`}>
              엑셀 다운로드
            </a>
          ) : <span className="analysis-basis-note">품목류 시연 기준</span>}
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
      </div>

      <div className="analysis-table-scroll">
        <table className="analysis-result-table" aria-label="가격 적정성 품목별 판정">
          <thead>
            <tr>
              <th>품목 / 사양</th>
              <th>단위·수량</th>
              <th>개당 단가</th>
              <th>구매 금액</th>
              <th>참조 최저·중앙값·최고</th>
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
                market={displayedMarketResults[line.raw_item_id] ?? null}
                marketLookup={marketLookupItems[line.raw_item_id]}
                onMarketResult={onMarketResult}
                analysisRunId={analysis.run_id}
              />
            ))}
          </tbody>
        </table>
        {lines.length === 0 && <p className="inline-state">선택한 조건에 맞는 품목이 없습니다.</p>}
      </div>
        </>
      ) : (
        <TargetPriceResults analysis={displayedAnalysis} analysisBasis={analysisBasis} />
      )}
    </section>
  );
}

function EquipmentResults({
  analysis,
  submittedBy,
  analysisBasis,
}: {
  analysis: QuoteAnalysisRun;
  submittedBy: string;
  analysisBasis: "FAMILY" | "EXACT";
}) {
  const [selectedEquipmentId, setSelectedEquipmentId] = useState<number | null>(null);
  const equipmentModalRef = useRef<HTMLDivElement>(null);
  const equipmentReturnFocusRef = useRef<HTMLElement | null>(null);
  const [activationOpen, setActivationOpen] = useState(false);
  const [activatedBy, setActivatedBy] = useState(submittedBy === "익명" ? "" : submittedBy);
  const [reason, setReason] = useState("신규 견적 검토 완료 및 표준 DB 반영");
  const [sendOutlook, setSendOutlook] = useState(false);
  const [recipient, setRecipient] = useState("");
  const [activationState, setActivationState] = useState<
    { kind: "idle" | "pending" | "success" | "error"; message: string }
  >({ kind: "idle", message: "" });
  const lineById = useMemo(
    () => new Map(analysis.lines.map((line) => [line.raw_item_id, line])),
    [analysis.lines],
  );
  const targetById = useMemo(
    () => new Map(analysis.target_lines.map((line) => [line.raw_item_id, line])),
    [analysis.target_lines],
  );
  const equipmentGroups = analysis.equipment_groups ?? [];
  const selectedEquipment = equipmentGroups.find((group) => group.id === selectedEquipmentId) ?? null;
  const totals = equipmentGroups.reduce(
    (sum, group) => ({
      quote: sum.quote + Number(group.quote_amount),
      target: sum.target + Number(group.target_amount),
      negotiation: sum.negotiation + Number(group.negotiation_amount),
    }),
    { quote: 0, target: 0, negotiation: 0 },
  );

  const closeEquipmentModal = () => {
    setSelectedEquipmentId(null);
    window.setTimeout(() => equipmentReturnFocusRef.current?.focus(), 0);
  };

  const openEquipmentModal = (group: EquipmentAnalysisGroup) => {
    equipmentReturnFocusRef.current = document.activeElement as HTMLElement | null;
    setSelectedEquipmentId(group.id);
  };

  useEffect(() => {
    if (selectedEquipmentId === null) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    equipmentModalRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSelectedEquipmentId(null);
        window.setTimeout(() => equipmentReturnFocusRef.current?.focus(), 0);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [selectedEquipmentId]);

  const activate = async () => {
    setActivationState({ kind: "pending", message: "표준 DB 반영 이력을 생성하는 중입니다." });
    try {
      const result = await activateQuoteAnalysisRun({
        runId: analysis.run_id,
        activatedBy: activatedBy.trim(),
        reasonDetail: reason,
        sendOutlook,
        outlookRecipient: recipient,
      });
      const alertMessage = result.alerts.length > 0
        ? ` 가격 변동 주의 ${result.alerts.length}건도 함께 기록했습니다.`
        : "";
      setActivationState({
        kind: "success",
        message: `표준 DB 반영 완료: ${result.entries.length}개 품목 처리.${alertMessage}`,
      });
    } catch (error) {
      setActivationState({
        kind: "error",
        message: error instanceof Error ? error.message : "표준 DB 반영에 실패했습니다.",
      });
    }
  };

  return (
    <section className="equipment-results" role="tabpanel">
      <div className="equipment-summary-band">
        <div><span>상세 시트 견적 합계</span><strong>{formatMoney(String(totals.quote))}</strong></div>
        <div><span>전체 구매 목표금액</span><strong>{formatMoney(String(totals.target))}</strong></div>
        <div className="is-negotiation"><span>목표 인하 금액</span><strong>{formatMoney(String(totals.negotiation))}</strong></div>
        {analysisBasis === "EXACT" ? (
          <button type="button" onClick={() => setActivationOpen((open) => !open)}>
            검토 완료 · 표준 DB 반영
          </button>
        ) : <span className="equipment-basis-badge">품목류 가격군 적용</span>}
      </div>

      {analysisBasis === "EXACT" && activationOpen ? (
        <section className="quote-activation-panel" aria-label="신규 견적 표준 DB 반영">
          <div>
            <strong>담당자 승인 후 데이터 축적</strong>
            <p>원본과 분석 이력은 그대로 두고 새 가격 버전을 추가합니다. 기존 중앙값 대비 ±20% 이상이면 주의 알림을 기록합니다.</p>
          </div>
          <label><span>반영 담당자</span><input value={activatedBy} onChange={(event) => setActivatedBy(event.target.value)} placeholder="이름 또는 사번" /></label>
          <label><span>반영 사유</span><input value={reason} onChange={(event) => setReason(event.target.value)} /></label>
          <label className="activation-outlook-toggle">
            <input type="checkbox" checked={sendOutlook} onChange={(event) => setSendOutlook(event.target.checked)} />
            <span>가격 변동 주의를 Outlook으로 발송</span>
          </label>
          {sendOutlook ? (
            <label><span>수신 이메일</span><input type="email" value={recipient} onChange={(event) => setRecipient(event.target.value)} placeholder="buyer@company.com" /></label>
          ) : null}
          <button type="button" disabled={activationState.kind === "pending" || activatedBy.trim().length < 2 || reason.trim().length < 3 || (sendOutlook && !recipient.includes("@"))} onClick={() => void activate()}>
            {activationState.kind === "pending" ? "반영 중…" : "승인하고 표준 DB에 추가"}
          </button>
          {activationState.kind !== "idle" ? <p className={`activation-result is-${activationState.kind}`} role={activationState.kind === "error" ? "alert" : "status"}>{activationState.message}</p> : null}
        </section>
      ) : null}

      <div className="result-toolbar equipment-toolbar">
        <div><h3>설비별 구매 목표</h3><span>상세 시트 기준 {equipmentGroups.length}개 설비</span></div>
        <small>시트별 품목 금액을 직접 합산하고 갑지 설비명으로 표시합니다.</small>
      </div>
      <div className="analysis-table-scroll equipment-table-scroll">
        <table className="analysis-result-table equipment-summary-table" aria-label="갑지 설비별 견적 분석">
          <thead><tr><th>설비명</th><th>견적가</th><th>구매 목표금액</th><th>목표 인하 금액</th><th>분석 상태</th><th>상세</th></tr></thead>
          <tbody>
            {equipmentGroups.length === 0 ? (
              <tr><td colSpan={6} className="equipment-empty-state">이 분석 이력에는 설비 갑지 연결 정보가 없습니다. 가격 적정성 탭에서 품목별 결과를 확인해 주세요.</td></tr>
            ) : null}
            {equipmentGroups.map((group) => (
                  <tr className="equipment-group-row" key={group.id}>
                    <td><strong>{group.name}</strong><small>{group.line_count}개 품목 · {group.source_kind === "COVER_SHEET" ? "갑지 설비명" : "시트명"}</small></td>
                    <td className="numeric">{formatMoney(group.quote_amount)}</td>
                    <td className="numeric is-emphasis">{formatMoney(group.target_amount)}</td>
                    <td className="numeric is-negotiation">{formatMoney(group.negotiation_amount)}</td>
                    <td><span className={group.target_available_count === group.line_count ? "equipment-status is-complete" : "equipment-status"}>{group.target_available_count === group.line_count ? "분석 완료" : `근거 보완 ${group.line_count - group.target_available_count}건`}</span></td>
                    <td><button type="button" aria-haspopup="dialog" onClick={() => openEquipmentModal(group)}>상세</button></td>
                  </tr>
            ))}
          </tbody>
          <tfoot><tr className="target-total-row"><td>합계</td><td className="numeric"><strong>{formatMoney(String(totals.quote))}</strong></td><td className="numeric"><strong>{formatMoney(String(totals.target))}</strong></td><td className="numeric is-negotiation"><strong>{formatMoney(String(totals.negotiation))}</strong></td><td colSpan={2}>설비 상세에서 품목별 근거 확인</td></tr></tfoot>
        </table>
      </div>
      {selectedEquipment ? (
        <div className="equipment-detail-overlay is-open" onMouseDown={(event) => { if (event.target === event.currentTarget) closeEquipmentModal(); }}>
          <div ref={equipmentModalRef} className="equipment-detail-modal t-modal is-open" role="dialog" aria-modal="true" aria-labelledby="equipment-detail-title" tabIndex={-1}>
            <header className="equipment-detail-modal-bar">
              <div><span>설비별 구매 목표</span><strong id="equipment-detail-title">{selectedEquipment.name}</strong><small>{selectedEquipment.line_count}개 품목 · {selectedEquipment.source_kind === "COVER_SHEET" ? "갑지 설비명" : "시트명"}</small></div>
              <button type="button" onClick={closeEquipmentModal} aria-label="설비 상세 닫기">닫기</button>
            </header>
            <div className="equipment-detail-modal-scroll">
              <dl className="equipment-detail-summary">
                <div><dt>설비 견적가</dt><dd>{formatMoney(selectedEquipment.quote_amount)}</dd></div>
                <div><dt>구매 목표금액</dt><dd>{formatMoney(selectedEquipment.target_amount)}</dd></div>
                <div className="is-negotiation"><dt>목표 인하 금액</dt><dd>{formatMoney(selectedEquipment.negotiation_amount)}</dd></div>
                <div><dt>분석 완료</dt><dd>{selectedEquipment.target_available_count} / {selectedEquipment.line_count}개</dd></div>
              </dl>
              <div className="equipment-detail-grid">
                <header><span>품목 / 사양</span><span>수량·단위</span><span>구매 금액</span><span>구매 목표금액</span><span>목표 인하 금액</span></header>
                {selectedEquipment.lines.map((groupLine) => {
                  const line = lineById.get(groupLine.raw_item_id);
                  const target = targetById.get(groupLine.raw_item_id);
                  return (
                    <div key={groupLine.raw_item_id}>
                      <span><strong>{line?.item_name ?? `품목 #${groupLine.raw_item_id}`}</strong><small>{line?.spec || "원문 규격 없음"}</small></span>
                      <span>{formatUnitQuantity(line?.unit ?? null, line?.quantity ?? null)}</span>
                      <span>{formatMoney(groupLine.quote_amount)}</span>
                      <span>{formatMoney(groupLine.target_amount)}</span>
                      <span className="is-negotiation">{formatMoney(groupLine.negotiation_amount)}</span>
                      {target?.status !== "AVAILABLE" ? <em>{target?.reason ?? "산정 근거 확인 필요"}</em> : null}
                    </div>
                  );
                })}
                {Number(selectedEquipment.unallocated_amount) > 0 ? (
                  <footer>공통 전기비·경비 등 품목 외 금액 <strong>{formatMoney(selectedEquipment.unallocated_amount)}</strong>은 갑지 금액에 유지했습니다.</footer>
                ) : null}
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function TargetPriceResults({ analysis, analysisBasis }: { analysis: QuoteAnalysisRun; analysisBasis: "FAMILY" | "EXACT" }) {
  const lineById = useMemo(
    () => new Map(analysis.lines.map((line) => [line.raw_item_id, line])),
    [analysis.lines],
  );
  const coverage = analysis.lines.length === 0
    ? 0
    : analysis.target_available_count / analysis.lines.length;
  const isLegacyPpi = analysis.inflation_series_kind === "PPI_ALL";
  const totalQuoteAmount = useMemo(
    () => analysis.lines.reduce((sum, line) => sum + Number(line.quote_amount ?? 0), 0),
    [analysis.lines],
  );
  const targetCoveredQuoteAmount = useMemo(
    () => analysis.target_lines.reduce((sum, target) => {
      if (target.target_amount === null) return sum;
      const line = lineById.get(target.raw_item_id);
      return sum + Number(line?.quote_amount ?? 0);
    }, 0),
    [analysis.target_lines, lineById],
  );
  const totalNegotiableAmount = useMemo(
    () => analysis.target_lines.reduce(
      (sum, target) => sum + negotiationAmount(target),
      0,
    ),
    [analysis.target_lines],
  );
  const overallNegotiationTarget = totalQuoteAmount - totalNegotiableAmount;

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
          <small>{analysisBasis === "FAMILY" ? "동일 단위·유사 가격대 비교군 부족" : "날짜·지수·표준 DB 근거 부족"}</small>
        </div>
        <div>
          <span>목표 인하 금액</span>
          <strong>{formatMoney(String(totalNegotiableAmount))}</strong>
          <small>제시가가 목표가보다 높은 품목만 합산</small>
        </div>
        <div className={totalNegotiableAmount > 0 ? "is-saving" : ""}>
          <span>전체 구매 목표금액</span>
          <strong>{formatMoney(String(overallNegotiationTarget))}</strong>
          <small>전체 견적에서 목표 인하 금액을 차감</small>
        </div>
      </div>

      <details className="target-method-disclosure">
        <summary>구매 목표가 산정 방식 보기</summary>
        <div>
          <strong>
            {analysisBasis === "FAMILY"
              ? "같은 품목류 중 동일 단위·유사 가격대의 낮은 가격을 구매 목표로 사용합니다."
              : isLegacyPpi
              ? "이 결과는 과거 목표가 정책으로 계산된 기록입니다."
              : "서로 다른 과거 견적에서 실제 확인된 최저 단가를 물가 보정해 구매 목표로 사용합니다."}
          </strong>
          <p>
            {analysisBasis === "FAMILY"
              ? "현재 단가의 ±30% 범위에 있는 상세 품목 중앙값만 비교해, 단위나 가격대가 다른 품목의 최저가가 섞이지 않도록 제한합니다."
              : isLegacyPpi
              ? "이 결과는 과거 실행 당시 저장된 생산자물가지수 기준으로 재현한 기록입니다."
              : analysis.target_period
              ? `원본 날짜가 확인된 과거 단가를 ${analysis.target_period.slice(0, 4)}년 확정 소비자물가까지 보정한 뒤 가장 낮은 단가를 채택합니다.`
              : "소비자물가 자료가 없어 목표가를 계산할 수 없습니다."}
            {analysis.inflation_source_last_changed ? (
              <> 최종 공표일은 {analysis.inflation_source_last_changed}입니다.</>
            ) : null}
          </p>
          <p>
            현재 제시가가 이미 구매 목표가보다 낮으면 목표 인하 금액은 0원으로 처리합니다.
            중앙값과 가격 범위는 ‘가격 적정성’ 탭에서 별도로 확인할 수 있습니다.
          </p>
          {analysisBasis === "EXACT" && analysis.inflation_source_url ? (
            <a href={analysis.inflation_source_url} target="_blank" rel="noreferrer">
              KOSIS 공식 통계 보기
            </a>
          ) : null}
        </div>
      </details>

      <div className="result-toolbar target-price-toolbar">
        <div>
          <h3>품목별 구매 목표가</h3>
          <span>표시 {analysis.target_lines.length}건</span>
        </div>
        {analysisBasis === "EXACT" ? (
          <a className="table-export-link" href={`/api/analysis/runs/${analysis.run_id}/target-price-export`}>
            엑셀 다운로드
          </a>
        ) : <span className="analysis-basis-note">품목류 시연 기준</span>}
      </div>

      <div className="analysis-table-scroll">
        <table className="analysis-result-table target-price-table" aria-label="구매 목표가 품목별 산정">
          <thead>
            <tr>
              <th>품목 / 사양</th>
              <th>수량</th>
              <th>개당 단가</th>
              <th>구매 금액</th>
              <th>구매 목표 단가(개당)</th>
              <th>구매 목표금액</th>
              <th>목표 인하 금액</th>
              <th>산정 근거</th>
            </tr>
          </thead>
          <tbody>
            {analysis.target_lines.map((target) => (
              <TargetPriceRow
                key={target.raw_item_id}
                target={target}
                line={lineById.get(target.raw_item_id)}
                targetPeriod={analysis.target_period}
                inflationSeriesKind={analysis.inflation_series_kind}
              />
            ))}
          </tbody>
          <tfoot>
            <tr className="target-total-row">
              <td colSpan={3}>전체 견적 합계</td>
              <td className="numeric"><strong>{formatMoney(String(totalQuoteAmount))}</strong></td>
              <td className="numeric">—</td>
              <td className="numeric"><strong>{formatMoney(String(overallNegotiationTarget))}</strong></td>
              <td className="numeric"><strong>{formatMoney(String(totalNegotiableAmount))}</strong></td>
              <td>
                목표가 산정 가능 품목의 구매금액 {formatMoney(String(targetCoveredQuoteAmount))}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}

function TargetPriceRow({
  target,
  line,
  targetPeriod,
  inflationSeriesKind,
}: {
  target: QuoteAnalysisRun["target_lines"][number];
  line: AnalysisLine | undefined;
  targetPeriod: string | null;
  inflationSeriesKind: string | null | undefined;
}) {
  const [open, setOpen] = useState(false);
  const cellRef = useRef<HTMLTableCellElement>(null);
  const usesAggressiveMinimum = inflationSeriesKind !== "PPI_ALL";
  const rankedEvidence = usesAggressiveMinimum
    ? [...target.evidence].sort(
        (left, right) =>
          Number(left.adjusted_unit_price) - Number(right.adjusted_unit_price) ||
          right.quote_date.localeCompare(left.quote_date) ||
          left.raw_item_id - right.raw_item_id,
      )
    : target.evidence;
  const effectiveTargetUnitPrice = buyerTargetValue(
    line?.quote_unit_price ?? null,
    target.target_unit_price,
  );
  const effectiveTargetAmount = buyerTargetValue(
    line?.quote_amount ?? null,
    target.target_amount,
  );
  const negotiableAmount = negotiationAmount(target);

  useEffect(() => {
    if (!open) return;
    const onClickOutside = (event: MouseEvent) => {
      if (cellRef.current && !cellRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [open]);

  return (
    <tr>
      <td>
        <strong>{line?.item_name ?? "품명 없음"}</strong>
        <span>{line?.spec ?? "원본에 규격 없음"}</span>
      </td>
      <td className="numeric">{formatUnitQuantity(line?.unit ?? null, line?.quantity ?? null)}</td>
      <td className="numeric">{formatMoney(line?.quote_unit_price ?? null)}</td>
      <td className="numeric">{formatMoney(line?.quote_amount ?? null)}</td>
      <td className="numeric target-unit-price">{formatMoney(effectiveTargetUnitPrice)}</td>
      <td className="numeric">{formatMoney(effectiveTargetAmount)}</td>
      <td className="numeric">
        <strong>
          {formatMoney(
            target.status === "AVAILABLE"
              ? numberString(negotiableAmount)
              : null,
          )}
        </strong>
        {target.status === "AVAILABLE" && negotiableAmount === 0 ? (
          <span>구매 목표가 이하</span>
        ) : null}
      </td>
      <td className="target-evidence-trigger" ref={cellRef}>
        <button
          type="button"
          className="target-evidence-trigger-button"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          {target.status === "AVAILABLE"
            ? `${usesAggressiveMinimum ? "최저가 근거 · " : ""}독립 원본 ${target.used_observation_count}건${target.used_observation_count === 1 ? " · 신뢰도 낮음" : ""}`
            : targetStatusLabel(target.status)}
        </button>
        {open && (
          <div className="target-evidence-popover" role="dialog">
            <p>{target.reason}</p>
            {rankedEvidence.map((evidence, index) => (
              <a
                key={evidence.raw_item_id}
                className={usesAggressiveMinimum && index === 0 ? "is-selected-target" : undefined}
                href={`/api/documents/variants/${evidence.source_variant_id}/file${evidence.source_page ? `#page=${evidence.source_page}` : ""}`}
                target="_blank"
                rel="noreferrer"
              >
                {usesAggressiveMinimum && index === 0 ? <strong className="target-selection-label">구매 목표로 채택</strong> : null}
                <span>{conciseSourceName(evidence.source_logical_name)}</span>
                <small>{evidence.quote_date} · {formatMoney(evidence.original_unit_price)} → {formatMoney(evidence.adjusted_unit_price)}</small>
                <small className="inflation-evidence-detail">
                  {inflationEvidenceLabel(evidence, targetPeriod, inflationSeriesKind)}
                </small>
              </a>
            ))}
          </div>
        )}
      </td>
    </tr>
  );
}

function inflationEvidenceLabel(
  evidence: QuoteAnalysisRun["target_lines"][number]["evidence"][number],
  targetPeriod: string | null,
  seriesKind?: string | null,
) {
  const inflation = evidence.inflation;
  if (inflation) {
    const rates = inflation.annual_rates
      .map(({ year, rate }) => `${year}년 ${Number(rate).toLocaleString("ko-KR")}%`)
      .join(" · ");
    const factor = Number(inflation.factor);
    const cumulative = Number(inflation.cumulative_percent);
    const summary = [
      rates,
      Number.isFinite(cumulative)
        ? `누적 ${cumulative >= 0 ? "+" : ""}${cumulative.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}%`
        : null,
      Number.isFinite(factor)
        ? `보정계수 ×${factor.toLocaleString("ko-KR", { minimumFractionDigits: 4, maximumFractionDigits: 6 })}`
        : null,
    ].filter((value): value is string => Boolean(value));
    return summary.join(" · ");
  }
  const sourceIndex = Number(evidence.source_index_value);
  const targetIndex = Number(evidence.target_index_value);
  if (
    !targetPeriod
    || !Number.isFinite(sourceIndex)
    || !Number.isFinite(targetIndex)
    || sourceIndex <= 0
    || !evidence.source_period
  ) {
    return seriesKind === "PPI_ALL"
      ? "생산자물가지수 보정 정보 없음"
      : "소비자물가지수(CPI) 보정 정보 없음";
  }
  const factor = targetIndex / sourceIndex;
  const months = periodDistance(evidence.source_period, targetPeriod);
  const annualRate = months && months > 0
    ? Math.pow(factor, 12 / months) - 1
    : null;
  const indexText = sourceIndex.toLocaleString("ko-KR", {
    maximumFractionDigits: 3,
  }) + " → " + targetIndex.toLocaleString("ko-KR", {
    maximumFractionDigits: 3,
  });
  const factorText = factor.toLocaleString("ko-KR", {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  });
  return "생산자물가지수 " + formatPeriod(evidence.source_period)
    + " → " + formatPeriod(targetPeriod)
    + " · 지수 " + indexText
    + " · 보정계수 ×" + factorText
    + (annualRate === null
      ? ""
      : " · 연환산 보정률 " + formatPercent(annualRate));
}

function periodDistance(sourcePeriod: string, targetPeriod: string) {
  const source = periodNumber(sourcePeriod);
  const target = periodNumber(targetPeriod);
  return source === null || target === null ? null : target - source;
}

function periodNumber(value: string) {
  if (!/^[0-9]{6}$/.test(value)) return null;
  const year = Number(value.slice(0, 4));
  const month = Number(value.slice(4));
  return Number.isFinite(year) && month >= 1 && month <= 12
    ? year * 12 + month - 1
    : null;
}

function formatPeriod(value: string) {
  const period = periodNumber(value);
  if (period === null) return value;
  return value.slice(0, 4) + "년 " + Number(value.slice(4)) + "월";
}

function targetStatusLabel(status: QuoteAnalysisRun["target_lines"][number]["status"]) {
  return {
    AVAILABLE: "산정 완료",
    DATE_UNAVAILABLE: "원본 견적일 확인 필요",
    INDEX_UNAVAILABLE: "물가지수 갱신 필요",
    RATE_GAP: "연간 소비자물가 자료 누락",
    COMPARABILITY_REVIEW_REQUIRED: "규격·가격 범위 확인 필요",
    MARKET_REFERENCE_REQUIRED: "표준 DB 없음 · 시장가 별도 확인",
    NOT_APPLICABLE: "산정 제외",
  }[status];
}

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
  const [marketError, setMarketError] = useState("");
  const [marketLoading, setMarketLoading] = useState(false);
  const hasPriceEvidence =
    line.match_status === "MATCHED" &&
    line.standard_item_id !== null &&
    line.standard_price_version_id !== null;
  const standardEvidence = useInfiniteQuery({
    queryKey: [
      "analysis-standard-evidence",
      line.standard_item_id,
      line.standard_price_version_id,
    ],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      getStandardEvidence({
        standardItemId: line.standard_item_id!,
        priceVersionId: line.standard_price_version_id!,
        afterId: pageParam,
        signal,
      }),
    getNextPageParam: safeNextCursor,
    enabled: false,
    staleTime: Number.POSITIVE_INFINITY,
  });
  const standardEvidenceObservations = uniqueByRawItemId(
    standardEvidence.data?.pages.flatMap((page) => page.observations) ?? [],
  );

  const requestMarket = async (forceRefresh = false) => {
    setMarketLoading(true);
    setMarketError("");
    try {
      onMarketResult(
        await lookupMarketPrice(line.raw_item_id, analysisRunId, forceRefresh),
      );
    } catch (error) {
      setMarketError(error instanceof Error ? error.message : "시장가 조회에 실패했습니다.");
    } finally {
      setMarketLoading(false);
    }
  };
  const minimumPrice = market?.minimum_price ?? line.minimum_price;
  const middlePrice = market?.median_price ?? line.reference_price;
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
      <td className="numeric">{formatUnitQuantity(line.unit, line.quantity)}</td>
      <td className="numeric">{formatMoney(line.quote_unit_price)}</td>
      <td className="numeric">{formatMoney(line.quote_amount)}</td>
      <td className="reference-range">
        {line.match_status === "MATCHED" || market ? (
          <div className="reference-range-row">
            <span className="reference-range-value">
              <small>최저</small>
              <strong>{formatMoney(minimumPrice)}</strong>
            </span>
            <span className="reference-range-value is-basis">
              <small>중앙값</small>
              <strong>{formatMoney(middlePrice)}</strong>
            </span>
            <span className="reference-range-value">
              <small>최고</small>
              <strong>{formatMoney(maximumPrice)}</strong>
            </span>
            {hasPriceEvidence && (
              <span className="reference-evidence-count">
                근거 {line.standard_observation_count ?? 0}건
                {line.evidence_quality === "SINGLE_OBSERVATION" ? " · 제출사 1곳" : ""}
                {line.evidence_quality === "SUPPLIER_UNKNOWN" ? " · 제출사 확인 필요" : ""}
                {line.evidence_quality === "NON_COMPARABLE" ? " · 규격·가격 범위 확인 필요" : ""}
              </span>
            )}
          </div>
        ) : "—"}
      </td>
      <td className="numeric">{formatSignedMoney(varianceAmount)}</td>
      <td className="numeric">
        {formatSignedPercent(market?.variance_percent ?? line.variance_percent)}
      </td>
      <td>
        <div className="line-evidence">
          <div
            className={`match-badge is-${line.match_status.toLowerCase()}${hasPriceEvidence ? " reference-evidence-trigger" : ""}`}
            tabIndex={hasPriceEvidence ? 0 : undefined}
            onMouseEnter={() => {
              if (hasPriceEvidence && !standardEvidence.data) void standardEvidence.refetch();
            }}
            onFocus={() => {
              if (hasPriceEvidence && !standardEvidence.data) void standardEvidence.refetch();
            }}
          >
            {matchStatusLabel(line.match_status)}
            {hasPriceEvidence && (
              <div className="reference-evidence-popover" role="tooltip">
                <strong>표준단가 원본 근거</strong>
                {standardEvidence.isFetching && !standardEvidence.isFetchingNextPage && (
                  <LoadingLabel>불러오는 중…</LoadingLabel>
                )}
                {standardEvidenceObservations.map((row) => (
                  <a
                    href={`/api/documents/variants/${row.source.variant_id}/file${row.source.page ? `#page=${row.source.page}` : ""}`}
                    target="_blank"
                    rel="noreferrer"
                    key={row.raw_item_id}
                  >
                    <span>{conciseSourceName(row.source.logical_name)}</span>
                    <strong>{formatMoney(row.unit_price)}</strong>
                  </a>
                ))}
                {standardEvidence.isError && <span>근거를 불러오지 못했습니다.</span>}
                {standardEvidence.hasNextPage && (
                  <button
                    className="load-more-button"
                    type="button"
                    disabled={standardEvidence.isFetchingNextPage}
                    onClick={() => void standardEvidence.fetchNextPage()}
                  >
                    {standardEvidence.isFetchingNextPage ? (
                      <LoadingLabel>불러오는 중…</LoadingLabel>
                    ) : (
                      "근거 더 보기"
                    )}
                  </button>
                )}
              </div>
            )}
          </div>
          {marketLookup && (
            <small className="market-auto-status">
              {marketBatchStatusLabel(marketLookup.status)}
              {marketLookup.detail ? <> · {marketLookup.detail}</> : null}
            </small>
          )}
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
                {marketLoading ? (
                  <LoadingLabel>조회 중…</LoadingLabel>
                ) : market ? (
                  "캐시 다시 보기"
                ) : (
                  "시장가 조회"
                )}
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
        <td colSpan={9}>
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
          <button
            type="button"
            disabled={loading}
            aria-busy={loading}
            onClick={onRefresh}
          >
            {loading ? (
              <LoadingLabel>갱신 중…</LoadingLabel>
            ) : (
              <span>실시간 갱신</span>
            )}
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
                  DeviceMart
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
                {!product.automatic_price_eligible && (
                  <div className="market-product-exclusions">
                    <strong>자동 판정에는 미사용</strong>
                    <span>
                      {(product.automatic_price_exclusion_reasons ?? [])
                        .map(marketExclusionLabel)
                        .join(" · ") || "상품 조건을 확인해 주세요."}
                    </span>
                  </div>
                )}
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

function marketLookupProgressLabel(progress: MarketLookupProgress) {
  if (progress.state === "PENDING") {
    return "시장가 자동 조회 중 " + progress.total + "건";
  }
  if (progress.state === "ERROR") {
    return "시장가 자동 조회를 완료하지 못했습니다. 불가 " + progress.unavailable + "건";
  }
  return "시장가 자동 조회 완료 " + progress.completed + "건 · 불가 " + progress.unavailable + "건";
}

function marketBatchStatusLabel(status: MarketLookupProgressItem["status"]) {
  return {
    PENDING: "시장가 자동 조회 중",
    STANDARD_APPLIED: "표준 기준 적용",
    IDENTIFIER_REQUIRED: "정확한 모델명 확인 필요",
    CACHE_HIT: "저장된 시장가 적용",
    LIVE_HIT: "실시간 시장가 적용",
    REFERENCE_ONLY: "참고가만 확인",
    NO_REFERENCE: "시장가 근거 없음",
    SOURCE_UNAVAILABLE: "출처 조회 불가",
    CLEANING_REQUIRED: "정제 확인 필요",
    EXCLUDED: "정제 제외",
    NOT_FOUND: "조회 대상 없음",
  }[status];
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
    REVIEW: "시장가 대비 주의",
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

function conciseSourceName(value: string) {
  const candidate = value.replace(/\\/g, "/").split("/").filter(Boolean).at(-1) ?? value;
  const stem = candidate.replace(/\.[^.]+$/, "");
  const extension = candidate.match(/\.(?:xlsx?|pdf|jpe?g|png|zip|ecml)$/i)?.[0] ?? "";
  const looksGenerated =
    stem.length > 48 &&
    (/^[0-9]{14,}/.test(stem) || /[A-Za-z0-9+/=]{30,}/.test(stem));
  return looksGenerated ? `수집 원본 견적서${extension}` : candidate;
}

function formatNumber(value: string | null) {
  if (value === null) return "—";
  const number = Number(value);
  return Number.isFinite(number)
    ? new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 }).format(number)
    : "—";
}

function formatUnitQuantity(unit: string | null, quantity: string | null) {
  const normalizedUnit = unit?.trim() || null;
  const formattedQuantity = quantity === null ? null : formatNumber(quantity);
  const values = [
    formattedQuantity === "—" ? null : formattedQuantity,
    normalizedUnit,
  ].filter((value): value is string => value !== null);
  return values.length > 0 ? values.join(" ") : "정보 없음";
}

function marketExclusionLabel(reason: string) {
  return {
    MODEL_NUMBER_REQUIRED: "정확한 모델명 없음",
    MODEL_NUMBER_NOT_EXACT: "모델명이 정확히 일치하지 않음",
    MANUFACTURER_REQUIRED: "제품 제조사 확인 필요",
    MANUFACTURER_MISMATCH: "제품 제조사 불일치",
    QUANTITY_REQUIRED: "수량 확인 필요",
    MOQ_NOT_MET: "최소 주문수량 미충족",
    STOCK_UNCONFIRMED: "재고 미확인",
    INSUFFICIENT_STOCK: "요청 수량 재고 부족",
  }[reason] ?? reason;
}

function buyerTargetValue(
  quotedValue: string | null,
  calculatedTarget: string | null,
) {
  if (calculatedTarget === null) return null;
  const target = Number(calculatedTarget);
  const quote = quotedValue === null ? null : Number(quotedValue);
  if (!Number.isFinite(target)) return null;
  if (quote === null || !Number.isFinite(quote)) return calculatedTarget;
  return String(Math.min(quote, target));
}

function negotiationAmount(
  target: QuoteAnalysisRun["target_lines"][number],
) {
  if (target.status !== "AVAILABLE" || target.variance_amount === null) {
    return 0;
  }
  const amount = Number(target.variance_amount);
  return Number.isFinite(amount) ? Math.max(0, amount) : 0;
}

function numberString(value: number | null) {
  return value === null || !Number.isFinite(value) ? null : String(value);
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
  return `(${number > 0 ? "+" : number < 0 ? "−" : ""}${formatted}%)`;
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
