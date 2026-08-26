import { useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  getItemFamilies,
  getItemFamilyDetail,
  getStandardEvidence,
  getStandardItems,
  getStandardItemPriceTrend,
  getDashboardOverview,
  getStandardPriceVersion,
  getStandardPriceVersions,
  getSourceCoverageSummary,
  type EvidenceQuality,
  type ItemFamilyDetail,
  type ItemFamilySummary,
  type PriceVersion,
  type StandardItemSummary,
  type StandardItemPriceTrend,
} from "../api/client";
import { safeNextCursor, uniqueByRawItemId } from "../api/pagination";
import { EvidenceBadge } from "../components/EvidenceBadge";
import { LoadingLabel } from "../components/LoadingLabel";
import { MetricStrip } from "../components/MetricStrip";
import { reasonLabel } from "../components/reasonLabels";
import { Skeleton } from "../components/Skeleton";

const STANDARD_CATEGORIES = [
  { code: "DRIVE_MOTION", name: "구동·모션" },
  { code: "SENSOR_MEASUREMENT", name: "센서·계측" },
  { code: "ELECTRICAL_CONTROL", name: "전장·제어" },
  { code: "PNEUMATIC_HYDRAULIC", name: "공압·유압" },
  { code: "MATERIAL_HANDLING", name: "이송·물류" },
  { code: "MECHANICAL_FABRICATION", name: "기계·제작" },
  { code: "TOOLING_FIXTURE", name: "치공구·금형" },
  { code: "UTILITY_ENVIRONMENT", name: "유틸리티·환경" },
  { code: "CABLE_CONNECTOR", name: "케이블·커넥터" },
  { code: "FASTENER_CONSUMABLE", name: "체결·소모품" },
  { code: "SAFETY", name: "안전·보호" },
  { code: "IT_NETWORK", name: "IT·네트워크" },
  { code: "LABOR_SERVICE", name: "노무·설치" },
  { code: "GENERAL_COMPONENT", name: "공통 설비·부품" },
] as const;

