import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";

import {
  getStandardEvidence,
  getStandardItems,
  getStandardPriceVersions,
  type EvidenceQuality,
  type PriceVersion,
  type StandardItemSummary,
} from "../api/client";
import { EvidenceBadge } from "../components/EvidenceBadge";
import { MetricStrip } from "../components/MetricStrip";

export function StandardPricesPage() {
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [quality, setQuality] = useState<EvidenceQuality | "">("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [requestedItemId, setRequestedItemId] = useState<number | null>(
    positiveIntegerParam("item_id"),
  );
  const attemptedCatalogCursors = useRef(new Set<number>());

  useEffect(() => {
    document.title = "표준 DB · Price Analyzer";
    return () => {
      document.title = "Price Analyzer";
    };
  }, []);

  const catalog = useInfiniteQuery({
    queryKey: ["standard-db", search, quality],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      getStandardItems({
        afterId: pageParam,
        search: search || undefined,
        evidenceQuality: quality || undefined,
        signal,
      }),
    getNextPageParam: safeNextCursor,
    retry: false,
  });
  const items = uniqueById(
    catalog.data?.pages.flatMap((page) => page.items) ?? [],
  );
  const {
    fetchNextPage: fetchNextCatalogPage,
    hasNextPage: hasNextCatalogPage,
    isFetchNextPageError: isFetchNextCatalogPageError,
    isFetchingNextPage: isFetchingNextCatalogPage,
  } = catalog;
  const requestedItem = items.find((item) => item.id === requestedItemId);
  const selected =
    requestedItem ??
    (requestedItemId === null
      ? items.find((item) => item.id === selectedId) ?? items[0] ?? null
      : null);
  const latestBuild = catalog.data?.pages[0]?.latest_build ?? null;
  const catalogCursor = catalog.data?.pages.at(-1)?.next_cursor ?? null;

  useEffect(() => {
    if (
      requestedItemId === null ||
      requestedItem ||
      !hasNextCatalogPage ||
      isFetchingNextCatalogPage ||
      isFetchNextCatalogPageError ||
      catalogCursor === null ||
      attemptedCatalogCursors.current.has(catalogCursor)
    ) {
      return;
    }
    attemptedCatalogCursors.current.add(catalogCursor);
    void fetchNextCatalogPage();
  }, [
    requestedItemId,
    requestedItem,
    hasNextCatalogPage,
    isFetchingNextCatalogPage,
    isFetchNextCatalogPageError,
    catalogCursor,
    fetchNextCatalogPage,
  ]);

  const evidence = useInfiniteQuery({
    queryKey: [
      "standard-db-evidence",
      selected?.id,
      selected?.current_price_version_id,
    ],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      getStandardEvidence({
        standardItemId: selected!.id,
        priceVersionId: selected!.current_price_version_id!,
        afterId: pageParam,
        signal,
      }),
    getNextPageParam: safeNextCursor,
    enabled:
      selected !== null && selected.current_price_version_id !== null,
    retry: false,
  });
  const observations = uniqueByRawItemId(
    evidence.data?.pages.flatMap((page) => page.observations) ?? [],
  );

  const history = useInfiniteQuery({
    queryKey: ["standard-db-history", selected?.id],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      getStandardPriceVersions({
        standardItemId: selected!.id,
        afterId: pageParam,
        signal,
      }),
    getNextPageParam: safeNextCursor,
    enabled: selected !== null,
    retry: false,
  });
  const versions = uniqueById(
    history.data?.pages.flatMap((page) => page.versions) ?? [],
  );

  return (
    <main className="workspace-page standard-db-page">
      <header className="standard-db-heading">
        <div>
          <p className="section-kicker">Historical quote reference</p>
          <h1>표준 DB</h1>
          <p>과거 견적의 정제 완료 품목과 단가 근거를 읽기 전용으로 확인합니다.</p>
        </div>
        <div className="build-status" aria-label="마지막 구축 상태">
          <span>마지막 구축</span>
          <strong>
            {latestBuild ? formatDateTime(latestBuild.built_at) : "구축 기록 없음"}
          </strong>
          <small>{latestBuild?.rule_version ?? "—"}</small>
        </div>
      </header>

      <form
        className="standard-db-toolbar"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          setSelectedId(null);
          setRequestedItemId(null);
          setSearch(searchInput.trim());
        }}
      >
        <label>
          <span>표준 품목 검색</span>
          <input
            aria-label="표준 품목 검색"
            type="search"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="품명, 사양 또는 단위"
          />
        </label>
        <label>
          <span>근거 품질</span>
          <select
            value={quality}
            onChange={(event) => {
              setSelectedId(null);
              setRequestedItemId(null);
              setQuality(event.target.value as EvidenceQuality | "");
            }}
          >
            <option value="">전체</option>
            <option value="SINGLE_OBSERVATION">근거 1건</option>
            <option value="MULTI_OBSERVATION">근거 2건 이상</option>
          </select>
        </label>
        <button type="submit">검색</button>
      </form>

      <div className="standard-db-workspace">
        <section className="standard-db-list" aria-label="표준 품목 목록">
          <header>
            <strong>표준 품목</strong>
            <span>{items.length.toLocaleString("ko-KR")}건 표시</span>
          </header>
          {catalog.isPending && <p className="inline-state">목록을 불러오는 중…</p>}
          {catalog.isError && !isFetchNextCatalogPageError && (
            <div className="inline-state is-error" role="alert">
              <p>표준 품목을 불러오지 못했습니다.</p>
              <button type="button" onClick={() => void catalog.refetch()}>
                다시 시도
              </button>
            </div>
          )}
          {isFetchNextCatalogPageError && (
            <div className="inline-state is-error" role="alert">
              <p>다음 표준 품목을 불러오지 못했습니다.</p>
              <button type="button" onClick={() => void fetchNextCatalogPage()}>
                품목 다시 시도
              </button>
            </div>
          )}
          {!catalog.isPending && !catalog.isError && items.length === 0 && (
            <p className="inline-state">검색 결과가 없습니다.</p>
          )}
          <ul>
            {items.map((item, index) => (
              <StandardItemRow
                item={item}
                selected={selected?.id === item.id}
                index={index}
                key={item.id}
                onSelect={() => setSelectedId(item.id)}
                onClearRequested={() => setRequestedItemId(null)}
              />
            ))}
          </ul>
          {hasNextCatalogPage && (
            <button
              className="load-more-button"
              type="button"
              disabled={isFetchingNextCatalogPage}
              onClick={() => void fetchNextCatalogPage()}
            >
              {isFetchingNextCatalogPage ? "불러오는 중…" : "품목 더 보기"}
            </button>
          )}
        </section>

        <section className="standard-db-detail" aria-label="선택한 표준 품목">
          {!selected ? (
            <div className="empty-detail">
              <p>왼쪽 목록에서 표준 품목을 선택하세요.</p>
            </div>
          ) : (
            <StandardItemDetail
              item={selected}
              observations={observations}
              evidencePending={evidence.isPending}
              evidenceError={evidence.isError}
              evidenceNextError={evidence.isFetchNextPageError}
              retryEvidence={() => void evidence.refetch()}
              retryNextEvidence={() => void evidence.fetchNextPage()}
              hasMoreEvidence={Boolean(evidence.hasNextPage)}
              loadMoreEvidence={() => void evidence.fetchNextPage()}
              evidenceLoadingMore={evidence.isFetchingNextPage}
              versions={versions}
              historyPending={history.isPending}
              historyError={history.isError}
              historyNextError={history.isFetchNextPageError}
              retryHistory={() => void history.refetch()}
              retryNextHistory={() => void history.fetchNextPage()}
              hasMoreHistory={Boolean(history.hasNextPage)}
              loadMoreHistory={() => void history.fetchNextPage()}
              historyLoadingMore={history.isFetchingNextPage}
            />
          )}
        </section>
      </div>
    </main>
  );
}

