import { useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import {
  getStandardEvidence,
  getStandardItems,
  getStandardPriceVersion,
  getStandardPriceVersions,
  getSourceCoverageSummary,
  type EvidenceQuality,
  type PriceVersion,
  type StandardItemSummary,
} from "../api/client";
import { safeNextCursor, uniqueByRawItemId } from "../api/pagination";
import { EvidenceBadge } from "../components/EvidenceBadge";
import { LoadingLabel } from "../components/LoadingLabel";
import { MetricStrip } from "../components/MetricStrip";

export function StandardPricesPage() {
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [quality, setQuality] = useState<EvidenceQuality | "">("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [requestedItemId, setRequestedItemId] = useState<number | null>(
    positiveIntegerParam("item_id"),
  );
  const [requestedVersionId, setRequestedVersionId] = useState<number | null>(
    positiveIntegerParam("version_id"),
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
  const sourceCoverage = useQuery({
    queryKey: ["source-coverage-summary"],
    queryFn: ({ signal }) => getSourceCoverageSummary(signal),
    retry: false,
  });
  const items = uniqueById(
    catalog.data?.pages.flatMap((page) => page.items) ?? [],
  );
  const displayedItems = [...items].sort((left, right) =>
    left.current_version.canonical_name.localeCompare(
      right.current_version.canonical_name,
      "ko-KR",
      { numeric: true },
    ),
  );
  const {
    fetchNextPage: fetchNextCatalogPage,
    hasNextPage: hasNextCatalogPage,
    isFetchNextPageError: isFetchNextCatalogPageError,
    isFetchingNextPage: isFetchingNextCatalogPage,
    isFetching: isFetchingCatalog,
  } = catalog;
  const requestedItem = items.find((item) => item.id === requestedItemId);
  const selected =
    requestedItem ??
    (requestedItemId === null
      ? items.find((item) => item.id === selectedId) ?? items[0] ?? null
      : null);
  const latestBuild = catalog.data?.pages[0]?.latest_build ?? null;
  const catalogCursor = catalog.data?.pages.at(-1)?.next_cursor ?? null;

  const requestedVersion = useQuery({
    queryKey: ["standard-db-price-version", selected?.id, requestedVersionId],
    queryFn: ({ signal }) =>
      getStandardPriceVersion({
        standardItemId: selected!.id,
        versionId: requestedVersionId!,
        signal,
      }),
    enabled: selected !== null && requestedVersionId !== null,
    retry: false,
  });
  const activePriceVersionId =
    requestedVersionId === null
      ? selected?.current_price_version_id ?? null
      : requestedVersion.data?.id ?? null;

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

  const requestedItemFound = requestedItemId !== null && requestedItem !== undefined;
  const requestedItemExhausted =
    requestedItemId !== null &&
    !requestedItem &&
    !hasNextCatalogPage &&
    !isFetchingNextCatalogPage &&
    !isFetchingCatalog;
  const requestedItemFetchFailed =
    requestedItemId !== null && !requestedItem && isFetchNextCatalogPageError;

  useEffect(() => {
    if (!requestedItemFound) return;
    const detail = document.getElementById("standard-item-detail");
    if (detail && typeof detail.scrollIntoView === "function") {
      detail.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [requestedItemFound]);

  const evidence = useInfiniteQuery({
    queryKey: [
      "standard-db-evidence",
      selected?.id,
      activePriceVersionId,
    ],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      getStandardEvidence({
        standardItemId: selected!.id,
        priceVersionId: activePriceVersionId!,
        afterId: pageParam,
        signal,
      }),
    getNextPageParam: safeNextCursor,
    enabled: selected !== null && activePriceVersionId !== null,
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
  const versionGroups = useMemo(() => compactPriceVersions(versions), [versions]);
  const sourceCoverageData = sourceCoverage.data;
  const hasSourceCoverage = Boolean(
    sourceCoverageData &&
      Number.isFinite(sourceCoverageData.scanned_files) &&
      Number.isFinite(sourceCoverageData.parsed_files) &&
      Number.isFinite(sourceCoverageData.unparsed_files) &&
      Number.isFinite(sourceCoverageData.parser_required_files) &&
      Number.isFinite(sourceCoverageData.ocr_required_files) &&
      Number.isFinite(sourceCoverageData.recollection_required_files) &&
      Number.isFinite(sourceCoverageData.recovered_copy_files) &&
      Number.isFinite(sourceCoverageData.security_release_required_files) &&
      Number.isFinite(sourceCoverageData.unsupported_files),
  );

  const exportParams = new URLSearchParams();
  if (search) exportParams.set("search", search);
  if (quality) exportParams.set("evidence_quality", quality);
  const exportQuery = exportParams.toString();
  const catalogExportHref = `/api/catalog/standard-items/export${
    exportQuery ? `?${exportQuery}` : ""
  }`;

  return (
    <main className="workspace-page standard-db-page">
      <header className="standard-db-heading">
        <div>
          <p className="section-kicker">과거 견적 기준</p>
          <h1>표준 DB</h1>
          <p>과거 견적에서 정제한 품목별 단가 범위와 원본 근거를 확인합니다.</p>
        </div>
        <div className="build-status" aria-label="최근 갱신 상태">
          <span>최근 갱신</span>
          <strong>
            {latestBuild
              ? formatDateTime(latestBuild.built_at)
              : isFetchingCatalog
                ? "불러오는 중…"
                : "구축 기록 없음"}
          </strong>
        </div>
      </header>

      {hasSourceCoverage && sourceCoverageData && (
        <details
          className="source-coverage-disclosure"
          aria-label="데이터 구축 현황"
        >
          <summary>
            <span>데이터 구축 현황</span>
            <small>
              원본 문서 {sourceCoverageData.parsed_files.toLocaleString("ko-KR")}개 활용 · 품목 미추출{" "}
              {sourceCoverageData.unparsed_files.toLocaleString("ko-KR")}개
            </small>
          </summary>
          <section className="source-coverage" aria-label="원본 견적 활용 현황">
            <div>
              <span>3차 원본</span>
              <strong>{sourceCoverageData.scanned_files.toLocaleString("ko-KR")}개</strong>
            </div>
            <div className="is-complete">
              <span>품목 추출 완료</span>
              <strong>{sourceCoverageData.parsed_files.toLocaleString("ko-KR")}개</strong>
            </div>
            <div>
              <span>추가 파서 대상</span>
              <strong>{sourceCoverageData.parser_required_files.toLocaleString("ko-KR")}개</strong>
            </div>
            <div>
              <span>OCR 대상</span>
              <strong>{sourceCoverageData.ocr_required_files.toLocaleString("ko-KR")}개</strong>
            </div>
            <div className="is-warning">
              <span>열기 실패</span>
              <strong>{sourceCoverageData.recollection_required_files.toLocaleString("ko-KR")}개</strong>
            </div>
            <div className="is-warning">
              <span>미지원 형식</span>
              <strong>{sourceCoverageData.unsupported_files.toLocaleString("ko-KR")}개</strong>
            </div>
            <p>
              품목 미추출 {sourceCoverageData.unparsed_files.toLocaleString("ko-KR")}개는 추가 파서,
              OCR, 열기 실패, 미지원 형식으로 모두 분류했습니다. 열기 실패본 중 복구본{" "}
              {sourceCoverageData.recovered_copy_files.toLocaleString("ko-KR")}개는 별도 확보됐고,
              나머지 {sourceCoverageData.security_release_required_files.toLocaleString("ko-KR")}개는
              보안해제본 또는 정상본 재수집이 필요합니다.
            </p>
          </section>
        </details>
      )}

      <form
        className="standard-db-toolbar"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          setSelectedId(null);
          setRequestedItemId(null);
          setRequestedVersionId(null);
          setSearch(searchInput.trim());
        }}
      >
        <label className="standard-search-control">
          <span className="sr-only">표준 품목 검색</span>
          <svg aria-hidden="true" viewBox="0 0 24 24">
            <circle cx="11" cy="11" r="6" />
            <path d="m16 16 4 4" />
          </svg>
          <input
            aria-label="표준 품목 검색"
            type="search"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="품명·사양·단위 검색"
          />
        </label>
        <label className="standard-filter-control">
          <span aria-hidden="true">근거</span>
          <select
            aria-label="근거 품질"
            value={quality}
            onChange={(event) => {
              setSelectedId(null);
              setRequestedItemId(null);
              setRequestedVersionId(null);
              setQuality(event.target.value as EvidenceQuality | "");
            }}
          >
            <option value="">전체</option>
            <option value="SUPPLIER_UNKNOWN">제출사 확인 필요</option>
            <option value="SINGLE_OBSERVATION">견적 제출사 1곳</option>
            <option value="MULTI_OBSERVATION">견적 제출사 2곳 이상</option>
          </select>
        </label>
        <button type="submit" className="standard-search-submit">
          <svg aria-hidden="true" viewBox="0 0 24 24">
            <circle cx="11" cy="11" r="6" />
            <path d="m16 16 4 4" />
          </svg>
          <span>검색</span>
        </button>
      </form>

      <div className="standard-db-catalog">
        <section className="standard-db-table-panel" aria-label="표준 품목 목록">
          <header>
            <div>
              <strong>표준 품목 목록</strong>
              <small>품명·사양·단위별로 묶은 가격 기준</small>
            </div>
            <div className="table-panel-actions">
              <span>
                {catalog.isPending
                  ? "불러오는 중…"
                  : `${items.length.toLocaleString("ko-KR")}건 표시`}
              </span>
              <a className="table-export-link" href={catalogExportHref}>
                엑셀 다운로드
              </a>
            </div>
          </header>
          {catalog.isPending && (
            <LoadingLabel as="p" className="inline-state" role="status">목록을 불러오는 중…</LoadingLabel>
          )}
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
          {requestedItemFetchFailed && (
            <div className="inline-state is-error" role="alert">
              <p>요청한 품목을 확인하는 중 오류가 발생했습니다.</p>
              <button type="button" onClick={() => void fetchNextCatalogPage()}>
                다시 시도
              </button>
            </div>
          )}
          {requestedItemExhausted && (
            <p className="inline-state" role="status">
              요청한 품목을 찾을 수 없습니다. 목록에서 다시 선택해 주세요.
            </p>
          )}
          {!catalog.isPending && !catalog.isError && items.length === 0 && (
            <p className="inline-state">검색 결과가 없습니다.</p>
          )}
          {displayedItems.length > 0 && (
            <div className="table-scroll standard-catalog-scroll">
              <table className="data-table standard-catalog-table">
                <thead>
                  <tr>
                    <th>품명</th>
                    <th>규격</th>
                    <th>단위</th>
                    <th className="numeric">최저</th>
                    <th className="numeric">중앙값</th>
                    <th className="numeric">평균</th>
                    <th className="numeric">최고</th>
                    <th className="numeric">근거</th>
                    <th>제품 제조사</th>
                    <th>견적 제출사</th>
                    <th>최근 견적일</th>
                  </tr>
                </thead>
                <tbody>
                  {displayedItems.map((item) => (
                    <StandardItemTableRow
                      item={item}
                      selected={selected?.id === item.id}
                      key={item.id}
                      onSelect={() => setSelectedId(item.id)}
                      onClearRequested={() => {
                        setRequestedItemId(null);
                        setRequestedVersionId(null);
                      }}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {hasNextCatalogPage && (
            <button
              className="load-more-button"
              type="button"
              disabled={isFetchingNextCatalogPage}
              onClick={() => void fetchNextCatalogPage()}
            >
              {isFetchingNextCatalogPage ? (
                <LoadingLabel>불러오는 중…</LoadingLabel>
              ) : (
                "품목 더 보기"
              )}
            </button>
          )}
        </section>

        <section
          className="standard-db-detail"
          id="standard-item-detail"
          aria-label="선택한 표준 품목"
        >
          {!selected ? (
            <div className="empty-detail">
              <p>왼쪽 목록에서 표준 품목을 선택하세요.</p>
            </div>
          ) : (
            <StandardItemDetail
              item={selected}
              snapshotVersion={
                requestedVersionId === null ? null : requestedVersion.data ?? null
              }
              snapshotPending={
                requestedVersionId !== null && requestedVersion.isPending
              }
              snapshotError={
                requestedVersionId !== null && requestedVersion.isError
              }
              observations={observations}
              evidencePending={evidence.isLoading}
              evidenceError={evidence.isError}
              evidenceNextError={evidence.isFetchNextPageError}
              retryEvidence={() => void evidence.refetch()}
              retryNextEvidence={() => void evidence.fetchNextPage()}
              hasMoreEvidence={Boolean(evidence.hasNextPage)}
              loadMoreEvidence={() => void evidence.fetchNextPage()}
              evidenceLoadingMore={evidence.isFetchingNextPage}
              versionGroups={versionGroups}
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

function StandardItemTableRow({
  item,
  selected,
  onSelect,
  onClearRequested,
}: {
  item: StandardItemSummary;
  selected: boolean;
  onSelect: () => void;
  onClearRequested: () => void;
}) {
  const price = item.current_price;
  return (
    <tr className={selected ? "is-selected" : undefined}>
      <td>
        <button
          type="button"
          className="standard-item-name-button"
          aria-expanded={selected}
          aria-controls="standard-item-detail"
          onClick={() => {
            onClearRequested();
            onSelect();
          }}
        >
          <strong>{item.current_version.canonical_name}</strong>
          <small>품목 #{item.id}</small>
        </button>
      </td>
      <td>{displaySpec(item)}</td>
      <td>{item.current_version.canonical_unit ?? "원문에 단위 없음"}</td>
      <td className="numeric">{formatWon(price?.minimum ?? null)}</td>
      <td className="numeric is-emphasis">{formatWon(price?.median ?? null)}</td>
      <td className="numeric">{formatWon(price?.average ?? null)}</td>
      <td className="numeric">{formatWon(price?.maximum ?? null)}</td>
      <td className="numeric">{formatObservationCount(item)}</td>
      <td>{item.maker_summary.join(", ") || "원본에서 확인되지 않음"}</td>
      <td>{item.supplier_summary.join(", ") || "원본에서 확인되지 않음"}</td>
      <td>{formatQuoteDate(item.quote_date_end, item.quote_date_end_quality)}</td>
    </tr>
  );
}

function StandardItemDetail({
  item,
  snapshotVersion,
  snapshotPending,
  snapshotError,
  observations,
  evidencePending,
  evidenceError,
  evidenceNextError,
  retryEvidence,
  retryNextEvidence,
  hasMoreEvidence,
  loadMoreEvidence,
  evidenceLoadingMore,
  versionGroups,
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
  snapshotVersion: PriceVersion | null;
  snapshotPending: boolean;
  snapshotError: boolean;
  observations: Array<{
    raw_item_id: number;
    unit_price: string;
    supplier_name: string | null;
    maker: string | null;
    quote_date: string | null;
    quote_date_quality?: "CONFIRMED" | "REFERENCE_BACKFILL" | "FILE_DATE_INFERRED" | null;
      source: {
      variant_id: number;
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
  versionGroups: PriceVersionGroup[];
  historyPending: boolean;
  historyError: boolean;
  historyNextError: boolean;
  retryHistory: () => void;
  retryNextHistory: () => void;
  hasMoreHistory: boolean;
  loadMoreHistory: () => void;
  historyLoadingMore: boolean;
}) {
  const pinned = snapshotPending || snapshotError || snapshotVersion !== null;
  const price = pinned ? snapshotVersion?.prices ?? null : item.current_price;
  const evidenceQuality = pinned
    ? snapshotVersion?.evidence_quality ?? null
    : item.evidence_quality;
  const observationCount = pinned
    ? snapshotVersion?.observation_count ?? 0
    : item.observation_count ?? 0;
  const supplierCount = pinned
    ? snapshotVersion?.supplier_count
    : item.supplier_count;
  return (
    <div className="standard-db-detail-content">
      {snapshotPending && (
        <LoadingLabel as="p" className="inline-state" role="status">
          분석 당시 가격 버전을 불러오는 중입니다.
        </LoadingLabel>
      )}
      {snapshotError && (
        <div className="inline-state is-error" role="alert">
          요청한 분석 당시 가격 버전을 찾을 수 없습니다.
        </div>
      )}
      {snapshotVersion && (
        <div className="build-status" aria-label="분석 당시 가격 버전">
          <span>분석 당시 가격 버전</span>
          <strong>v{snapshotVersion.version_number}</strong>
          <small>{formatDateTime(snapshotVersion.approved_at)}</small>
        </div>
      )}
      <header className="standard-record-heading">
        <div>
          <p className="section-kicker">표준 품목 #{item.id}</p>
          <h2>{item.current_version.canonical_name}</h2>
          <p>
            {displaySpec(item)} ·{" "}
            {item.current_version.canonical_unit ?? "원문에 단위 없음"}
          </p>
        </div>
        <EvidenceBadge
          quality={evidenceQuality}
          count={observationCount}
          supplierCount={supplierCount}
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
      {price === null && (
        <p className="inline-state">현재 생성된 표준단가가 없습니다.</p>
      )}

      <dl className="standard-context-strip">
        <div><dt>견적 제출사</dt><dd>{item.supplier_summary.join(", ") || "원본에서 확인되지 않음"}</dd></div>
        <div><dt>제품 제조사</dt><dd>{item.maker_summary.join(", ") || "원본에서 확인되지 않음"}</dd></div>
        <div>
          <dt>견적일 범위</dt>
          <dd>
            {formatDateRange(
              item.quote_date_start,
              item.quote_date_end,
              item.quote_date_end_quality,
            )}
          </dd>
        </div>
      </dl>

      <section className="standard-evidence-section">
        <div className="section-heading">
          <div>
            <p className="section-kicker">원본 견적 근거</p>
            <h2>가격 근거</h2>
          </div>
          <span>{observationCount}건</span>
        </div>
        {evidencePending && (
          <LoadingLabel as="p" className="inline-state">근거를 불러오는 중…</LoadingLabel>
        )}
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
                  <th>견적 제출사</th>
                  <th>제품 제조사</th>
                  <th>단가</th>
                  <th>견적일</th>
                  <th>원본 위치</th>
                </tr>
              </thead>
              <tbody>
                {observations.map((row) => (
                  <tr key={row.raw_item_id}>
                    <td>{row.supplier_name ?? "원본에서 확인되지 않음"}</td>
                    <td>{row.maker ?? "원본에서 확인되지 않음"}</td>
                    <td className="numeric">{formatWon(row.unit_price)}</td>
                    <td>{formatQuoteDate(row.quote_date, row.quote_date_quality)}</td>
                    <td>
                       <a
                         href={`/api/documents/variants/${row.source.variant_id}/file`}
                         target="_blank"
                         rel="noreferrer"
                       >
                         원본 견적서 열기
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
            {evidenceLoadingMore ? (
              <LoadingLabel>불러오는 중…</LoadingLabel>
            ) : (
              "근거 더 보기"
            )}
          </button>
        )}
      </section>

      <section className="standard-history-section">
        <div className="section-heading">
          <div>
            <p className="section-kicker">업데이트 로그</p>
            <h2>업데이트 로그</h2>
          </div>
          <span>{versionGroups.length}건</span>
        </div>
        {historyPending && (
          <LoadingLabel as="p" className="inline-state">이력을 불러오는 중…</LoadingLabel>
        )}
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
        {!historyPending && !historyError && versionGroups.length === 0 && (
          <p className="inline-state">저장된 가격 버전이 없습니다.</p>
        )}
        <ol className="standard-version-ledger">
          {versionGroups.map((group) => {
            const version = group.versions.at(-1)!;
            const first = group.versions[0];
            return (
            <li key={version.id}>
              <div>
                <strong>
                  {group.versions.length === 1
                    ? `v${version.version_number}`
                    : `v${first.version_number}–v${version.version_number}`}
                </strong>
                <span>{formatDateTime(version.approved_at)}</span>
                {group.versions.length > 1 && (
                  <small>동일 단가 재계산 {group.versions.length}회</small>
                )}
              </div>
              <EvidenceBadge
                quality={version.evidence_quality}
                count={version.observation_count}
                supplierCount={version.supplier_count}
              />
              <dl>
                <div><dt>중앙값</dt><dd>{formatWon(version.prices.median)}</dd></div>
                <div><dt>범위</dt><dd>{formatWon(version.prices.minimum)} – {formatWon(version.prices.maximum)}</dd></div>
              </dl>
            </li>
          );})}
        </ol>
        {hasMoreHistory && (
          <button
            className="load-more-button"
            type="button"
            disabled={historyLoadingMore}
            onClick={loadMoreHistory}
          >
            {historyLoadingMore ? (
              <LoadingLabel>불러오는 중…</LoadingLabel>
            ) : (
              "가격 이력 더 보기"
            )}
          </button>
        )}
      </section>
    </div>
  );
}

function displaySpec(item: StandardItemSummary) {
  if (item.current_version.canonical_spec) {
    return item.current_version.canonical_spec;
  }
  switch (item.spec_source_status) {
    case "SOURCE_BLANK":
      return "원문에 규격 없음";
    case "PARSER_UNMAPPED":
      return "견적서에서 규격 위치 확인 필요";
    case "MIXED_REVIEW_REQUIRED":
      return "일부 원본의 규격 위치 확인 필요";
    case "MIXED_SOURCE_VALUES":
      return "일부 원문에 규격 없음";
    default:
      return "원본에서 확인되지 않음";
  }
}

function formatObservationCount(item: StandardItemSummary) {
  if (item.observation_count !== null) {
    const suppliers = item.supplier_count ?? 0;
    const supplierLabel = suppliers > 0
      ? `견적 제출사 ${suppliers.toLocaleString("ko-KR")}곳`
      : "견적 제출사 확인 필요";
    return `${item.observation_count.toLocaleString("ko-KR")}건 · ${supplierLabel}`;
  }
  return item.operational_status === "NO_ELIGIBLE_EVIDENCE"
    ? "근거 없음"
    : "재구축 필요";
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

function formatQuoteDate(
  value: string | null,
  quality?: "CONFIRMED" | "REFERENCE_BACKFILL" | "FILE_DATE_INFERRED" | null,
) {
  if (!value) return "원본에서 확인되지 않음";
  if (quality === "FILE_DATE_INFERRED") {
    return `${value.slice(0, 4)}년 (파일명 기준)`;
  }
  if (quality === "REFERENCE_BACKFILL") {
    return `${value} (기존 자료 보완)`;
  }
  return value;
}

function formatDateRange(
  start: string | null,
  end: string | null,
  endQuality?: "CONFIRMED" | "REFERENCE_BACKFILL" | "FILE_DATE_INFERRED" | null,
) {
  if (!start && !end) return "원본에서 확인되지 않음";
  if (start === end || !end) return formatQuoteDate(start ?? end, endQuality);
  return `${start} – ${formatQuoteDate(end, endQuality)}`;
}

function sourceLocation(source: {
  logical_name: string;
  path?: string;
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
  const name = conciseSourceName(source.logical_name, source.path);
  return `${name}${location.length ? ` · ${location.join(" · ")}` : ""}`;
}

function conciseSourceName(logicalName: string, sourcePath?: string) {
  const basename = (value: string) => value.replace(/\\/g, "/").split("/").filter(Boolean).at(-1) ?? value;
  const logicalBase = basename(logicalName);
  const pathBase = sourcePath ? basename(sourcePath) : logicalBase;
  const candidate = /\.[a-z0-9]{2,5}$/i.test(logicalBase) ? logicalBase : pathBase;
  const stem = candidate.replace(/\.[^.]+$/, "");
  const extension = candidate.match(/\.(?:xlsx?|pdf|jpe?g|png|zip|ecml)$/i)?.[0] ?? "";
  const looksGenerated =
    stem.length > 48 &&
    (/^[0-9]{14,}/.test(stem) || /[A-Za-z0-9+/=]{30,}/.test(stem));
  return looksGenerated ? `수집 원본 견적서${extension}` : candidate;
}

type PriceVersionGroup = { versions: PriceVersion[] };

function compactPriceVersions(versions: PriceVersion[]): PriceVersionGroup[] {
  const groups: PriceVersionGroup[] = [];
  for (const version of versions) {
    const latest = groups.at(-1);
    const previous = latest?.versions.at(-1);
    if (latest && previous && sameDisplayedPrice(previous, version)) {
      latest.versions.push(version);
    } else {
      groups.push({ versions: [version] });
    }
  }
  return groups;
}

function sameDisplayedPrice(left: PriceVersion, right: PriceVersion) {
  return (
    left.observation_count === right.observation_count &&
    left.evidence_quality === right.evidence_quality &&
    left.prices.minimum === right.prices.minimum &&
    left.prices.median === right.prices.median &&
    left.prices.average === right.prices.average &&
    left.prices.maximum === right.prices.maximum
  );
}

function uniqueById<T extends { id: number }>(items: T[]) {
  const seen = new Set<number>();
  return items.filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}

function positiveIntegerParam(name: string) {
  const value = Number(new URLSearchParams(window.location.search).get(name));
  return Number.isInteger(value) && value > 0 ? value : null;
}