export function StandardPricesPage() {
  const [viewMode, setViewMode] = useState<"families" | "items">(
    () => stringParam("view") === "items" || positiveIntegerParam("item_id") ? "items" : "families",
  );
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [quality, setQuality] = useState<EvidenceQuality | "">("");
  const [category, setCategory] = useState(stringParam("category"));
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [requestedItemId, setRequestedItemId] = useState<number | null>(
    positiveIntegerParam("item_id"),
  );
  const [requestedVersionId, setRequestedVersionId] = useState<number | null>(
    positiveIntegerParam("version_id"),
  );
  const [modalPhase, setModalPhase] = useState<"closed" | "open" | "closing">(
    () => positiveIntegerParam("item_id") ? "open" : "closed",
  );
  const [selectedFamilyCode, setSelectedFamilyCode] = useState<string | null>(null);
  const attemptedCatalogCursors = useRef(new Set<number>());
  const closeTimerRef = useRef<number | null>(null);
  const modalRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    document.title = "표준 DB · 통합 견적 분석 시스템";
    return () => {
      document.title = "통합 견적 분석 시스템";
    };
  }, []);

  const catalog = useInfiniteQuery({
    queryKey: ["standard-db", search, quality, category],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      getStandardItems({
        afterId: pageParam,
        search: search || undefined,
        evidenceQuality: quality || undefined,
        category: category || undefined,
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
    (requestedItemId === null && selectedId !== null
      ? items.find((item) => item.id === selectedId) ?? null
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

  const requestedItemExhausted =
    requestedItemId !== null &&
    !requestedItem &&
    !hasNextCatalogPage &&
    !isFetchingNextCatalogPage &&
    !isFetchingCatalog;
  const requestedItemFetchFailed =
    requestedItemId !== null && !requestedItem && isFetchNextCatalogPageError;

  useEffect(() => {
    if (modalPhase === "closed" && selectedFamilyCode === null) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    if (modalPhase === "open") {
      window.requestAnimationFrame(() => modalRef.current?.focus());
    }
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [modalPhase, selectedFamilyCode]);

  useEffect(() => () => {
    if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
  }, []);

  const openDetail = (itemId: number) => {
    if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
    returnFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    setRequestedItemId(null);
    setRequestedVersionId(null);
    setSelectedId(itemId);
    setModalPhase("open");
  };

  const closeDetail = () => {
    if (!selected || modalPhase === "closing") return;
    if (selectedId === null) setSelectedId(selected.id);
    setRequestedItemId(null);
    setRequestedVersionId(null);
    setModalPhase("closing");
    closeTimerRef.current = window.setTimeout(() => {
      setModalPhase("closed");
      setSelectedId(null);
      returnFocusRef.current?.focus();
      closeTimerRef.current = null;
    }, 150);
  };

  const handleModalKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeDetail();
      return;
    }
    if (event.key !== "Tab" || !modalRef.current) return;
    const focusable = Array.from(
      modalRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    );
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable.at(-1)!;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

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
  const trend = useQuery({
    queryKey: ["standard-item-price-trend", selected?.id],
    queryFn: ({ signal }) => getStandardItemPriceTrend(selected!.id, signal),
    enabled: selected !== null,
    retry: false,
  });
  const families = useQuery({
    queryKey: ["standard-db-families", search, category],
    queryFn: ({ signal }) => getItemFamilies({
      search: search || undefined,
      category: category || undefined,
      signal,
    }),
    enabled: viewMode === "families",
    retry: false,
  });
  const familyDetail = useQuery({
    queryKey: ["standard-db-family", selectedFamilyCode],
    queryFn: ({ signal }) => getItemFamilyDetail(selectedFamilyCode!, signal),
    enabled: selectedFamilyCode !== null,
    retry: false,
  });
  const dashboard = useQuery({
    queryKey: ["dashboard-overview"],
    queryFn: ({ signal }) => getDashboardOverview(signal),
    retry: false,
  });
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
  if (category) exportParams.set("category", category);
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

      <nav className="standard-category-rail" aria-label="표준 DB 품목 카테고리">
        <button
          type="button"
          aria-current={!category ? "page" : undefined}
          onClick={() => {
            setCategory("");
            setSelectedId(null);
          }}
        >
          <span>00</span><strong>전체 품목</strong>
        </button>
        {STANDARD_CATEGORIES.map((item, index) => (
          <button
            type="button"
            key={item.code}
            aria-current={category === item.code ? "page" : undefined}
            onClick={() => {
              setCategory(item.code);
              setSelectedId(null);
              setRequestedItemId(null);
              setRequestedVersionId(null);
            }}
          >
            <span>{String(index + 1).padStart(2, "0")}</span><strong>{item.name}</strong>
          </button>
        ))}
      </nav>

      <div className="standard-view-switch" role="group" aria-label="표준 DB 보기 방식">
        <button
          type="button"
          aria-pressed={viewMode === "families"}
          onClick={() => setViewMode("families")}
        >
          품목류로 보기
          <small>유사 품목을 묶은 가격 흐름</small>
        </button>
        <button
          type="button"
          aria-pressed={viewMode === "items"}
          onClick={() => setViewMode("items")}
        >
          정확 품목으로 보기
          <small>품명·규격·단위가 같은 원본 근거</small>
        </button>
      </div>

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
        {viewMode === "items" && <label className="standard-filter-control">
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
        </label>}
        <button type="submit" className="standard-search-submit">
          <svg aria-hidden="true" viewBox="0 0 24 24">
            <circle cx="11" cy="11" r="6" />
            <path d="m16 16 4 4" />
          </svg>
          <span>검색</span>
        </button>
      </form>

      {viewMode === "families" && (
        <FamilyCatalog
          data={families.data?.families ?? []}
          pending={families.isPending}
          error={families.isError}
          retry={() => void families.refetch()}
          onSelect={setSelectedFamilyCode}
          totalItems={families.data?.item_count ?? 0}
        />
      )}

      {viewMode === "items" && <div className="standard-db-catalog">
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
            <StandardCatalogSkeleton />
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
                      selected={selected?.id === item.id && modalPhase !== "closed"}
                      key={item.id}
                      onSelect={() => openDetail(item.id)}
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

      </div>}
      {selected && modalPhase !== "closed" && (
        <div
          className={`standard-detail-overlay ${modalPhase === "open" ? "is-open" : "is-closing"}`}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeDetail();
          }}
        >
          <div
            className={`standard-detail-modal t-modal ${modalPhase === "open" ? "is-open" : "is-closing"}`}
            id="standard-item-detail-modal"
            ref={modalRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="standard-item-detail-title"
            tabIndex={-1}
            onKeyDown={handleModalKeyDown}
          >
            <header className="standard-detail-modal-bar">
              <div><span>PRICE RECORD</span><strong>표준 품목 상세</strong></div>
              <button type="button" onClick={closeDetail} aria-label="표준 품목 상세 닫기">
                <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18" /></svg>
                닫기
              </button>
            </header>
            <div className="standard-detail-modal-scroll">
              <StandardItemDetail
                item={selected}
                headingId="standard-item-detail-title"
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
                trend={trend.data ?? null}
                trendPending={trend.isPending}
                trendError={trend.isError}
              />
            </div>
          </div>
        </div>
      )}
      {selectedFamilyCode && (
        <div
          className="standard-detail-overlay is-open"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setSelectedFamilyCode(null);
          }}
        >
          <div
            className="standard-detail-modal family-detail-modal t-modal is-open"
            role="dialog"
            aria-modal="true"
            aria-labelledby="family-detail-title"
            tabIndex={-1}
            onKeyDown={(event) => {
              if (event.key === "Escape") setSelectedFamilyCode(null);
            }}
          >
            <header className="standard-detail-modal-bar">
              <div><span>ITEM FAMILY</span><strong>품목류 상세</strong></div>
              <button type="button" onClick={() => setSelectedFamilyCode(null)} aria-label="품목류 상세 닫기">
                <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18" /></svg>
                닫기
              </button>
            </header>
            <div className="standard-detail-modal-scroll">
              {familyDetail.isPending && <FamilyDetailSkeleton />}
              {familyDetail.isError && (
                <div className="inline-state is-error">
                  <p>품목류를 불러오지 못했습니다.</p>
                  <button type="button" onClick={() => void familyDetail.refetch()}>다시 시도</button>
                </div>
              )}
              {familyDetail.data && <FamilyDetail family={familyDetail.data} />}
            </div>
          </div>
        </div>
      )}
      <section className="standard-unclassified-band" aria-label="표준 DB 미분류 작업 목록">
        <div>
          <span>STANDARD DB / UNCLASSIFIED QUEUE</span>
          <h2>
            정제 검토 대기
            <strong>{dashboard.data ? ` ${dashboard.data.cleansing_todo.count.toLocaleString("ko-KR")}건` : " 집계 중"}</strong>
          </h2>
          <p>아직 표준 가격에 반영되지 않은 원문 품목입니다. 검토를 마치면 기존 이력을 보존한 채 표준 DB 근거로 연결됩니다.</p>
        </div>
        <div className="standard-unclassified-reasons">
          {dashboard.data?.cleansing_todo.top_reasons.slice(0, 4).map((reason) => (
            <span key={reason.reason_code}>{reasonLabel(reason.reason_code)} <b>{reason.count.toLocaleString("ko-KR")}</b></span>
          ))}
        </div>
        <a href="/cleansing">미분류 품목 검토</a>
      </section>
    </main>
  );
}