function StandardItemRow({
  item,
  selected,
  index,
  onSelect,
  onClearRequested,
}: {
  item: StandardItemSummary;
  selected: boolean;
  index: number;
  onSelect: () => void;
  onClearRequested: () => void;
}) {
  return (
    <li style={{ "--row-index": index } as React.CSSProperties}>
      <button
        type="button"
        className={`standard-db-row ${selected ? "is-selected" : ""}`}
        aria-current={selected ? "true" : undefined}
        aria-pressed={selected}
        onClick={() => {
          onClearRequested();
          onSelect();
        }}
      >
        <span>
          <strong>{item.current_version.canonical_name}</strong>
          <small>
            {item.current_version.canonical_spec ?? "사양 없음"} ·{" "}
            {item.current_version.canonical_unit ?? "단위 없음"}
          </small>
        </span>
        <span className="standard-db-row-meta">
          <EvidenceBadge
            quality={item.evidence_quality}
            count={item.observation_count}
          />
          <strong>{formatWon(item.current_price?.median ?? null)}</strong>
        </span>
      </button>
    </li>
  );
}

function StandardItemDetail({
  item,
  observations,
  evidencePending,
  evidenceError,
  evidenceNextError,
  retryEvidence,
  retryNextEvidence,
  hasMoreEvidence,
  loadMoreEvidence,
  evidenceLoadingMore,
  versions,
  historyPending,
  historyError,
  historyNextError,
  retryHistory,
  retryNextHistory,
  hasMoreHistory,
  loadMoreHistory,
  historyLoadingMore,
}: {
  item: StandardItemSummary;
  observations: Array<{
    raw_item_id: number;
    unit_price: string;
    supplier_name: string | null;
    maker: string | null;
    quote_date: string | null;
    source: {
      logical_name: string;
      path: string;
      sheet: string | null;
      page: number | null;
      row: number | null;
      cells: string | null;
    };
  }>;
  evidencePending: boolean;
  evidenceError: boolean;
  evidenceNextError: boolean;
  retryEvidence: () => void;
  retryNextEvidence: () => void;
  hasMoreEvidence: boolean;
  loadMoreEvidence: () => void;
  evidenceLoadingMore: boolean;
  versions: PriceVersion[];
  historyPending: boolean;
  historyError: boolean;
  historyNextError: boolean;
  retryHistory: () => void;
  retryNextHistory: () => void;
  hasMoreHistory: boolean;
  loadMoreHistory: () => void;
  historyLoadingMore: boolean;
}) {
  const price = item.current_price;
  return (
    <div className="standard-db-detail-content">
      <header className="standard-record-heading">
        <div>
          <p className="section-kicker">Standard item #{item.id}</p>
          <h2>{item.current_version.canonical_name}</h2>
          <p>
            {item.current_version.canonical_spec ?? "사양 없음"} ·{" "}
            {item.current_version.canonical_unit ?? "단위 없음"}
          </p>
        </div>
        <EvidenceBadge
          quality={item.evidence_quality}
          count={item.observation_count}
        />
      </header>

      <MetricStrip
        items={[
          { label: "최저", value: formatWon(price?.minimum ?? null) },
          { label: "중앙값", value: formatWon(price?.median ?? null), emphasis: true },
          { label: "평균", value: formatWon(price?.average ?? null) },
          { label: "최고", value: formatWon(price?.maximum ?? null) },
        ]}
      />

      <dl className="standard-context-strip">
        <div><dt>공급사</dt><dd>{item.supplier_summary.join(", ") || "미등록"}</dd></div>
        <div><dt>제조사</dt><dd>{item.maker_summary.join(", ") || "미등록"}</dd></div>
        <div>
          <dt>견적일 범위</dt>
          <dd>{formatDateRange(item.quote_date_start, item.quote_date_end)}</dd>
        </div>
      </dl>

      <section className="standard-evidence-section">
        <div className="section-heading">
          <div>
            <p className="section-kicker">Traceable observations</p>
            <h2>가격 근거</h2>
          </div>
          <span>{item.observation_count}건</span>
        </div>
        {evidencePending && <p className="inline-state">근거를 불러오는 중…</p>}
        {evidenceError && !evidenceNextError && (
          <div className="inline-state is-error" role="alert">
            <p>가격 근거를 불러오지 못했습니다.</p>
            <button type="button" onClick={retryEvidence}>다시 시도</button>
          </div>
        )}
        {evidenceNextError && (
          <div className="inline-state is-error" role="alert">
            <p>다음 가격 근거를 불러오지 못했습니다.</p>
            <button type="button" onClick={retryNextEvidence}>
              근거 다시 시도
            </button>
          </div>
        )}
        {!evidencePending && !evidenceError && observations.length === 0 && (
          <p className="inline-state">표시할 원본 근거가 없습니다.</p>
        )}
        {observations.length > 0 && (
          <div className="table-scroll">
            <table className="data-table standard-evidence-table">
              <thead>
                <tr>
                  <th>공급사</th>
                  <th>제조사</th>
                  <th>단가</th>
                  <th>견적일</th>
                  <th>원본 위치</th>
                </tr>
              </thead>
              <tbody>
                {observations.map((row) => (
                  <tr key={row.raw_item_id}>
                    <td>{row.supplier_name ?? "미등록"}</td>
                    <td>{row.maker ?? "미등록"}</td>
                    <td className="numeric">{formatWon(row.unit_price)}</td>
                    <td>{row.quote_date ?? "—"}</td>
                    <td>
                      <a href={`/grouping?raw_item_id=${row.raw_item_id}`}>
                        원본 견적 근거
                      </a>
                      <small>
                        {sourceLocation(row.source)}
                      </small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {hasMoreEvidence && (
          <button
            className="load-more-button"
            type="button"
            disabled={evidenceLoadingMore}
            onClick={loadMoreEvidence}
          >
            {evidenceLoadingMore ? "불러오는 중…" : "근거 더 보기"}
          </button>
        )}
      </section>

      <section className="standard-history-section">
        <div className="section-heading">
          <div>
            <p className="section-kicker">Immutable ledger</p>
            <h2>가격 버전 이력</h2>
          </div>
          <span>{versions.length}개 버전</span>
        </div>
        {historyPending && <p className="inline-state">이력을 불러오는 중…</p>}
        {historyError && !historyNextError && (
          <div className="inline-state is-error" role="alert">
            <p>가격 버전 이력을 불러오지 못했습니다.</p>
            <button type="button" onClick={retryHistory}>다시 시도</button>
          </div>
        )}
        {historyNextError && (
          <div className="inline-state is-error" role="alert">
            <p>다음 가격 이력을 불러오지 못했습니다.</p>
            <button type="button" onClick={retryNextHistory}>
              이력 다시 시도
            </button>
          </div>
        )}
        {!historyPending && !historyError && versions.length === 0 && (
          <p className="inline-state">저장된 가격 버전이 없습니다.</p>
        )}
        <ol className="standard-version-ledger">
          {versions.map((version) => (
            <li key={version.id}>
              <div>
                <strong>v{version.version_number}</strong>
                <span>{formatDateTime(version.approved_at)}</span>
              </div>
              <EvidenceBadge
                quality={version.evidence_quality}
                count={version.observation_count}
              />
              <dl>
                <div><dt>중앙값</dt><dd>{formatWon(version.prices.median)}</dd></div>
                <div><dt>범위</dt><dd>{formatWon(version.prices.minimum)}–{formatWon(version.prices.maximum)}</dd></div>
              </dl>
            </li>
          ))}
        </ol>
        {hasMoreHistory && (
          <button
            className="load-more-button"
            type="button"
            disabled={historyLoadingMore}
            onClick={loadMoreHistory}
          >
            {historyLoadingMore ? "불러오는 중…" : "가격 이력 더 보기"}
          </button>
        )}
      </section>
    </div>
  );
}

function formatWon(value: string | null) {
  if (value === null) return "—";
  const amount = Number(value);
  return Number.isFinite(amount)
    ? `${new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 }).format(amount)}원`
    : "—";
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatDateRange(start: string | null, end: string | null) {
  if (!start && !end) return "미등록";
  if (start === end || !end) return start ?? end ?? "미등록";
  return `${start} – ${end}`;
}

function sourceLocation(source: {
  logical_name: string;
  sheet: string | null;
  page: number | null;
  row: number | null;
  cells: string | null;
}) {
  const location = [
    source.sheet,
    source.page === null ? null : `${source.page}쪽`,
    source.row === null ? null : `${source.row}행`,
    source.cells,
  ].filter(Boolean);
  return `${source.logical_name}${location.length ? ` · ${location.join(" · ")}` : ""}`;
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

function uniqueByRawItemId<T extends { raw_item_id: number }>(items: T[]) {
  const seen = new Set<number>();
  return items.filter((item) => {
    if (seen.has(item.raw_item_id)) return false;
    seen.add(item.raw_item_id);
    return true;
  });
}

function positiveIntegerParam(name: string) {
  const value = Number(new URLSearchParams(window.location.search).get(name));
  return Number.isInteger(value) && value > 0 ? value : null;
}
