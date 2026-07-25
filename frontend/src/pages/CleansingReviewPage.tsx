import { useEffect, useMemo, useRef, useState } from "react";
import {
  keepPreviousData,
  type InfiniteData,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";

import {
  ApiError,
  getReviewQueue,
  submitManualDecision,
  type ManualDecisionStatus,
  type ReviewQueueResponse,
} from "../api/client";
import { DecisionBar } from "../components/DecisionBar";
import { ItemInspector } from "../components/ItemInspector";
import { ReviewQueue } from "../components/ReviewQueue";

type Notice = { kind: "success" | "stale" | "error"; text: string };

interface DecisionMutationVariables {
  rawItemId: number;
  expectedDecisionId: number;
  status: ManualDecisionStatus;
  actor: string;
  detail: string;
}

export function CleansingReviewPage() {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [resolvedIds, setResolvedIds] = useState<Set<number>>(() => new Set());
  const [search, setSearch] = useState("");
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState<Notice | null>(null);
  const inspectorHeadingRef = useRef<HTMLHeadingElement>(null);
  const focusAfterDecisionRef = useRef(false);
  const debouncedSearch = useDebouncedValue(search.trim(), 300);

  const queue = useInfiniteQuery({
    queryKey: ["cleansing-review", debouncedSearch, reason],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      getReviewQueue({
        afterId: pageParam,
        search: debouncedSearch || undefined,
        reasonCode: reason || undefined,
        signal,
      }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
  });
  const unfilteredCache = queryClient.getQueryData<
    InfiniteData<ReviewQueueResponse>
  >(["cleansing-review", "", ""]);
  const displayData = queue.data ?? unfilteredCache;

  const items = useMemo(
    () =>
      (displayData?.pages.flatMap((page) => page.items) ?? []).filter(
        (item) => !resolvedIds.has(item.raw_item_id),
      ),
    [displayData, resolvedIds],
  );
  const availableReasons = useMemo(
    () => [
      ...new Set([
        ...(reason ? [reason] : []),
        ...(displayData?.pages[0]?.available_reason_codes ?? []),
      ]),
    ],
    [displayData, reason],
  );

  const effectiveSelectedId = items.some(
    (item) => item.raw_item_id === selectedId,
  )
    ? selectedId
    : items[0]?.raw_item_id ?? null;
  const selected =
    items.find((item) => item.raw_item_id === effectiveSelectedId) ??
    null;
  const isFilterTransition =
    search.trim() !== debouncedSearch ||
    queue.isPlaceholderData ||
    (queue.isFetching && !queue.isFetchingNextPage) ||
    queue.isError;
  useEffect(() => {
    if (!focusAfterDecisionRef.current) return;
    focusAfterDecisionRef.current = false;
    inspectorHeadingRef.current?.focus();
  }, [selected?.raw_item_id]);

  const mutation = useMutation({
    mutationFn: (variables: DecisionMutationVariables) =>
      submitManualDecision(variables.rawItemId, {
        status: variables.status,
        reason_code: "MANUAL_REVIEW",
        reason_detail: variables.detail,
        decided_by: variables.actor,
        expected_current_decision_id: variables.expectedDecisionId,
      }),
    onSuccess: (_data, variables) => {
      focusAfterDecisionRef.current = true;
      setResolvedIds((current) => new Set(current).add(variables.rawItemId));
      setNotice({ kind: "success", text: "판단이 저장되었습니다." });
      void queryClient.invalidateQueries({ queryKey: ["cleansing-review"] });
    },
    onError: async (error) => {
      if (
        error instanceof ApiError &&
        error.status === 409 &&
        error.errorCode === "STALE_DECISION"
      ) {
        setNotice({
          kind: "stale",
          text: "다른 검토자가 먼저 변경했습니다. 최신 내용으로 새로고침했습니다.",
        });
        await queue.refetch();
        return;
      }
      setNotice({
        kind: "error",
        text:
          error instanceof ApiError
            ? `판단을 저장하지 못했습니다: ${error.message.slice(0, 300)}`
            : "판단을 저장하지 못했습니다. 내용을 확인하고 다시 시도해 주세요.",
      });
    },
  });

  if (queue.isPending) {
    return <StateScreen message="검토 항목을 불러오는 중입니다." busy />;
  }
  if (queue.isError && !displayData) {
    return (
      <StateScreen
        message="검토 목록을 불러오지 못했습니다."
        alert
        action={<button onClick={() => void queue.refetch()}>다시 시도</button>}
      />
    );
  }
  const hasActiveFilter = Boolean(debouncedSearch || reason);
  if (items.length === 0 && !hasActiveFilter) {
    return (
      <StateScreen
        message={
          notice?.kind === "success"
            ? notice.text
            : "검토할 항목이 없습니다."
        }
      />
    );
  }

  const serverRemaining = displayData?.pages[0]?.remaining ?? items.length;
  const queryErrorMessage =
    queue.isError && queue.error instanceof ApiError
      ? queue.error.message.slice(0, 300)
      : "검토 목록을 불러오지 못했습니다.";

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">P</span>
          <div>
            <strong>Price Analyzer</strong>
            <span>견적 정제 검토</span>
          </div>
        </div>
        <div
          className="queue-status"
          aria-label={`검토 대기 ${serverRemaining}건`}
        >
          <span className="status-dot" aria-hidden="true" />
          검토 대기 <strong>{serverRemaining}</strong>건
        </div>
      </header>
      {queue.isError && (
        <section className="query-error-banner" role="alert">
          <div>
            <strong>현재 검색 조건을 적용하지 못했습니다.</strong>
            <span>{queryErrorMessage}</span>
          </div>
          <div className="query-error-actions">
            <button type="button" onClick={() => void queue.refetch()}>
              현재 조건 다시 시도
            </button>
            <button
              type="button"
              onClick={() => {
                setSearch("");
                setReason("");
                setSelectedId(null);
                setNotice(null);
              }}
            >
              필터 초기화
            </button>
          </div>
        </section>
      )}

      <div className="workspace">
        <ReviewQueue
          items={items}
          availableReasons={availableReasons}
          selectedId={effectiveSelectedId}
          search={search}
          reason={reason}
          controlsLocked={mutation.isPending}
          resultsLocked={mutation.isPending || isFilterTransition}
          isSearching={isFilterTransition && !queue.isError}
          hasNextPage={queue.hasNextPage}
          isFetchingNextPage={queue.isFetchingNextPage}
          onSearchChange={setSearch}
          onReasonChange={setReason}
          onSelect={(id) => {
            setSelectedId(id);
            setNotice(null);
          }}
          onLoadMore={() => void queue.fetchNextPage()}
        />
        <div className="detail-pane">
          {!selected && (
            <div className="filtered-empty">
              <p>검색 조건에 맞는 검토 항목이 없습니다.</p>
              <span>검색어나 검토 사유를 변경해 주세요.</span>
              {notice && (
                <strong
                  className={`notice-${notice.kind}`}
                  role={notice.kind === "success" ? "status" : "alert"}
                >
                  {notice.text}
                </strong>
              )}
            </div>
          )}
          {selected && (
            <ItemInspector item={selected} headingRef={inspectorHeadingRef} />
          )}
          {selected && (
            <DecisionBar
              key={selected.raw_item_id}
              item={selected}
              isSaving={mutation.isPending}
              isDisabled={isFilterTransition}
              notice={notice}
              onSubmit={(status, actor, detail) => {
                setNotice(null);
                mutation.mutate({
                  rawItemId: selected.raw_item_id,
                  expectedDecisionId: selected.decision.id,
                  status,
                  actor,
                  detail,
                });
              }}
            />
          )}
        </div>
      </div>
    </main>
  );
}

function StateScreen({
  message,
  busy = false,
  alert = false,
  action,
}: {
  message: string;
  busy?: boolean;
  alert?: boolean;
  action?: React.ReactNode;
}) {
  return (
    <main
      className="state-screen"
      role={alert ? "alert" : "status"}
      aria-live={alert ? "assertive" : "polite"}
      aria-busy={busy}
    >
      <span className="brand-mark" aria-hidden="true">P</span>
      <p>{message}</p>
      {action}
    </main>
  );
}

function useDebouncedValue<T>(value: T, delay: number) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timeout = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timeout);
  }, [delay, value]);
  return debounced;
}