function FamilyCatalog({
  data,
  pending,
  error,
  retry,
  onSelect,
  totalItems,
}: {
  data: ItemFamilySummary[];
  pending: boolean;
  error: boolean;
  retry: () => void;
  onSelect: (code: string) => void;
  totalItems: number;
}) {
  return (
    <section className="standard-family-panel" aria-label="품목류 목록">
      <header>
        <div>
          <strong>품목류 가격 기준</strong>
          <small>전 품목을 용도와 명칭 기준으로 묶고 정확 품명·규격은 하위 근거로 보존합니다.</small>
        </div>
        <span>{pending ? "묶는 중…" : `${data.length.toLocaleString("ko-KR")}개 품목류 · ${totalItems.toLocaleString("ko-KR")}개 품목`}</span>
      </header>
      {pending && <FamilyDetailSkeleton />}
      {error && <div className="inline-state is-error"><p>품목류를 불러오지 못했습니다.</p><button type="button" onClick={retry}>다시 시도</button></div>}
      {!pending && !error && data.length === 0 && <p className="inline-state">검색 결과가 없습니다.</p>}
      {data.length > 0 && (
        <div className="table-scroll standard-family-scroll">
          <table className="data-table standard-family-table">
            <thead>
              <tr>
                <th>품목류</th>
                <th className="numeric">정확 품목</th>
                <th className="numeric">가격 근거</th>
                <th className="numeric">관측 연도</th>
                <th className="numeric">최저</th>
                <th className="numeric">중앙값</th>
                <th className="numeric">평균</th>
                <th className="numeric">최고</th>
              </tr>
            </thead>
            <tbody>
              {data.map((family) => (
                <tr key={family.code}>
                  <td>
                    <button type="button" className="family-name-button" onClick={() => onSelect(family.code)}>
                      <strong>{family.name}</strong>
                      <small>{family.category_names.join(" · ") || "공통 설비·부품"} · 상세 보기</small>
                    </button>
                  </td>
                  <td className="numeric">{family.item_count.toLocaleString("ko-KR")}개</td>
                  <td className="numeric">{family.observation_count.toLocaleString("ko-KR")}건</td>
                  <td className="numeric">{family.year_count ? `${family.year_count}개년` : "날짜 확인 필요"}</td>
                  <td className="numeric">{formatWon(family.price?.minimum ?? null)}</td>
                  <td className="numeric is-emphasis">{formatWon(family.price?.median ?? null)}</td>
                  <td className="numeric">{formatWon(family.price?.average ?? null)}</td>
                  <td className="numeric">{formatWon(family.price?.maximum ?? null)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function FamilyDetail({ family }: { family: ItemFamilyDetail }) {
  return (
    <article className="family-detail">
      <header className="family-detail-heading">
        <div>
          <p className="section-kicker">품목류 가격 분석</p>
          <h2 id="family-detail-title">{family.name}</h2>
          <p>{family.item_count.toLocaleString("ko-KR")}개 정확 품목 · {family.observation_count.toLocaleString("ko-KR")}건 가격 근거 · {family.supplier_count.toLocaleString("ko-KR")}개 견적 제출사</p>
        </div>
        <span>{family.year_count ? `${family.year_count}개년 추이` : "견적일 확인 필요"}</span>
      </header>
      <dl className="family-price-strip">
        <div><dt>최저</dt><dd>{formatWon(family.price?.minimum ?? null)}</dd></div>
        <div><dt>중앙값</dt><dd>{formatWon(family.price?.median ?? null)}</dd></div>
        <div><dt>평균</dt><dd>{formatWon(family.price?.average ?? null)}</dd></div>
        <div><dt>최고</dt><dd>{formatWon(family.price?.maximum ?? null)}</dd></div>
      </dl>
      <section className="family-trend-section" aria-labelledby="family-trend-title">
        <div className="section-title">
          <p className="section-kicker">연도별 가격 변화</p>
          <h3 id="family-trend-title">연도별 중앙값</h3>
        </div>
        {family.trend.length > 0 ? (
          <div className="family-trend-chart" aria-label={`${family.name} 연도별 가격 변화`}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={family.trend} margin={{ top: 16, right: 18, bottom: 0, left: 8 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="year" tickLine={false} axisLine={false} />
                <YAxis hide domain={["auto", "auto"]} />
                <Tooltip formatter={(value) => formatWon(String(value))} labelFormatter={(label) => `${label}년`} />
                <Area type="monotone" dataKey="median" name="중앙값" stroke="var(--accent)" fill="var(--accent-soft)" strokeWidth={2.5} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ) : <p className="inline-state">확정 견적일이 없어 연도별 추이를 표시할 수 없습니다.</p>}
      </section>
      <section className="family-members" aria-labelledby="family-members-title">
        <div className="section-title">
          <p className="section-kicker">하위 가격 근거</p>
          <h3 id="family-members-title">정확 품명·규격</h3>
        </div>
        <div className="table-scroll">
          <table className="data-table">
            <thead><tr><th>품명</th><th>규격</th><th>단위</th><th className="numeric">근거</th><th className="numeric">중앙값</th></tr></thead>
            <tbody>
              {family.members.map((member) => (
                <tr key={member.standard_item_id}>
                  <td><strong>{member.name}</strong></td>
                  <td>{member.spec || "원문에 규격 없음"}</td>
                  <td>{member.unit || "—"}</td>
                  <td className="numeric">{member.observation_count.toLocaleString("ko-KR")}건</td>
                  <td className="numeric is-emphasis">{formatWon(member.price.median)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </article>
  );
}

function FamilyDetailSkeleton() {
  return <div className="family-skeleton" role="status" aria-label="품목류를 불러오는 중"><Skeleton height="58px" /><Skeleton height="190px" /><Skeleton height="110px" /></div>;
}

function StandardItemTableRow({
  item,
  selected,
  onSelect,
}: {
  item: StandardItemSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const price = item.current_price;
  return (
    <tr className={selected ? "is-selected" : undefined}>
      <td>
        <button
          type="button"
          className="standard-item-name-button"
          aria-expanded={selected}
          aria-haspopup="dialog"
          aria-controls="standard-item-detail-modal"
          onClick={onSelect}
        >
          <strong>{item.current_version.canonical_name}</strong>
          <small>
            {item.category_name ?? "공통 설비·부품"}
            {item.category_confidence !== null && item.category_confidence !== undefined && Number(item.category_confidence) < 50 ? " · 추정 분류" : ""}
            {` · 품목 #${item.id}`}
          </small>
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

function StandardCatalogSkeleton() {
  const widths = ["82%", "70%", "42%", "64%", "68%", "64%", "64%", "38%", "70%", "70%", "58%"];
  return (
    <div className="table-scroll standard-catalog-scroll standard-catalog-skeleton" role="status" aria-label="표준 품목 목록을 불러오는 중" aria-busy="true">
      <span className="sr-only">표준 품목 목록을 불러오는 중입니다.</span>
      <table className="data-table standard-catalog-table" aria-hidden="true">
        <thead>
          <tr>
            <th>품명</th><th>규격</th><th>단위</th><th>최저</th><th>중앙값</th><th>평균</th><th>최고</th><th>근거</th><th>제품 제조사</th><th>견적 제출사</th><th>최근 견적일</th>
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: 8 }, (_, rowIndex) => (
            <tr key={rowIndex}>
              {widths.map((width, cellIndex) => (
                <td key={cellIndex}>
                  <Skeleton width={width} height={cellIndex === 0 ? "13px" : "10px"} />
                  {cellIndex === 0 ? <Skeleton className="skeleton-subline" width="55%" height="8px" /> : null}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StandardItemDetail({
  item,
  headingId,
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
  trend,
  trendPending,
  trendError,
}: {
  item: StandardItemSummary;
  headingId?: string;
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
  trend: StandardItemPriceTrend | null;
  trendPending: boolean;
  trendError: boolean;
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
          <h2 id={headingId}>{item.current_version.canonical_name}</h2>
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
      <PriceTrendPanel trend={trend} pending={trendPending} error={trendError} />
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

function PriceTrendPanel({
  trend,
  pending,
  error,
}: {
  trend: StandardItemPriceTrend | null;
  pending: boolean;
  error: boolean;
}) {
  return (
    <section className="standard-price-trend" aria-label="연도별 가격 추세">
      <header>
        <div><p className="section-kicker">PRICE HISTORY</p><h3>연도별 가격 증감 추세</h3></div>
        {trend ? <span>견적일 미확정 {trend.undated_observation_count}건</span> : null}
      </header>
      {pending ? <LoadingLabel as="p" className="inline-state">가격 추세를 계산하는 중…</LoadingLabel> : null}
      {error ? <p className="inline-state">가격 추세를 불러오지 못했습니다.</p> : null}
      {!pending && !error && trend?.points.length === 0 ? (
        <p className="inline-state">확정 견적일이 있는 가격 근거가 없습니다.</p>
      ) : null}
      {trend && trend.points.length > 0 ? (
        <div className="standard-price-trend-chart">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={trend.points} margin={{ top: 12, right: 8, bottom: 0, left: 2 }}>
              <defs>
                <linearGradient id="standardTrendFill" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0" stopColor="#00287a" stopOpacity={0.22} />
                  <stop offset="1" stopColor="#00287a" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid vertical={false} stroke="var(--soft-line)" />
              <XAxis dataKey="year" tickLine={false} axisLine={false} tick={{ fill: "var(--muted)", fontSize: 10 }} />
              <YAxis hide domain={["dataMin", "dataMax"]} />
              <Tooltip content={<TrendTooltip />} />
              <Area type="monotone" dataKey="maximum" stroke="var(--line)" fill="transparent" strokeDasharray="3 4" animationDuration={800} animationEasing="ease-out" />
              <Area type="monotone" dataKey="minimum" stroke="var(--muted)" fill="transparent" strokeDasharray="3 4" animationBegin={80} animationDuration={800} animationEasing="ease-out" />
              <Area type="monotone" dataKey="median" stroke="#00287a" strokeWidth={2.5} fill="url(#standardTrendFill)" animationBegin={150} animationDuration={1050} animationEasing="ease-out" />
            </AreaChart>
          </ResponsiveContainer>
          <span className="standard-trend-readhead" aria-hidden="true" />
        </div>
      ) : null}
      {trend ? <small>{trend.note}</small> : null}
    </section>
  );
}

function TrendTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ payload: StandardItemPriceTrend["points"][number] }>; label?: string }) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div className="dashboard-tooltip">
      <span>{label}년 · 근거 {point.observation_count}건</span>
      <strong>{formatWon(point.median)}</strong>
      <small>{formatWon(point.minimum)} – {formatWon(point.maximum)}</small>
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

function stringParam(name: string) {
  return new URLSearchParams(window.location.search).get(name)?.trim() ?? "";
}
