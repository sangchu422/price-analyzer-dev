import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";

import {
  getAnalysisDocuments,
  getDocumentAnalysis,
  type AnalysisAssessment,
  type AnalysisLine,
  type AnalysisMatchStatus,
} from "../api/client";

const matchLabels: Record<AnalysisMatchStatus, string> = {
  EXCLUDED: "정제 제외",
  REVIEW_REQUIRED: "정제 검토 필요",
  CANDIDATE: "후보만 있음",
  NO_MATCH: "매칭 없음",
  MATCHED_NO_PRICE: "표준단가 없음",
  MATCHED: "표준단가 적용",
};

const assessmentLabels: Record<AnalysisAssessment, string> = {
  NOT_APPLICABLE: "판정 없음",
  REVIEW_REQUIRED: "검토 필요",
  LOW: "낮음",
  WITHIN_RANGE: "범위 내",
  REVIEW: "가격 검토",
  HIGH: "높음",
};

export function QuoteAnalysisPage() {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [matchStatus, setMatchStatus] = useState<AnalysisMatchStatus | "">("");
  const [assessment, setAssessment] = useState<AnalysisAssessment | "">("");
  const documents = useInfiniteQuery({
    queryKey: ["analysis-documents"],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      getAnalysisDocuments({ afterId: pageParam, signal }),
    getNextPageParam: safeNextCursor,
  });
  const documentItems = uniqueById(
    documents.data?.pages.flatMap((page) => page.items) ?? [],
    (item) => item.id,
  );
  const effectiveId =
    documentItems.some((item) => item.id === selectedId)
      ? selectedId
      : documentItems[0]?.id ?? null;
  const analysis = useInfiniteQuery({
    queryKey: ["quote-analysis", effectiveId, matchStatus, assessment],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      getDocumentAnalysis({
        documentId: effectiveId!,
        matchStatus: matchStatus || undefined,
        assessment: assessment || undefined,
        afterId: pageParam,
        signal,
      }),
    enabled: effectiveId !== null,
    getNextPageParam: safeNextCursor,
  });
  const analysisLines = uniqueById(
    analysis.data?.pages.flatMap((page) => page.lines) ?? [],
    (line) => line.raw_item_id,
  );
  const selectedDocument = documentItems.find(
    (item) => item.id === effectiveId,
  );
  const documentTotal = documents.data?.pages[0]?.total ?? 0;

  return (
    <main className="workspace-page analysis-page">
      <header className="page-heading">
        <div><p className="section-kicker">Incoming quote review</p><h1>신규 견적 분석</h1></div>
        <p>접수한 견적 품목을 표준 DB와 비교해 가격 범위와 편차를 확인합니다. 매칭되지 않은 품목은 가격 판정에 적용하지 않습니다.</p>
      </header>
      <div className="analysis-controls">
        <label>
          <span>신규 견적서</span>
          <select
            value={effectiveId ?? ""}
            onChange={(event) => setSelectedId(Number(event.target.value))}
            disabled={documents.isPending}
          >
            {documentItems.map((document) => (
              <option key={document.id} value={document.id}>{document.logical_name}</option>
            ))}
          </select>
        </label>
        {documents.hasNextPage && (
          <button
            type="button"
            disabled={documents.isFetchingNextPage}
            onClick={() => void documents.fetchNextPage()}
          >
            {documents.isFetchingNextPage
              ? "다음 견적서 불러오는 중…"
              : "다음 견적서 불러오기"}
          </button>
        )}
        <label>
          <span>매칭 상태</span>
          <select value={matchStatus} onChange={(event) => setMatchStatus(event.target.value as AnalysisMatchStatus | "")}>
            <option value="">전체</option>
            {Object.entries(matchLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label>
          <span>가격 판정</span>
          <select value={assessment} onChange={(event) => setAssessment(event.target.value as AnalysisAssessment | "")}>
            <option value="">전체</option>
            {Object.entries(assessmentLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <div className="filter-status" aria-live="polite">
          {analysis.isPending
            ? "서버 필터 적용 중…"
            : `표시 ${analysisLines.length}개 / 원천 전체 ${selectedDocument?.raw_item_count ?? 0}개`}
        </div>
      </div>

      {documents.isError && <div className="workspace-notice is-error" role="alert">견적서 목록을 불러오지 못했습니다.</div>}
      {selectedDocument && (
        <section className="analysis-surface">
          <header className="analysis-heading">
            <div>
              <p className="section-kicker">Document #{selectedDocument.id}</p>
              <h1>{selectedDocument.logical_name}</h1>
              <p>원천 {selectedDocument.raw_item_count}행 · 정제 포함 {selectedDocument.included_count}행</p>
            </div>
            <dl>
              <div><dt>표시 행</dt><dd>{analysisLines.length}</dd></div>
              <div><dt>원천 전체</dt><dd>{selectedDocument.raw_item_count}</dd></div>
              <div><dt>견적서</dt><dd>{documentItems.length}/{documentTotal}</dd></div>
            </dl>
          </header>

          {analysis.isError && (
            <div className="workspace-notice is-error" role="alert">
              현재 조건의 비교 결과를 불러오지 못했습니다.
              <button type="button" onClick={() => void analysis.refetch()}>다시 시도</button>
            </div>
          )}
          {analysis.isPending && (
            <div className="analysis-loading" role="status">
              새 조건의 결과를 불러오는 중…
            </div>
          )}
          {!analysis.isPending && (
          <div className={`table-scroll analysis-table-wrap ${analysis.isFetchingNextPage ? "is-loading" : ""}`}>
            <table className="data-table analysis-table">
              <thead>
                <tr>
                  <th>견적 품목</th>
                  <th>매칭</th>
                  <th>표준품목</th>
                  <th>견적 단가</th>
                  <th>참조 중앙값</th>
                  <th>참조 범위</th>
                  <th>편차</th>
                  <th>판정</th>
                  <th>근거</th>
                </tr>
              </thead>
              <tbody>
                {analysisLines.map((line) => <AnalysisRow key={line.raw_item_id} line={line} />)}
              </tbody>
            </table>
            {analysisLines.length === 0 && (
              <p className="inline-state">현재 서버 필터에 맞는 행이 없습니다.</p>
            )}
            {analysis.hasNextPage && (
              <button
                className="load-more-button"
                type="button"
                disabled={analysis.isFetchingNextPage}
                onClick={() => void analysis.fetchNextPage()}
              >
                {analysis.isFetchingNextPage
                  ? "다음 분석 행 불러오는 중…"
                  : "다음 분석 행 불러오기"}
              </button>
            )}
          </div>
          )}
        </section>
      )}
    </main>
  );
}

function AnalysisRow({ line }: { line: AnalysisLine }) {
  const canApplyPrice = line.match_status === "MATCHED";
  const candidate = line.candidates[0];
  const canonical =
    line.canonical_name ??
    candidate?.canonical_name ??
    (line.standard_item_id ? `표준품목 #${line.standard_item_id}` : "—");
  return (
    <tr>
      <td>
        <strong>{line.item_name ?? "품명 없음"}</strong>
        <span>{line.spec ?? "사양 없음"} · {line.unit ?? "단위 없음"}</span>
      </td>
      <td><span className={`analysis-status is-${line.match_status.toLowerCase()}`}>{matchLabels[line.match_status]}</span></td>
      <td>
        <strong>{canonical}</strong>
        {candidate && line.match_status === "CANDIDATE" && (
          <span>후보 점수 {Math.round(Number(candidate.final_score) * 100)}%</span>
        )}
      </td>
      <td className="numeric">{line.quote_unit_price ?? "—"}</td>
      <td className="numeric">{canApplyPrice ? line.reference_price ?? "—" : "적용 안 함"}</td>
      <td className="numeric">
        {canApplyPrice && line.minimum_price && line.maximum_price
          ? `${line.minimum_price}–${line.maximum_price}`
          : "—"}
      </td>
      <td className="numeric">
        {canApplyPrice && line.variance_percent ? `${line.variance_percent}%` : "—"}
      </td>
      <td><span className={`assessment is-${line.assessment.toLowerCase()}`}>{assessmentLabels[line.assessment]}</span></td>
      <td>
        <details className="evidence-popover">
          <summary>보기</summary>
          <div>
            <strong>{line.source.path}</strong>
            <span>{line.source.sheet ?? "시트 없음"} · {line.source.row ? `${line.source.row}행` : line.source.page ? `${line.source.page}쪽` : "위치 없음"}</span>
            <a
              href={`/grouping?raw_item_id=${line.raw_item_id}`}
              aria-label="원천행 감사 보기"
            >
              원천행 감사 보기
            </a>
            {line.standard_item_id && line.standard_price_version_id && (
              <a
                href={`/standard-prices?item_id=${line.standard_item_id}&version_id=${line.standard_price_version_id}`}
                aria-label="표준단가 버전 감사 보기"
              >
                표준단가 vID {line.standard_price_version_id}
              </a>
            )}
            {candidate?.matched_tokens.length ? <span>모델 토큰 {candidate.matched_tokens.join(", ")}</span> : null}
          </div>
        </details>
      </td>
    </tr>
  );
}

function safeNextCursor<T extends { next_cursor: number | null }>(
  lastPage: T,
  allPages: T[],
  lastPageParam: number | undefined,
) {
  const next = lastPage.next_cursor;
  if (
    next === null ||
    next === lastPageParam ||
    allPages.slice(0, -1).some((page) => page.next_cursor === next)
  ) {
    return undefined;
  }
  return next;
}

function uniqueById<T>(items: T[], getId: (item: T) => number) {
  const seen = new Set<number>();
  return items.filter((item) => {
    const id = getId(item);
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}
