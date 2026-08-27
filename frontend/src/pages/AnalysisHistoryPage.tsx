import { useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArchiveRestore, CheckCircle2, Database, FileClock, X } from "lucide-react";

import {
  getAnalysisHistory,
  setQuoteCatalogState,
  type AnalysisHistoryItem,
  type QuoteCatalogState,
} from "../api/client";
import { safeNextCursor } from "../api/pagination";
import { LoadingLabel } from "../components/LoadingLabel";

type HistoryFilter = "ALL" | QuoteCatalogState;
type PendingChange = {
  item: AnalysisHistoryItem;
  nextState: "INCLUDED" | "EXCLUDED";
};

const stateCopy: Record<QuoteCatalogState, { label: string; note: string }> = {
  NOT_INCLUDED: {
    label: "표준 DB 미반영",
    note: "분석 결과만 보관 중이며 가격 기준에는 사용하지 않습니다.",
  },
  INCLUDED: {
    label: "표준 DB 반영",
    note: "현재 표준 DB 가격 근거에 포함되어 있습니다.",
  },
  EXCLUDED: {
    label: "표준 DB 제외",
    note: "이력은 보존하고 운영 가격 근거에서 제외했습니다.",
  },
};

export function AnalysisHistoryPage() {
  const [filter, setFilter] = useState<HistoryFilter>("ALL");
  const [pendingChange, setPendingChange] = useState<PendingChange | null>(null);
  const [decidedBy, setDecidedBy] = useState("");
  const [reasonDetail, setReasonDetail] = useState("");
  const dialogRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const queryClient = useQueryClient();
  const history = useInfiniteQuery({
    queryKey: ["analysis-history", filter],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) => getAnalysisHistory({
      state: filter === "ALL" ? undefined : filter,
      afterId: pageParam,
      signal,
    }),
    getNextPageParam: safeNextCursor,
    retry: false,
  });
  const mutation = useMutation({
    mutationFn: (change: PendingChange) => setQuoteCatalogState({
      runId: change.item.run_id,
      state: change.nextState,
      decidedBy: decidedBy.trim(),
      reasonDetail: reasonDetail.trim(),
      expectedCurrentDecisionId: change.item.current_decision_id,
    }),
    onSuccess: async () => {
      setPendingChange(null);
      setDecidedBy("");
      setReasonDetail("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["analysis-history"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard-overview"] }),
        queryClient.invalidateQueries({ queryKey: ["standard-db"] }),
        queryClient.invalidateQueries({ queryKey: ["item-families"] }),
      ]);
      window.setTimeout(() => returnFocusRef.current?.focus(), 0);
    },
  });

  useEffect(() => {
    document.title = "분석 이력 관리 · 통합 견적 분석 시스템";
    return () => {
      document.title = "통합 견적 분석 시스템";
    };
  }, []);

  useEffect(() => {
    if (!pendingChange) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !mutation.isPending) {
        setPendingChange(null);
        window.setTimeout(() => returnFocusRef.current?.focus(), 0);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [pendingChange, mutation.isPending]);

  const items = useMemo(() => {
    const seen = new Set<number>();
    return (history.data?.pages.flatMap((page) => page.items) ?? []).filter((item) => {
      if (seen.has(item.run_id)) return false;
      seen.add(item.run_id);
      return true;
    });
  }, [history.data]);
  const total = history.data?.pages[0]?.total ?? 0;

  function openDialog(item: AnalysisHistoryItem, nextState: "INCLUDED" | "EXCLUDED", trigger: HTMLElement) {
    returnFocusRef.current = trigger;
    setPendingChange({ item, nextState });
    setReasonDetail(nextState === "INCLUDED" ? "담당자 검토 후 표준 DB 가격 근거로 반영" : "운영 표준 DB 가격 근거에서 제외");
    mutation.reset();
  }

  function closeDialog() {
    if (mutation.isPending) return;
    setPendingChange(null);
    mutation.reset();
    window.setTimeout(() => returnFocusRef.current?.focus(), 0);
  }

  return (
    <main className="workspace-page analysis-history-page">
      <header className="page-heading analysis-history-heading">
        <div>
          <p className="section-kicker">QUOTE GOVERNANCE</p>
          <h1>분석 이력 관리</h1>
        </div>
        <p>신규 견적 분석 결과는 자동으로 표준 DB에 들어가지 않습니다. 담당자가 확인한 견적만 반영하고, 필요하면 원본을 지우지 않은 채 다시 제외할 수 있습니다.</p>
      </header>

      <section className="analysis-history-command" aria-label="분석 이력 필터">
        <div>
          <FileClock aria-hidden="true" />
          <span>보관된 분석 이력</span>
          <strong>{total.toLocaleString("ko-KR")}건</strong>
        </div>
        <div className="history-filter-tabs" role="group" aria-label="표준 DB 반영 상태">
          {(["ALL", "NOT_INCLUDED", "INCLUDED", "EXCLUDED"] as const).map((value) => (
            <button
              type="button"
              key={value}
              aria-pressed={filter === value}
              onClick={() => setFilter(value)}
            >
              {value === "ALL" ? "전체" : stateCopy[value].label}
            </button>
          ))}
        </div>
      </section>

      <section className="analysis-history-card" aria-labelledby="history-table-title">
        <header>
          <div><span>ANALYSIS ARCHIVE</span><h2 id="history-table-title">견적서 분석 기록</h2></div>
          <small>견적서는 건, 견적서 안의 품목은 개로 표시합니다.</small>
        </header>
        {history.isLoading ? (
          <div className="history-inline-state" role="status"><LoadingLabel>분석 이력을 불러오는 중</LoadingLabel></div>
        ) : history.isError ? (
          <div className="history-inline-state is-error" role="alert">분석 이력을 불러오지 못했습니다.<button type="button" onClick={() => void history.refetch()}>다시 시도</button></div>
        ) : (
          <div className="analysis-history-table-scroll">
            <table className="analysis-history-table">
              <thead><tr><th>견적서</th><th>분석 일시</th><th>분석 담당자</th><th>품목</th><th>구매 금액</th><th>목표 산정</th><th>표준 DB 상태</th><th>관리</th></tr></thead>
              <tbody>
                {items.map((item) => {
                  const copy = stateCopy[item.catalog_state];
                  return (
                    <tr key={item.run_id}>
                      <td title={item.file_name}><strong>{displayDocumentName(item.file_name)}</strong><small>분석 #{item.run_id} · 문서 #{item.document_id}</small></td>
                      <td><time dateTime={item.analyzed_at}>{formatDateTime(item.analyzed_at)}</time></td>
                      <td>{item.created_by || "익명"}</td>
                      <td><strong>{item.total_line_count.toLocaleString("ko-KR")}개</strong><small>목표가 {item.target_available_count.toLocaleString("ko-KR")}개</small></td>
                      <td className="numeric">{formatMoney(item.quote_total_amount)}</td>
                      <td className="numeric">{formatMoney(item.target_total_amount)}</td>
                      <td><span className={`catalog-state-badge is-${item.catalog_state.toLowerCase()}`}>{copy.label}</span><small>{copy.note}</small></td>
                      <td>
                        {item.catalog_state === "INCLUDED" ? (
                          <button type="button" className="history-action is-exclude" onClick={(event) => openDialog(item, "EXCLUDED", event.currentTarget)}><ArchiveRestore size={14} /> 반영 제외</button>
                        ) : (
                          <button type="button" className="history-action is-include" onClick={(event) => openDialog(item, "INCLUDED", event.currentTarget)}><Database size={14} /> 표준 DB 반영</button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {items.length === 0 ? <p className="history-empty-state">선택한 상태의 분석 이력이 없습니다.</p> : null}
          </div>
        )}
        {history.hasNextPage ? <button className="history-load-more" type="button" disabled={history.isFetchingNextPage} onClick={() => void history.fetchNextPage()}>{history.isFetchingNextPage ? "불러오는 중…" : "이력 더 보기"}</button> : null}
      </section>

      {pendingChange ? (
        <div className="catalog-state-overlay is-open" onMouseDown={(event) => { if (event.target === event.currentTarget) closeDialog(); }}>
          <div ref={dialogRef} className="catalog-state-dialog t-modal is-open" role="dialog" aria-modal="true" aria-labelledby="catalog-state-dialog-title" tabIndex={-1}>
            <header>
              <div><span>{pendingChange.nextState === "INCLUDED" ? "STANDARD DB / INCLUDE" : "STANDARD DB / EXCLUDE"}</span><h2 id="catalog-state-dialog-title">{pendingChange.nextState === "INCLUDED" ? "표준 DB에 반영할까요?" : "표준 DB 반영에서 제외할까요?"}</h2></div>
              <button type="button" aria-label="창 닫기" onClick={closeDialog}><X size={18} /></button>
            </header>
            <div className="catalog-state-document">
              {pendingChange.nextState === "INCLUDED" ? <CheckCircle2 aria-hidden="true" /> : <ArchiveRestore aria-hidden="true" />}
              <div><strong>{displayDocumentName(pendingChange.item.file_name)}</strong><small>원본과 분석 이력은 삭제되지 않습니다.</small></div>
            </div>
            <label><span>담당자</span><input autoFocus value={decidedBy} onChange={(event) => setDecidedBy(event.target.value)} placeholder="이름 또는 사번" /></label>
            <label><span>변경 사유</span><textarea value={reasonDetail} onChange={(event) => setReasonDetail(event.target.value)} rows={3} /></label>
            {mutation.isError ? <p className="catalog-state-error" role="alert">{mutation.error instanceof Error ? mutation.error.message : "상태를 변경하지 못했습니다."}</p> : null}
            <footer>
              <button type="button" onClick={closeDialog} disabled={mutation.isPending}>취소</button>
              <button type="button" className="is-primary" disabled={mutation.isPending || decidedBy.trim().length < 1 || reasonDetail.trim().length < 3} onClick={() => mutation.mutate(pendingChange)}>{mutation.isPending ? "표준 DB 재구축 중…" : pendingChange.nextState === "INCLUDED" ? "확인 후 반영" : "확인 후 제외"}</button>
            </footer>
          </div>
        </div>
      ) : null}
    </main>
  );
}

function formatMoney(value: string | null) {
  if (value === null || value === "") return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `${Math.round(parsed).toLocaleString("ko-KR")}원` : "—";
}

function formatDateTime(value: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function displayDocumentName(value: string) {
  return value.replace(/\\/g, "/").split("/").filter(Boolean).at(-1) ?? value;
}
