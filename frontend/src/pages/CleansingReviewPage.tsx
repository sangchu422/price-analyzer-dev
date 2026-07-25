import { useMemo, useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";

import {
  ApiError,
  getReviewQueue,
  submitManualDecision,
  type ManualDecisionStatus,
} from "../api/client";
import { DecisionBar } from "../components/DecisionBar";
import { ItemInspector } from "../components/ItemInspector";
import { ReviewQueue } from "../components/ReviewQueue";

type Notice = { kind: "success" | "stale" | "error"; text: string };

export function CleansingReviewPage() {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [resolvedIds, setResolvedIds] = useState<Set<number>>(() => new Set());
  const [search, setSearch] = useState("");
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState<Notice | null>(null);

  const queue = useInfiniteQuery({
    queryKey: ["cleansing-review"],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) => getReviewQueue(pageParam, signal),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  const items = useMemo(
    () =>
      (queue.data?.pages.flatMap((page) => page.items) ?? []).filter(
        (item) => !resolvedIds.has(item.raw_item_id),
      ),
    [queue.data, resolvedIds],
  );

  const effectiveSelectedId = items.some((item) => item.raw_item_id === selectedId)
    ? selectedId
    : items[0]?.raw_item_id ?? null;
  const selected = items.find((item) => item.raw_item_id === effectiveSelectedId) ?? null;
  const mutation = useMutation({
    mutationFn: ({
      status,
      actor,
      detail,
    }: {
      status: ManualDecisionStatus;
      actor: string;
      detail: string;
    }) => {
      if (!selected) throw new Error("선택된 항목이 없습니다.");
      return submitManualDecision(selected.raw_item_id, {
        status,
        reason_code: "MANUAL_REVIEW",
        reason_detail: detail,
        decided_by: actor,
        expected_current_decision_id: selected.decision.id,
      });
    },
    onSuccess: () => {
      if (!selected) return;
      setResolvedIds((current) => new Set(current).add(selected.raw_item_id));
      setNotice({ kind: "success", text: "판단이 저장되었습니다." });
      void queryClient.invalidateQueries({ queryKey: ["cleansing-review"] });
    },
    onError: async (error) => {
      if (error instanceof ApiError && error.status === 409 && error.errorCode === "STALE_DECISION") {
        setNotice({
          kind: "stale",
          text: "다른 검토자가 먼저 변경했습니다. 최신 내용으로 새로고침했습니다.",
        });
        await queue.refetch();
        return;
      }
      setNotice({
        kind: "error",
        text: "판단을 저장하지 못했습니다. 내용을 확인하고 다시 시도해 주세요.",
      });
    },
  });

  if (queue.isPending) {
    return <StateScreen message="검토 항목을 불러오는 중입니다." busy />;
  }
  if (queue.isError) {
    return (
      <StateScreen
        message="검토 목록을 불러오지 못했습니다."
        action={<button onClick={() => void queue.refetch()}>다시 시도</button>}
      />
    );
  }
  if (items.length === 0) {
    return (
      <StateScreen
        message={notice?.kind === "success" ? notice.text : "검토할 항목이 없습니다."}
      />
    );
  }

  const serverRemaining = queue.data.pages[0]?.remaining ?? items.length;

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
        <div className="queue-status" aria-label={`검토 대기 ${serverRemaining}건`}>
          <span className="status-dot" aria-hidden="true" />
          검토 대기 <strong>{serverRemaining}</strong>건
        </div>
      </header>

      <div className="workspace">
        <ReviewQueue
          items={items}
          selectedId={effectiveSelectedId}
          search={search}
          reason={reason}
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
          {selected && <ItemInspector item={selected} />}
          {selected && (
            <DecisionBar
              key={selected.raw_item_id}
              item={selected}
              isSaving={mutation.isPending}
              notice={notice}
              onSubmit={(status, actor, detail) => {
                setNotice(null);
                mutation.mutate({ status, actor, detail });
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
  action,
}: {
  message: string;
  busy?: boolean;
  action?: React.ReactNode;
}) {
  return (
    <main className="state-screen" aria-live="polite" aria-busy={busy}>
      <span className="brand-mark" aria-hidden="true">P</span>
      <p>{message}</p>
      {action}
    </main>
  );
}
