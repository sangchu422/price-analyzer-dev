import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  Database,
  Gauge,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { memo, useEffect, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { getDashboardOverview, syncProcurementIndicators, type DashboardOverview } from "../api/client";
import { AnimatedNumber } from "../components/AnimatedNumber";
import { reasonLabel } from "../components/reasonLabels";
import { Skeleton } from "../components/Skeleton";
import { TextBlockAnimation } from "../components/TextBlockAnimation";
import { WiaInteractiveMark } from "../components/WiaInteractiveMark";

const DASHBOARD_SNAPSHOT_KEY = "price-analyzer-dashboard-snapshot-v1";
const FAMILY_PAGE_SIZE = 6;
const FAMILY_ROTATION_MS = 5_200;

function loadDashboardSnapshot(): DashboardOverview | undefined {
  try {
    const stored = window.localStorage.getItem(DASHBOARD_SNAPSHOT_KEY);
    if (!stored) return undefined;
    const parsed = JSON.parse(stored) as DashboardOverview;
    return parsed?.catalog && Array.isArray(parsed.families) ? parsed : undefined;
  } catch {
    return undefined;
  }
}

export function DashboardPage({ onNavigate }: { onNavigate: (path: string) => void }) {
  const [selectedIndicatorCode, setSelectedIndicatorCode] = useState<string | null>(null);
  const [familiesExpanded, setFamiliesExpanded] = useState(false);
  const [familyPage, setFamilyPage] = useState(0);
  const [familyCarouselPaused, setFamilyCarouselPaused] = useState(false);
  const [performanceYear, setPerformanceYear] = useState(2026);
  const indicatorSyncAttempted = useRef(false);
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["dashboard-overview"],
    queryFn: ({ signal }) => getDashboardOverview(signal),
    placeholderData: loadDashboardSnapshot(),
    refetchInterval: 60_000,
  });

  useEffect(() => {
    document.title = "종합현황 · 통합 견적 분석 시스템";
    return () => {
      document.title = "통합 견적 분석 시스템";
    };
  }, []);

  useEffect(() => {
    if (!query.data || query.isPlaceholderData) return;
    try {
      window.localStorage.setItem(DASHBOARD_SNAPSHOT_KEY, JSON.stringify(query.data));
    } catch {
      // The live query still works when browser storage is unavailable.
    }
  }, [query.data, query.isPlaceholderData]);

  useEffect(() => {
    if (
      indicatorSyncAttempted.current
      || !query.data
      || !query.data.indicators.some((indicator) => indicator.source_status === "UNAVAILABLE" || indicator.source_status === "STALE")
    ) return;
    indicatorSyncAttempted.current = true;
    const controller = new AbortController();
    void syncProcurementIndicators(controller.signal)
      .then(() => queryClient.invalidateQueries({ queryKey: ["dashboard-overview"] }))
      .catch(() => undefined);
    return () => controller.abort();
  }, [query.data, queryClient]);

  const familyPageCount = Math.max(1, Math.ceil((query.data?.families.length ?? 0) / FAMILY_PAGE_SIZE));

  useEffect(() => {
    if (familiesExpanded || familyCarouselPaused || familyPageCount <= 1) return;
    const rotation = window.setInterval(() => {
      setFamilyPage((current) => (current + 1) % familyPageCount);
    }, FAMILY_ROTATION_MS);
    return () => window.clearInterval(rotation);
  }, [familiesExpanded, familyCarouselPaused, familyPageCount]);

  if (query.isLoading) {
    return (
      <main className="dashboard-page dashboard-skeleton" role="status" aria-busy="true">
        <div className="dashboard-grid-field" aria-hidden="true" />
        <header className="dashboard-hero">
          <h1 className="sr-only">HYUNDAI WIA 구매 종합현황</h1>
          <DashboardHeroBrand />
          <div className="dashboard-hero-status">
            <span>DATA CONNECTING</span>
            <Skeleton width="116px" height="42px" />
            <small>전체 견적 품목을 집계하고 있습니다.</small>
          </div>
        </header>
        <section className="dashboard-skeleton-grid">
          <div className="dashboard-left-rail">
            <article className="dashboard-skeleton-panel is-primary">
              <header><span>STANDARD DB / LIVE</span><strong>표준 DB 구축 현황</strong></header>
              <div className="dashboard-skeleton-catalog"><Skeleton className="dashboard-skeleton-ring" /><div><Skeleton /><Skeleton /><Skeleton /><Skeleton /></div></div>
            </article>
            <article className="dashboard-skeleton-panel is-wide">
              <header><span>ITEM DISTRIBUTION</span><strong>품목</strong></header>
              <div className="dashboard-skeleton-list">{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} />)}</div>
            </article>
          </div>
          <aside className="dashboard-right-rail">
            <article className="dashboard-skeleton-panel">
              <header><span>ACTUAL / FORECAST</span><strong>월별 품의 현황</strong></header>
              <div className="dashboard-skeleton-bars" aria-hidden="true">{[48, 66, 78, 58, 72, 88, 82, 54, 39, 45, 52, 60].map((height, index) => <i key={index} style={{ height: `${height}%` }} />)}</div>
            </article>
            <article className="dashboard-skeleton-panel is-market">
              <header><span>MARKET SIGNAL</span><strong>구매 참고 지표</strong></header>
              <div className="dashboard-skeleton-list is-compact">{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} />)}</div>
            </article>
          </aside>
        </section>
      </main>
    );
  }
  if (query.isError || !query.data) {
    return (
      <main className="dashboard-page dashboard-error" role="alert">
        <strong>종합현황을 불러오지 못했습니다.</strong>
        <button type="button" onClick={() => void query.refetch()}>다시 연결</button>
      </main>
    );
  }

  const data = query.data;
  const historicalQuoteCount = data.catalog.historical_quote_document_count ?? 0;
  const eligibleItemCount = data.catalog.eligible_item_count ?? data.catalog.total_standard_items;
  const standardizedItemCount = data.catalog.standardized_item_count ?? data.catalog.active_price_items;
  const unstandardizedItemCount = data.catalog.unstandardized_item_count ?? Math.max(eligibleItemCount - standardizedItemCount, 0);
  const completion = data.catalog.standardization_percent === undefined
    ? (eligibleItemCount === 0 ? 0 : standardizedItemCount / eligibleItemCount * 100)
    : Number(data.catalog.standardization_percent);
  const selectedIndicator = data.indicators.find((item) => item.code === selectedIndicatorCode) ?? null;
  const resolvedFamilyPage = familyPage % familyPageCount;
  const familyPageStart = resolvedFamilyPage * FAMILY_PAGE_SIZE;
  const visibleFamilyPage = data.families.slice(familyPageStart, familyPageStart + FAMILY_PAGE_SIZE);
  const monthlyPerformance = data.monthly_performance as DashboardOverview["monthly_performance"] & {
    year?: number;
    series?: Array<{ month: number; label: string; count: number; kind: "ACTUAL" | "FORECAST" }>;
  };
  const availableYears = monthlyPerformance.available_years ?? [monthlyPerformance.year ?? 2026];
  const legacySeries = (monthlyPerformance.series ?? []).map((entry) => ({
    ...entry,
    equipment_purchase: 0,
    integrated_purchase: entry.count,
    total: entry.count,
  }));
  const performanceSeries = monthlyPerformance.series_by_year?.[String(performanceYear)] ?? legacySeries;

  return (
    <main className="dashboard-page">
      <div className="dashboard-grid-field" aria-hidden="true" />
      <header className="dashboard-hero">
        <h1 className="sr-only">HYUNDAI WIA 구매 종합현황</h1>
        <DashboardHeroBrand />
        <div className="dashboard-hero-status">
          <span>{query.isFetching ? "DATA REFRESHING" : "DATA PULSE"}</span>
          <AnimatedNumber value={eligibleItemCount} className="dashboard-pulse-number" />
          <small>전체 견적 품목</small>
          <time>{data.as_of} 기준</time>
        </div>
      </header>

      <section className="dashboard-command-grid">
        <div className="dashboard-left-rail">
          <section className="catalog-command" aria-labelledby="catalog-command-title">
            <header>
              <div>
                <span>STANDARD DB / LIVE</span>
                <h2 id="catalog-command-title">표준 DB 구축 현황</h2>
              </div>
              <Database aria-hidden="true" />
            </header>
            <div className="catalog-radar">
              <div className="catalog-radar-number">
                <AnimatedNumber value={completion} decimals={1} suffix="%" className="catalog-completion-number" />
                <span>품목 표준화율</span>
              </div>
              <svg viewBox="0 0 220 220" aria-hidden="true">
                <circle cx="110" cy="110" r="91" />
                <circle
                  className="is-progress"
                  cx="110"
                  cy="110"
                  r="91"
                  pathLength="100"
                  strokeDasharray={`${completion} ${100 - completion}`}
                />
                <circle cx="110" cy="110" r="70" />
                <path d="M110 10V210M10 110H210M39 39L181 181M181 39L39 181" />
              </svg>
            </div>
            <div className="catalog-funnel" aria-label="표준 DB 단계별 현황">
              <div className="catalog-funnel-heading">
                <strong>표준 DB 단계별 현황</strong>
                <span>수집부터 활용까지</span>
              </div>
              <FunnelLine label="과거 견적서" value={historicalQuoteCount} max={historicalQuoteCount} unit="건" meta="수집 원본" delay={0} />
              <FunnelLine label="전체 견적 품목" value={eligibleItemCount} max={eligibleItemCount} unit="개" meta="분석 대상" delay={0.08} />
              <FunnelLine label="표준 DB 연결" value={standardizedItemCount} max={eligibleItemCount} unit="개" meta={`${completion.toFixed(1)}%`} accent delay={0.16} />
              <FunnelLine label="표준화 대기" value={unstandardizedItemCount} max={eligibleItemCount} unit="개" meta={`${Math.max(0, 100 - completion).toFixed(1)}%`} warning delay={0.24} />
            </div>
            <button className="dashboard-text-action" type="button" onClick={() => onNavigate("/standard-prices")}>
              표준 DB 탐색 <ArrowRight aria-hidden="true" size={16} />
            </button>
          </section>

          <section className="category-spectrum" aria-labelledby="category-spectrum-title">
            <header>
              <div>
                <span>ITEM FAMILY DISTRIBUTION</span>
                <h2 id="category-spectrum-title">품목</h2>
              </div>
              <small>표준 DB를 구매 관점의 {data.families.length.toLocaleString("ko-KR")}개 품목 분류로 나눴습니다.</small>
            </header>
            {familiesExpanded ? (
              <motion.div className="category-spectrum-list" layout>
                {data.families.map((family, index) => (
                  <motion.button
                    key={family.code}
                    type="button"
                    initial={{ opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    layout
                    transition={{ delay: Math.min(index * 0.025, 0.28) }}
                    onClick={() => onNavigate(`/standard-prices?family=${encodeURIComponent(family.code)}`)}
                  >
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <strong>{family.display_name ?? displayFamilyName(family.name)}</strong>
                    <AnimatedNumber value={family.item_count} suffix="종" className="category-count" />
                    <i style={{ "--share": `${Math.max(Number(family.share_percent), 1)}%` } as React.CSSProperties} />
                  </motion.button>
                ))}
              </motion.div>
            ) : (
              <div
                className="family-share-carousel"
                onMouseEnter={() => setFamilyCarouselPaused(true)}
                onMouseLeave={() => setFamilyCarouselPaused(false)}
                onFocusCapture={() => setFamilyCarouselPaused(true)}
                onBlurCapture={() => setFamilyCarouselPaused(false)}
              >
                <div className="family-share-viewport" aria-live="polite">
                  <AnimatePresence mode="wait" initial={false}>
                    <motion.div
                      className="family-share-page"
                      key={resolvedFamilyPage}
                      initial={{ opacity: 0, x: 34, filter: "blur(3px)" }}
                      animate={{ opacity: 1, x: 0, filter: "blur(0px)" }}
                      exit={{ opacity: 0, x: -28, filter: "blur(2px)" }}
                      transition={{ duration: 0.38, ease: [0.22, 1, 0.36, 1] }}
                    >
                      {visibleFamilyPage.map((family, index) => {
                        const share = Math.max(0, Number(family.share_percent));
                        return (
                          <button
                            key={family.code}
                            type="button"
                            className="family-share-item"
                            aria-label={`${family.display_name ?? displayFamilyName(family.name)} ${family.item_count.toLocaleString("ko-KR")}종, 전체의 ${share.toFixed(1)}%`}
                            onClick={() => onNavigate(`/standard-prices?family=${encodeURIComponent(family.code)}`)}
                          >
                            <span className="family-share-rank">{String(familyPageStart + index + 1).padStart(2, "0")}</span>
                            <span className="family-share-copy">
                              <strong>{family.display_name ?? displayFamilyName(family.name)}</strong>
                              <small>{family.item_count.toLocaleString("ko-KR")}종</small>
                            </span>
                            <span className="family-share-chart" style={{ "--family-share": `${Math.min(share, 100)}` } as React.CSSProperties} aria-hidden="true">
                              <svg viewBox="0 0 42 42">
                                <circle cx="21" cy="21" r="16" />
                                <motion.circle
                                  className="is-share"
                                  cx="21"
                                  cy="21"
                                  r="16"
                                  pathLength="100"
                                  initial={{ pathLength: 0 }}
                                  animate={{ pathLength: Math.min(share, 100) / 100 }}
                                  transition={{ delay: index * 0.045, duration: 0.72, ease: [0.22, 1, 0.36, 1] }}
                                />
                              </svg>
                              <b>{share.toFixed(1)}%</b>
                            </span>
                          </button>
                        );
                      })}
                    </motion.div>
                  </AnimatePresence>
                </div>
                {familyPageCount > 1 ? (
                  <div className="family-share-controls">
                    <span>{resolvedFamilyPage + 1} / {familyPageCount}</span>
                    <div className="family-share-progress" aria-hidden="true"><i key={resolvedFamilyPage} /></div>
                    <button type="button" aria-label="이전 품목 구성비" onClick={() => setFamilyPage((current) => (current - 1 + familyPageCount) % familyPageCount)}><ChevronLeft size={15} /></button>
                    <button type="button" aria-label="다음 품목 구성비" onClick={() => setFamilyPage((current) => (current + 1) % familyPageCount)}><ChevronRight size={15} /></button>
                  </div>
                ) : null}
              </div>
            )}
            {data.families.length > 12 ? (
              <button
                type="button"
                className="category-spectrum-toggle"
                aria-expanded={familiesExpanded}
                onClick={() => setFamiliesExpanded((current) => !current)}
              >
                {familiesExpanded ? "품목 분류 접기" : `전체 ${data.families.length.toLocaleString("ko-KR")}개 품목 분류 펼치기`}
                <ArrowRight aria-hidden="true" size={14} />
              </button>
            ) : null}
          </section>

          <section className="todo-command">
            <div className="todo-signal"><ShieldAlert aria-hidden="true" /></div>
            <div>
              <span>STANDARD DB / TO-DO</span>
              <strong>미분류·검토 대기 <AnimatedNumber value={data.cleansing_todo.count} suffix="건" /></strong>
              <p>정제 검토 항목도 표준 DB 작업 흐름 안에서 이어서 처리합니다.</p>
            </div>
            <div className="todo-reasons">
              {data.cleansing_todo.top_reasons.slice(0, 3).map((reason) => (
                <span key={reason.reason_code}>{reasonLabel(reason.reason_code)} <b>{reason.count.toLocaleString("ko-KR")}</b></span>
              ))}
            </div>
            <button type="button" onClick={() => onNavigate("/cleansing")}>검토 시작 <ArrowRight size={15} /></button>
          </section>
        </div>

        <aside className="dashboard-right-rail">
          <section className="performance-command" aria-labelledby="performance-title">
            <header>
              <div>
                <span>ACTUAL / FORECAST</span>
                <h2 id="performance-title">월별 품의 현황</h2>
              </div>
              <div className="performance-year-switch" aria-label="현황 연도">
                {availableYears.map((year) => (
                  <button type="button" key={year} aria-pressed={performanceYear === year} onClick={() => setPerformanceYear(year)}>{year}</button>
                ))}
              </div>
            </header>
            <MonthlyPerformanceChart series={performanceSeries} />
            <footer>
              <span><i className="is-actual" /> 실적</span>
              <span><i className="is-forecast" /> 예상</span>
              <span><i className="is-equipment" /> 설비구매</span>
              <span><i className="is-integrated" /> 통합구매</span>
              <small>{data.monthly_performance.forecast_method}</small>
            </footer>
          </section>

          {data.indicators.length > 0 ? (
            <section className="market-command" aria-labelledby="market-command-title">
              <header>
                <div>
                  <span>MARKET SIGNAL / AT A GLANCE</span>
                  <h2 id="market-command-title">구매 참고 지표</h2>
                </div>
                <small>원자재·환율·임율·시황을 동시에 비교합니다.</small>
              </header>
              <div className="market-signal-grid">
                {data.indicators.map((indicator, index) => (
                  <MarketSignalTile
                    indicator={indicator}
                    index={index}
                    key={indicator.code}
                    selected={selectedIndicatorCode === indicator.code}
                    onSelect={() => setSelectedIndicatorCode((current) => current === indicator.code ? null : indicator.code)}
                  />
                ))}
              </div>
              {selectedIndicator ? (
                <div className="market-impact-panel" role="region" aria-label={`${selectedIndicator.name} 영향 품목`}>
                  <div>
                    <strong>{selectedIndicator.name} 영향 예상</strong>
                    <span>규칙 기반 참고 · 구매 목표가 계산에는 반영하지 않습니다.</span>
                  </div>
                  <div className="market-impact-list">
                    {selectedIndicator.affected_families.map((impact) => (
                      <button
                        type="button"
                        key={impact.family_code}
                        onClick={() => onNavigate(`/standard-prices?family=${encodeURIComponent(impact.family_code)}`)}
                      >
                        <span>{impact.family_name}</span>
                        <b>원가 압력 · {impact.strength === "HIGH" ? "영향 큼" : impact.strength === "MEDIUM" ? "영향 보통" : "영향 낮음"}</b>
                        <small>{impact.rationale}</small>
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
              <footer>
                {Array.from(new Set(data.indicators.map((item) => item.source_label))).map((label) => (
                  <span key={label}>{label}</span>
                ))}
              </footer>
            </section>
          ) : null}

          <section className="alert-command" aria-labelledby="alert-title">
            <header>
              <div>
                <span>PRICE WATCH</span>
                <h2 id="alert-title">가격 변동 알림</h2>
              </div>
              <Gauge aria-hidden="true" />
            </header>
            {data.alerts.length === 0 ? (
              <div className="alert-demo" aria-label="시연용 가격 변동 알림 예시">
                <div className="alert-demo-heading"><Sparkles aria-hidden="true" size={16} /><span>시연 예시</span></div>
                <strong>서보모터 감속기</strong>
                <p>신규 견적 단가가 최근 표준가격보다 높습니다.</p>
                <em>+18.7%</em>
              </div>
            ) : (
              <ol>
                {data.alerts.slice(0, 4).map((alert) => (
                  <li key={alert.id}>
                    <i className={alert.severity === "CRITICAL" ? "is-critical" : ""} />
                    <div><strong>{alert.item_name}</strong><span>{alert.message}</span></div>
                    <em>{Number(alert.difference_percent) > 0 ? "+" : ""}{Number(alert.difference_percent).toFixed(1)}%</em>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </aside>
      </section>

      <div className="dashboard-ticker" aria-label="핵심 운영 현황">
        <div>
          <span>HISTORICAL QUOTES <b>{historicalQuoteCount.toLocaleString("ko-KR")}건</b></span>
          <span>STANDARDIZED ROWS <b>{standardizedItemCount.toLocaleString("ko-KR")}개</b></span>
          <span>PENDING <b>{unstandardizedItemCount.toLocaleString("ko-KR")}개</b></span>
          <span>REVIEW TODO <b>{data.catalog.cleansing_todo_items.toLocaleString("ko-KR")}개</b></span>
        </div>
      </div>
      <div className="dashboard-system-corner" aria-label="HYUNDAI WIA Procurement Intelligence">
        <strong>HYUNDAI WIA</strong>
        <span>PROCUREMENT INTELLIGENCE</span>
      </div>
    </main>
  );
}

function DashboardHeroBrand() {
  return (
    <div className="dashboard-hero-brand">
      <WiaInteractiveMark />
      <TextBlockAnimation
        blockColor="#00287a"
        className="dashboard-hero-message"
        delay={0.18}
        duration={0.88}
        onceKey="dashboard-core-message"
      >
        <p>
          <span>견적을 받는 순간,</span>
          <strong>협상 목표가 보입니다.</strong>
        </p>
      </TextBlockAnimation>
    </div>
  );
}

type MonthlyPerformanceEntry = {
  month: number;
  label: string;
  kind: "ACTUAL" | "FORECAST";
  equipment_purchase: number;
  integrated_purchase: number;
  total: number;
};

const MonthlyPerformanceChart = memo(function MonthlyPerformanceChart({
  series,
}: {
  series: MonthlyPerformanceEntry[];
}) {
  const [entryAnimationComplete, setEntryAnimationComplete] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => setEntryAnimationComplete(true), 1_050);
    return () => window.clearTimeout(timer);
  }, []);

  return (
    <div className="performance-chart">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={series} barCategoryGap="28%" margin={{ top: 24, right: 4, bottom: 0, left: 4 }}>
          <CartesianGrid vertical={false} stroke="var(--dashboard-grid)" />
          <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: "var(--muted)", fontSize: 10 }} />
          <YAxis hide />
          <Tooltip content={<PerformanceTooltip />} cursor={{ fill: "rgba(0,40,122,.04)" }} />
          <Bar
            name="설비구매"
            dataKey="equipment_purchase"
            stackId="volume"
            animationDuration={950}
            isAnimationActive={!entryAnimationComplete}
          >
            {series.map((entry) => (
              <Cell key={entry.month} fill={entry.kind === "ACTUAL" ? "var(--accent-readable)" : "var(--accent-soft)"} stroke="var(--accent-readable)" strokeDasharray={entry.kind === "FORECAST" ? "3 3" : undefined} />
            ))}
          </Bar>
          <Bar
            name="통합구매"
            dataKey="integrated_purchase"
            stackId="volume"
            radius={[2, 2, 0, 0]}
            animationDuration={950}
            isAnimationActive={!entryAnimationComplete}
          >
            {series.map((entry) => (
              <Cell key={entry.month} fill={entry.kind === "ACTUAL" ? "var(--info)" : "var(--info-surface)"} stroke="var(--info)" strokeDasharray={entry.kind === "FORECAST" ? "3 3" : undefined} />
            ))}
            <LabelList
              className="performance-total-label"
              dataKey="total"
              fill="var(--ink)"
              fontSize={9}
              fontWeight={760}
              position="top"
              formatter={formatApprovalCount}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <span className="performance-readhead" aria-hidden="true" />
    </div>
  );
});

function FunnelLine({ label, value, max, unit = "", meta, delay = 0, accent = false, warning = false }: { label: string; value: number; max: number; unit?: string; meta: string; delay?: number; accent?: boolean; warning?: boolean }) {
  const width = max === 0 ? 0 : Math.max(1.2, value / max * 100);
  return (
    <div
      className={accent ? "is-accent" : warning ? "is-warning" : ""}
    >
      <span>{label}<small>{meta}</small></span><AnimatedNumber value={value} suffix={unit} className="funnel-number" />
      <div className="catalog-flow-track">
        <motion.b initial={{ scaleX: 0 }} animate={{ scaleX: 1 }} transition={{ delay: delay + 0.12, duration: 0.88, ease: [0.22, 1, 0.36, 1] }} style={{ width: `${width}%` }} />
        <motion.i initial={{ left: 0, opacity: 0 }} animate={{ left: `${width}%`, opacity: 1 }} transition={{ delay: delay + 0.12, duration: 0.88, ease: [0.22, 1, 0.36, 1] }} />
      </div>
    </div>
  );
}

function MarketSignalTile({ indicator, index, selected, onSelect }: { indicator: DashboardOverview["indicators"][number]; index: number; selected: boolean; onSelect: () => void }) {
  const available = indicator.points.length > 0;
  const first = Number(indicator.points[0]?.value ?? 0);
  const last = Number(indicator.points.at(-1)?.value ?? 0);
  const difference = last - first;
  const delta = first === 0 ? 0 : difference / first * 100;
  const rising = difference >= 0;
  const gradientId = `market-fill-${indicator.code.toLowerCase()}`;
  return (
    <motion.button
      type="button"
      onClick={onSelect}
      aria-expanded={selected}
      className={`${rising ? "market-signal is-up" : "market-signal is-down"}${selected ? " is-selected" : ""}${available ? "" : " is-unavailable"}`}
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(index * 0.055, 0.28), duration: 0.45 }}
      style={{ "--signal-delay": `${0.45 + Math.min(index * 0.08, 0.4)}s` } as React.CSSProperties}
    >
      <div className="market-sparkline" aria-hidden="true">
        {available ? <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={indicator.points} margin={{ top: 5, right: 2, bottom: 2, left: 2 }}>
            <defs>
              <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor={rising ? "#ff355e" : "#2f7ee6"} stopOpacity={0.28} />
                <stop offset="100%" stopColor={rising ? "#ff355e" : "#2f7ee6"} stopOpacity={0} />
              </linearGradient>
            </defs>
            <YAxis hide domain={["dataMin - 1", "dataMax + 1"]} />
            <Area
              type="monotone"
              dataKey="value"
              stroke={rising ? "#ff355e" : "#2f7ee6"}
              strokeWidth={2}
              fill={`url(#${gradientId})`}
              dot={false}
              animationBegin={Math.min(index * 80, 400)}
              animationDuration={1050}
              animationEasing="ease-out"
            />
          </AreaChart>
        </ResponsiveContainer> : <span className="market-signal-empty">SYNC</span>}
        <span className="market-readhead" />
      </div>
      <div className="market-signal-copy">
        <span>{indicator.name} <small>{indicator.group}</small></span>
        <strong>{available ? <AnimatedNumber value={last} decimals={1} /> : "자료 확인 중"}</strong>
        {available ? <em>
          {rising ? "+" : ""}{difference.toFixed(1)} ({rising ? "+" : ""}{delta.toFixed(1)}%)
        </em> : <em>{indicator.source_status === "STALE" ? "갱신 지연" : "연결 중"}</em>}
        <small className="market-source-period">
          {indicator.latest_period ?? "—"} · {indicator.source_status === "STALE"
            ? "저장 자료"
            : indicator.source_status === "UNAVAILABLE"
              ? "자료 없음"
              : `${indicatorFrequencyLabel(indicator.source_frequency)} 최신`}
        </small>
      </div>
    </motion.button>
  );
}

function PerformanceTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number; name: string; payload: { kind: string; total: number } }>; label?: string }) {
  if (!active || !payload?.length) return null;
  return <div className="dashboard-tooltip"><span>{label} · {payload[0].payload.kind === "ACTUAL" ? "실적" : "예상"}</span>{payload.map((entry) => <small key={entry.name}>{entry.name} {entry.value.toLocaleString("ko-KR")}건</small>)}<strong>합계 {payload[0].payload.total.toLocaleString("ko-KR")}건</strong></div>;
}

function displayFamilyName(value: string) {
  return value.replace(/류$/, "");
}

function indicatorFrequencyLabel(frequency?: "DAILY" | "MONTHLY" | "ANNUAL") {
  if (frequency === "DAILY") return "일별";
  if (frequency === "ANNUAL") return "연간";
  return "월별";
}

function formatApprovalCount(value: unknown) {
  const count = Number(value);
  return Number.isFinite(count) ? count.toLocaleString("ko-KR") : "";
}
