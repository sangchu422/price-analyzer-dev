import { useMemo, useState } from "react";

import {
  ApiError,
  getCompleteDocumentAnalysis,
  submitIncomingBid,
  type AnalysisAssessment,
  type AnalysisLine,
  type DocumentAnalysis,
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
  REVIEW_REQUIRED: "판정대기",
  LOW: "저가",
  WITHIN_RANGE: "적정",
  REVIEW: "가격 검토",
  HIGH: "고가",
};

export function QuoteAnalysisPage() {
  const [file, setFile] = useState<File | null>(null);
  const [submittedBy, setSubmittedBy] = useState("");
  const [stage, setStage] = useState<WorkflowStage>("IDLE");
  const [submission, setSubmission] = useState<SubmissionResponse | null>(null);
  const [analysis, setAnalysis] = useState<DocumentAnalysis | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [resultFilter, setResultFilter] = useState<ResultFilter>("ALL");
  const [validationError, setValidationError] = useState("");
  const busy = stage !== "IDLE";

  const startAnalysis = async () => {
    if (!file || !submittedBy.trim()) {
      setValidationError("견적서 파일과 접수자를 모두 입력해 주세요.");
      return;
    }
    setValidationError("");
    setError(null);
    setAnalysis(null);
    setSubmission(null);
    setStage("PARSING");
    try {
      const accepted = await submitIncomingBid(file, submittedBy.trim());
      setSubmission(accepted);
      setStage("ANALYZING");
      const result = await getCompleteDocumentAnalysis(accepted.document_id);
      setAnalysis(result);
      setStage("IDLE");
    } catch (caught) {
      setError(caught);
      setStage("IDLE");
    }
  };

  const metrics = useMemo(
    () => summarize(analysis?.lines ?? [], submission?.raw_item_count ?? 0),
    [analysis, submission],
  );
  const visibleLines = useMemo(
    () => (analysis?.lines ?? []).filter((line) => lineMatches(line, resultFilter)),
    [analysis, resultFilter],
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
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
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
        <button
          className="primary-action"
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
            <button type="button" onClick={() => void startAnalysis()}>
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
}: {
  analysis: DocumentAnalysis;
  submission: SubmissionResponse;
  metrics: ReturnType<typeof summarize>;
  lines: AnalysisLine[];
  filter: ResultFilter;
  onFilter: (filter: ResultFilter) => void;
}) {
  return (
    <section className="analysis-results">
      <header className="result-heading">
        <div>
          <p className="section-kicker">Document #{analysis.document.id}</p>
          <h2>{analysis.document.logical_name}</h2>
          <p>
            <span>{`${submission.parser_name} ${submission.parser_version}`}</span>
            {" · "}
            <strong>{`총 ${submission.raw_item_count}개 품목`}</strong>
          </p>
        </div>
        <div className={`overall-assessment is-${metrics.overallTone}`}>
          <span>종합 판정</span>
          <strong>{metrics.overall}</strong>
          <small>평가 금액 커버리지 {formatPercent(metrics.coverage)}</small>
        </div>
      </header>

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
          <span>판정대기</span>
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
        <div>
          <span>가격 판정</span>
          <strong>저가 {metrics.low}건</strong>
        </div>
      </div>

      <div className="market-roadmap">
        <strong>{`시장가 확인 필요 ${metrics.market}건`}</strong>
        <span>DeviceMart·Mouser DB/실시간 조회 연동 예정</span>
        <p>현재는 조회한 가격처럼 표시하지 않으며, 설비구매팀의 판정대기 검토 대상으로 유지합니다.</p>
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
            <option value="PENDING">판정대기</option>
            <option value="HIGH">고가</option>
            <option value="WITHIN_RANGE">적정</option>
            <option value="REVIEW">가격 검토</option>
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
              <th>참조 최소·평균·최대</th>
              <th>편차</th>
              <th>근거</th>
              <th>판정</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => <AnalysisRow key={line.raw_item_id} line={line} />)}
          </tbody>
        </table>
        {lines.length === 0 && <p className="inline-state">선택한 조건에 맞는 품목이 없습니다.</p>}
      </div>
    </section>
  );
}

function AnalysisRow({ line }: { line: AnalysisLine }) {
  const market = line.market_price_lookup_required;
  const pending = line.assessment === "REVIEW_REQUIRED";
  return (
    <tr>
      <td>
        <strong>{line.item_name ?? "품명 없음"}</strong>
        <span>{line.spec ?? "사양 없음"}</span>
      </td>
      <td className="numeric">{line.unit ?? "—"} · {formatNumber(line.quantity)}</td>
      <td className="numeric">{formatMoney(line.quote_unit_price)}</td>
      <td className="numeric">{formatMoney(line.quote_amount)}</td>
      <td className="reference-range">
        {line.match_status === "MATCHED" ? (
          <>
            <strong>{formatMoney(line.minimum_price)}</strong>
            <span>{formatMoney(line.average_price)}</span>
            <strong>{formatMoney(line.maximum_price)}</strong>
          </>
        ) : "—"}
      </td>
      <td className="numeric">{line.variance_percent ? `${formatNumber(line.variance_percent)}%` : "—"}</td>
      <td>
        <div className="line-evidence">
          {line.standard_item_id ? (
            <a href={`/standard-prices?item_id=${line.standard_item_id}`}>
              표준 DB #{line.standard_item_id}
            </a>
          ) : (
            <span className="muted">표준 근거 없음</span>
          )}
          <small>
            {line.source.sheet ?? "파일"} · {line.source.row ? `${line.source.row}행` : line.source.page ? `${line.source.page}쪽` : "위치 없음"}
          </small>
        </div>
      </td>
      <td>
        <div className="row-status">
          <span className={`assessment is-${line.assessment.toLowerCase()}`}>
            {market ? "시장가 확인 필요" : assessmentLabels[line.assessment]}
          </span>
          {pending && <small>판정대기</small>}
        </div>
      </td>
    </tr>
  );
}

function summarize(lines: AnalysisLine[], rawCount: number) {
  const count = (assessment: AnalysisAssessment) =>
    lines.filter((line) => line.assessment === assessment).length;
  const matched = lines.filter((line) => line.match_status === "MATCHED").length;
  const market = lines.filter((line) => line.market_price_lookup_required).length;
  const pending = lines.filter((line) => line.assessment === "REVIEW_REQUIRED").length;
  const assessedAmount = lines.reduce(
    (sum, line) => sum + (line.match_status === "MATCHED" ? numericAmount(line) : 0),
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
    overall: high > 0 ? "고가 품목 검토 필요" : review > 0 || pending > 0 ? "판정대기 포함" : "적정 범위",
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

function lineMatches(line: AnalysisLine, filter: ResultFilter) {
  if (filter === "ALL") return true;
  if (filter === "MATCHED") return line.match_status === "MATCHED";
  if (filter === "MARKET") return line.market_price_lookup_required;
  if (filter === "PENDING") return line.assessment === "REVIEW_REQUIRED";
  return line.assessment === filter;
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
