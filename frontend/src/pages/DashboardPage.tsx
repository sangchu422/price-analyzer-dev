import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Database,
  Gauge,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { getDashboardOverview, syncProcurementIndicators, type DashboardOverview } from "../api/client";
import { AnimatedNumber } from "../components/AnimatedNumber";
import { reasonLabel } from "../components/reasonLabels";
import { Skeleton } from "../components/Skeleton";
import { WiaInteractiveMark } from "../components/WiaInteractiveMark";

export function DashboardPage({ onNavigate }: { onNavigate: (path: string) => void }) {
  const [selectedIndicatorCode, setSelectedIndicatorCode] = useState<string | null>(null);
  const [familiesExpanded, setFamiliesExpanded] = useState(false);
  const [performanceYear, setPerformanceYear] = useState(2026);
  const indicatorSyncAttempted = useRef(false);
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["dashboard-overview"],
    queryFn: ({ signal }) => getDashboardOverview(signal),
    refetchInterval: 60_000,
  });

  useEffect(() => {
    document.title = "종합현황 · 통합 견적 분석 시스템";
    return () => {
      document.title = "통합 견적 분석 시스템";
    };
  }, []);

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

  if (query.isLoading) {
    return (
      <main className="dashboard-page dashboard-skeleton" role="status" aria-busy="true">
        <div className="dashboard-grid-field" aria-hidden="true" />
        <header className="dashboard-hero">
          <h1 className="sr-only">HYUNDAI WIA 구매 종합현황</h1>
          <WiaInteractiveMark />
          <div className="dashboard-hero-status">
            <span>DATA PULSE</span>
            <Skeleton width="116px" height="42px" />
            <Skeleton width="94px" height="10px" />
          </div>
        </header>
        <section className="dashboard-skeleton-grid">
          <Skeleton as="div" className="dashboard-skeleton-panel is-primary" />
          <Skeleton as="div" className="dashboard-skeleton-panel" />
          <Skeleton as="div" className="dashboard-skeleton-panel is-wide" />
          <Skeleton as="div" className="dashboard-skeleton-panel" />
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
  const visibleFamilies = familiesExpanded ? data.families : data.families.slice(0, 12);
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
        <WiaInteractiveMark />
        <div className="dashboard-hero-status">
          <span>DATA PULSE</span>
          <AnimatedNumber value={standardizedItemCount} className="dashboard-pulse-number" />
          <small>표준화 완료 품목</small>
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
              <FunnelLine label="과거 견적서" value={historicalQuoteCount} max={historicalQuoteCount} unit="건" />
              <FunnelLine label="전체 견적 품목" value={eligibleItemCount} max={eligibleItemCount} unit="개" />
              <FunnelLine label="표준화 완료" value={standardizedItemCount} max={eligibleItemCount} unit="개" accent />
              <FunnelLine label="표준화 대기" value={unstandardizedItemCount} max={eligibleItemCount} unit="개" warning />
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
              <small>표준 DB를 구매 관점의 {data.families.length.toLocaleString("ko-KR")}개 품목으로 묶었습니다.</small>
            </header>
            <motion.div className="category-spectrum-list" layout>
              <AnimatePresence initial={false}>
              {visibleFamilies.map((family, index) => (
                <motion.button
                  key={family.code}
                  type="button"
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -8 }}
                  layout
                  transition={{ delay: Math.min(index * 0.035, 0.3) }}
                  onClick={() => onNavigate(`/standard-prices?family=${encodeURIComponent(family.code)}`)}
                >
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <strong>{family.display_name ?? displayFamilyName(family.name)}</strong>
                  <AnimatedNumber value={family.item_count} className="category-count" />
                  <i style={{ "--share": `${Math.max(Number(family.share_percent), 2)}%` } as React.CSSProperties} />
                </motion.button>
              ))}
              </AnimatePresence>
            </motion.div>
            {data.families.length > 12 ? (
              <button
                type="button"
                className="category-spectrum-toggle"
                aria-expanded={familiesExpanded}
                onClick={() => setFamiliesExpanded((current) => !current)}
              >
                {familiesExpanded ? "품목 접기" : `전체 ${data.families.length.toLocaleString("ko-KR")}개 품목 펼치기`}
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
                <h2 id="performance-title">월별 견적 분석 현황</h2>
              </div>
              <div className="performance-year-switch" aria-label="현황 연도">
                {availableYears.map((year) => (
                  <button type="button" key={year} aria-pressed={performanceYear === year} onClick={() => setPerformanceYear(year)}>{year}</button>
                ))}
              </div>
            </header>
            <div className="performance-chart">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={performanceSeries} barCategoryGap="28%">
                  <CartesianGrid vertical={false} stroke="var(--dashboard-grid)" />
                  <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: "var(--muted)", fontSize: 10 }} />
                  <YAxis hide />
                  <Tooltip content={<PerformanceTooltip />} cursor={{ fill: "rgba(0,40,122,.04)" }} />
                  <Bar name="설비구매" dataKey="equipment_purchase" stackId="volume" animationDuration={950}>
                    {performanceSeries.map((entry) => (
                      <Cell key={entry.month} fill={entry.kind === "ACTUAL" ? "#00287a" : "rgba(0,40,122,.24)"} stroke="#00287a" strokeDasharray={entry.kind === "FORECAST" ? "3 3" : undefined} />
                    ))}
                  </Bar>
                  <Bar name="통합구매" dataKey="integrated_purchase" stackId="volume" radius={[2, 2, 0, 0]} animationDuration={950}>
                    {performanceSeries.map((entry) => (
                      <Cell key={entry.month} fill={entry.kind === "ACTUAL" ? "#5f86d5" : "rgba(95,134,213,.22)"} stroke="#5f86d5" strokeDasharray={entry.kind === "FORECAST" ? "3 3" : undefined} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              <span className="performance-readhead" aria-hidden="true" />
            </div>
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
              <div className="alert-empty"><Sparkles size={17} /> 신규 반영 가격의 이상징후가 없습니다.</div>
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
          <span>STANDARDIZED <b>{standardizedItemCount.toLocaleString("ko-KR")}개</b></span>
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

function FunnelLine({ label, value, max, unit = "", accent = false, warning = false }: { label: string; value: number; max: number; unit?: string; accent?: boolean; warning?: boolean }) {
  const width = max === 0 ? 0 : Math.max(1.2, value / max * 100);
  return (
    <div className={accent ? "is-accent" : warning ? "is-warning" : ""}>
      <span>{label}</span><AnimatedNumber value={value} suffix={unit} className="funnel-number" />
      <i><motion.b initial={{ width: 0 }} animate={{ width: `${width}%` }} transition={{ duration: 0.9, ease: "easeOut" }} /></i>
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
        <small className="market-source-period">{indicator.latest_period ?? "—"} · {indicator.source_status === "STALE" ? "저장 자료" : indicator.source_status === "UNAVAILABLE" ? "자료 없음" : "최신 자료"}</small>
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
