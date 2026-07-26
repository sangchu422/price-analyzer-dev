import { useEffect, useRef, useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  ApiError,
  approvePrice,
  getPriceDraft,
  getStandardPriceVersions,
  getStandardItems,
  type PriceVersion,
} from "../api/client";

export function StandardPricesPage() {
  const queryClient = useQueryClient();
  const linkedItemId = positiveIntegerParam("item_id");
  const linkedVersionId = positiveIntegerParam("version_id");
  const [requestedItemId, setRequestedItemId] = useState<number | null>(
    linkedItemId,
  );
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [actor, setActor] = useState("");
  const versionLinkRefs = useRef(new Map<number, HTMLAnchorElement>());
  const focusedLinkedVersionId = useRef<number | null>(null);
  const attemptedCatalogCursors = useRef(new Set<number>());
  const attemptedHistoryCursors = useRef(new Set<string>());
  const [notice, setNotice] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const catalog = useInfiniteQuery({
    queryKey: ["standard-items"],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      getStandardItems({ afterId: pageParam, signal }),
    getNextPageParam: safeNextCursor,
    retry: false,
  });
  const catalogItems = uniqueById(
    catalog.data?.pages.flatMap((page) => page.items) ?? [],
  );
  const {
    fetchNextPage,
    hasNextPage,
    isFetchNextPageError,
    isFetchingNextPage,
  } = catalog;
  const linkedItem = catalogItems.find((item) => item.id === requestedItemId);
  const effectiveId =
    linkedItem?.id ??
    (requestedItemId !== null
      ? null
      : catalogItems.some((item) => item.id === selectedId)
      ? selectedId
      : catalogItems[0]?.id ?? null);
  const selected =
    catalogItems.find((item) => item.id === effectiveId) ?? null;
  const activeLinkedVersionId =
    requestedItemId !== null && linkedItem ? linkedVersionId : null;
  const catalogCursor =
    catalog.data?.pages.at(-1)?.next_cursor ?? null;
  const linkedItemMissing =
    requestedItemId !== null &&
    !linkedItem &&
    catalog.data !== undefined &&
    !hasNextPage &&
    !isFetchingNextPage &&
    !isFetchNextPageError;

  useEffect(() => {
    if (
      requestedItemId === null ||
      linkedItem ||
      !hasNextPage ||
      isFetchingNextPage ||
      isFetchNextPageError ||
      catalogCursor === null ||
      attemptedCatalogCursors.current.has(catalogCursor)
    ) {
      return;
    }
    attemptedCatalogCursors.current.add(catalogCursor);
    void fetchNextPage();
  }, [
    requestedItemId,
    linkedItem,
    hasNextPage,
    isFetchingNextPage,
    isFetchNextPageError,
    catalogCursor,
    fetchNextPage,
  ]);
  const draft = useQuery({
    queryKey: ["price-draft", effectiveId],
    queryFn: ({ signal }) => getPriceDraft(effectiveId!, signal),
    enabled: effectiveId !== null,
    retry: false,
  });
  const history = useInfiniteQuery({
    queryKey: ["price-history", effectiveId],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      getStandardPriceVersions({
        standardItemId: effectiveId!,
        afterId: pageParam,
        signal,
      }),
    getNextPageParam: safeNextCursor,
    enabled: effectiveId !== null,
    retry: false,
  });
  const historyVersions = uniqueById(
    history.data?.pages.flatMap((page) => page.versions) ?? [],
  );
  const {
    fetchNextPage: fetchNextHistoryPage,
    hasNextPage: hasNextHistoryPage,
    isFetchNextPageError: isFetchNextHistoryPageError,
    isFetchingNextPage: isFetchingNextHistoryPage,
  } = history;
  const linkedVersion = historyVersions.find(
    (version) => version.id === activeLinkedVersionId,
  );
  const historyCursor = history.data?.pages.at(-1)?.next_cursor ?? null;
  const historyCursorKey =
    effectiveId !== null && historyCursor !== null
      ? `${effectiveId}:${historyCursor}`
      : null;
  const linkedVersionMissing =
    activeLinkedVersionId !== null &&
    !linkedVersion &&
    history.data !== undefined &&
    !hasNextHistoryPage &&
    !isFetchingNextHistoryPage &&
    !isFetchNextHistoryPageError;

  useEffect(() => {
    if (
      activeLinkedVersionId === null ||
      linkedVersion ||
      !hasNextHistoryPage ||
      isFetchingNextHistoryPage ||
      isFetchNextHistoryPageError ||
      historyCursorKey === null ||
      attemptedHistoryCursors.current.has(historyCursorKey)
    ) {
      return;
    }
    attemptedHistoryCursors.current.add(historyCursorKey);
    void fetchNextHistoryPage();
  }, [
    activeLinkedVersionId,
    linkedVersion,
    hasNextHistoryPage,
    isFetchingNextHistoryPage,
    isFetchNextHistoryPageError,
    historyCursorKey,
    fetchNextHistoryPage,
  ]);

  useEffect(() => {
    if (
      activeLinkedVersionId === null ||
      !linkedVersion ||
      focusedLinkedVersionId.current === activeLinkedVersionId
    ) {
      return;
    }
    const link = versionLinkRefs.current.get(activeLinkedVersionId);
    if (!link) return;
    link.focus();
    focusedLinkedVersionId.current = activeLinkedVersionId;
  }, [activeLinkedVersionId, linkedVersion]);

  const approval = useMutation({
    mutationFn: () =>
      approvePrice(effectiveId!, {
        expected_fingerprint: draft.data!.fingerprint,
        expected_current_version_id:
          draft.data!.current_standard_price_version_id,
        approved_by: actor.trim(),
      }),
    onSuccess: (version) => {
      setNotice({
        kind: "success",
        text: `표준단가 v${version.version_number}를 승인했습니다.`,
      });
      setActor("");
      void queryClient.invalidateQueries({
        queryKey: ["price-history", effectiveId],
      });
      void queryClient.invalidateQueries({
        queryKey: ["price-draft", effectiveId],
      });
    },
    onError: (error) => {
      setNotice({
        kind: "error",
        text:
          error instanceof ApiError && error.status === 409
            ? "계산 근거나 승인 이력이 변경되었습니다. 최신 초안을 다시 확인해 주세요."
            : "표준단가 버전을 승인하지 못했습니다.",
      });
      void queryClient.invalidateQueries({ queryKey: ["price-history", effectiveId] });
      void queryClient.invalidateQueries({ queryKey: ["price-draft", effectiveId] });
    },
  });

  return (
    <main className="workspace-page standard-price-page">
      <header className="page-heading">
        <div><p className="section-kicker">Standard price database</p><h1>표준 DB</h1></div>
        <p>과거 견적의 현재 유효 행만 계산하고, 승인 시점의 근거를 변경 불가능한 버전으로 남깁니다.</p>
      </header>
      {notice && (
        <div className={`workspace-notice is-${notice.kind}`} role={notice.kind === "error" ? "alert" : "status"}>
          {notice.text}
        </div>
      )}
      <div className="split-workspace price-workspace">
        <section className="work-list" aria-label="표준품목 목록">
          <header><strong>표준품목</strong><span>{catalogItems.length}건</span></header>
          {catalog.isPending && <p className="inline-state">불러오는 중…</p>}
          {catalog.isError && <p className="inline-state is-error">표준품목을 불러오지 못했습니다.</p>}
          <ul>
            {catalogItems.map((item, index) => (
              <li key={item.id} style={{ "--row-index": index } as React.CSSProperties}>
                <button
                  type="button"
                  className={effectiveId === item.id ? "work-row is-selected" : "work-row"}
                  onClick={() => {
                    setRequestedItemId(null);
                    setSelectedId(item.id);
                    setNotice(null);
                    setActor("");
                  }}
                >
                  <strong>{item.current_version.canonical_name}</strong>
                  <span>{item.current_version.canonical_spec ?? "사양 없음"}</span>
                  <small>멤버 {item.member_count}개 · 카탈로그 v{item.current_version.version_number}</small>
                </button>
              </li>
            ))}
          </ul>
          {hasNextPage && (
            <button
              className="load-more-button"
              type="button"
              disabled={isFetchingNextPage}
              onClick={() => void fetchNextPage()}
            >
              {isFetchingNextPage
                ? "다음 표준품목 불러오는 중…"
                : "다음 표준품목 불러오기"}
            </button>
          )}
        </section>
        <section className="work-detail" aria-label="표준단가 상세">
          {requestedItemId !== null && !linkedItem && (
            <div
              className={`empty-detail ${
                catalog.isError || linkedItemMissing || isFetchNextPageError
                  ? "is-error"
                  : ""
              }`}
              role={
                catalog.isError || linkedItemMissing || isFetchNextPageError
                  ? "alert"
                  : "status"
              }
            >
              {catalog.isError && !isFetchNextPageError ? (
                <>
                  <p>표준품목 목록을 불러오지 못했습니다.</p>
                  <button type="button" onClick={() => void catalog.refetch()}>
                    표준품목 목록 다시 시도
                  </button>
                </>
              ) : isFetchNextPageError ? (
                <>
                  <p>표준품목을 찾는 중 다음 목록을 불러오지 못했습니다.</p>
                  <button type="button" onClick={() => void fetchNextPage()}>
                    딥링크 품목 다시 찾기
                  </button>
                </>
              ) : linkedItemMissing ? (
                <>
                  <p>요청한 표준품목 근거를 찾을 수 없음</p>
                  <button
                    type="button"
                    onClick={() => {
                      setRequestedItemId(null);
                      setSelectedId(catalogItems[0]?.id ?? null);
                    }}
                  >
                    목록 돌아가기
                  </button>
                </>
              ) : (
                <p>딥링크의 표준품목을 찾는 중…</p>
              )}
            </div>
          )}
          {requestedItemId === null && !selected && (
            <div className="empty-detail"><p>표준품목을 선택하세요.</p></div>
          )}
          {selected && (
            <div className="detail-content">
              <header className="record-heading">
                <div>
                  <p className="section-kicker">Standard item #{selected.id}</p>
                  <h1>{selected.current_version.canonical_name}</h1>
                  <p>{selected.current_version.canonical_spec ?? "사양 없음"} · {selected.current_version.canonical_unit ?? "단위 없음"}</p>
                </div>
                <span className="status-tag">멤버 {selected.member_count}개</span>
              </header>

              {draft.isPending && <p className="inline-state">단가 초안을 계산하는 중…</p>}
              {draft.isError && (
                <div className="inline-state is-error" role="alert">
                  <p>{priceDraftError(draft.error)}</p>
                  <button type="button" onClick={() => void draft.refetch()}>다시 계산</button>
                </div>
              )}
              {draft.data && (
                <>
                  <section className="price-draft-section">
                    <div className="section-heading">
                      <div><p className="section-kicker">Current draft</p><h2>승인 대기 초안</h2></div>
                      <span>{draft.data.observation_count}개 관측값</span>
                    </div>
                    <dl className="price-metrics">
                      <PriceMetric label="최저" value={draft.data.prices.minimum} />
                      <PriceMetric label="중앙값" value={draft.data.prices.median} featured />
                      <PriceMetric label="평균" value={draft.data.prices.average} />
                      <PriceMetric label="최고" value={draft.data.prices.maximum} />
                    </dl>
                    <div className="draft-context">
                      <span>공급사 {draft.data.supplier_count}곳</span>
                      <span>최근 견적 {draft.data.latest_quote_date ?? "날짜 없음"}</span>
                      <span>계산식 {draft.data.calculation_version}</span>
                    </div>
                    <div className="approval-line">
                      <label><span>승인자</span><input value={actor} onChange={(event) => setActor(event.target.value)} /></label>
                      <button
                        type="button"
                        disabled={
                          !actor.trim() ||
                          approval.isPending
                        }
                        onClick={() => approval.mutate()}
                      >
                        {approval.isPending ? "승인 중…" : "표준단가 버전 승인"}
                      </button>
                    </div>
                  </section>

                  <section className="source-table-section">
                    <div className="section-heading">
                      <div><p className="section-kicker">Contributing evidence</p><h2>계산 근거</h2></div>
                      <span>현재 정제·그룹핑 결정</span>
                    </div>
                    <div className="table-scroll">
                      <table className="data-table">
                        <thead><tr><th>공급사</th><th>단가</th><th>견적일</th><th>원본</th><th>행</th></tr></thead>
                        <tbody>
                          {draft.data.observations.map((row) => (
                            <tr key={row.raw_item_id}>
                              <td>{row.supplier_name ?? "미등록"}</td>
                              <td className="numeric">{row.unit_price}</td>
                              <td>{row.quote_date ?? "—"}</td>
                              <td>
                                <a
                                  href={`/grouping?raw_item_id=${row.raw_item_id}`}
                                  aria-label={`원천행 ${row.raw_item_id} 감사 보기`}
                                >
                                  {row.source.path}
                                </a>
                              </td>
                              <td>{row.source.row ?? row.source.page ?? "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </section>
                </>
              )}

              <section className="history-section">
                <div className="section-heading">
                  <div><p className="section-kicker">Immutable ledger</p><h2>승인 이력</h2></div>
                  <span>{historyVersions.length}개 버전</span>
                </div>
                {history.isPending && <p className="inline-state">이력을 불러오는 중…</p>}
                {isFetchNextHistoryPageError && activeLinkedVersionId !== null && (
                  <div className="inline-state is-error" role="alert">
                    <p>요청한 버전을 찾는 중 다음 이력을 불러오지 못했습니다.</p>
                    <button
                      type="button"
                      onClick={() => void fetchNextHistoryPage()}
                    >
                      딥링크 버전 다시 찾기
                    </button>
                  </div>
                )}
                {history.isError && !isFetchNextHistoryPageError && (
                  <div className="inline-state is-error" role="alert">
                    <p>승인 이력을 불러오지 못했습니다.</p>
                    <button type="button" onClick={() => void history.refetch()}>
                      이력 다시 시도
                    </button>
                  </div>
                )}
                {linkedVersionMissing && (
                  <div className="inline-state is-error" role="alert">
                    <p>요청한 표준단가 버전 근거를 찾을 수 없음</p>
                    <a href={`/standard-prices?item_id=${selected.id}`}>
                      목록 돌아가기
                    </a>
                  </div>
                )}
                <ol className="version-ledger">
                  {historyVersions.map((version) => (
                    <VersionRow
                      key={version.id}
                      version={version}
                      standardItemId={selected.id}
                      linkedVersionId={activeLinkedVersionId}
                      auditLinkRef={(element) => {
                        if (element) versionLinkRefs.current.set(version.id, element);
                        else versionLinkRefs.current.delete(version.id);
                      }}
                    />
                  ))}
                </ol>
                {hasNextHistoryPage && (
                  <button
                    className="load-more-button"
                    type="button"
                    disabled={isFetchingNextHistoryPage}
                    onClick={() => void fetchNextHistoryPage()}
                  >
                    {isFetchingNextHistoryPage
                      ? "다음 승인 이력 불러오는 중…"
                      : "다음 승인 이력 불러오기"}
                  </button>
                )}
              </section>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

function PriceMetric({ label, value, featured = false }: { label: string; value: string; featured?: boolean }) {
  return <div className={featured ? "is-featured" : undefined}><dt>{label}</dt><dd>{value}</dd></div>;
}

function VersionRow({
  version,
  standardItemId,
  linkedVersionId,
  auditLinkRef,
}: {
  version: PriceVersion;
  standardItemId: number;
  linkedVersionId: number | null;
  auditLinkRef: (element: HTMLAnchorElement | null) => void;
}) {
  return (
    <li>
      <div>
        <strong>
          <a
            ref={auditLinkRef}
            href={`/standard-prices?item_id=${standardItemId}&version_id=${version.id}`}
            aria-label={`표준단가 v${version.version_number} 감사 링크`}
          >
            v{version.version_number} · {version.approved_by}
          </a>
        </strong>
        <span>{formatDateTime(version.approved_at)} · 관측값 {version.observation_count}개</span>
      </div>
      <div className="version-price"><span>중앙값</span><strong>{version.prices.median}</strong></div>
      <details
        aria-label={`표준단가 v${version.version_number} 버전 근거`}
        {...(linkedVersionId === version.id ? { open: true } : {})}
      >
        <summary>버전 근거</summary>
        <p>최저 {version.prices.minimum} · 평균 {version.prices.average} · 최고 {version.prices.maximum}</p>
        <p>표준품목 버전 {version.standard_item_version?.version_number ?? "레거시"} · 제외 {version.excluded_count}건 · 재검토 {version.review_required_count}건</p>
      </details>
    </li>
  );
}

function priceDraftError(error: unknown) {
  if (error instanceof ApiError && error.errorCode === "NO_ELIGIBLE_PRICE_OBSERVATIONS") {
    return "현재 계산에 사용할 수 있는 견적 행이 없습니다.";
  }
  return "표준단가 초안을 계산하지 못했습니다.";
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function positiveIntegerParam(name: string) {
  const value = Number(new URLSearchParams(window.location.search).get(name));
  return Number.isInteger(value) && value > 0 ? value : null;
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

function uniqueById<T extends { id: number }>(items: T[]) {
  const seen = new Set<number>();
  return items.filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}
