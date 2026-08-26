import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Database,
  Gauge,
  Radar,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { useEffect, useState } from "react";
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

import { getDashboardOverview, type DashboardOverview } from "../api/client";
import { reasonLabel } from "../components/reasonLabels";

export function DashboardPage({ onNavigate }: { onNavigate: (path: string) => void }) {
  const reduceMotion = useReducedMotion();
  const query = useQuery({
    queryKey: ["dashboard-overview"],
    queryFn: ({ signal }) => getDashboardOverview(signal),
    refetchInterval: 60_000,
  });
  const [indicatorIndex, setIndicatorIndex] = useState(0);
  const indicators = query.data?.indicators ?? [];

  useEffect(() => {
    document.title = "종합현황 · Price Analyzer";
    return () => {
      document.title = "Price Analyzer";
    };
  }, []);

  useEffect(() => {
    if (reduceMotion || indicators.length < 2) return;
    const interval = window.setInterval(
      () => setIndicatorIndex((current) => (current + 1) % indicators.length),
      5_500,
    );
    return () => window.clearInterval(interval);
  }, [indicators.length, reduceMotion]);

  if (query.isLoading) {
    return (
      <main className="dashboard-page dashboard-loading" role="status">
        <span className="dashboard-orbit" aria-hidden="true" />
        <strong>구매 데이터를 연결하고 있습니다</strong>
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
  const indicator = indicators[indicatorIndex] ?? indicators[0];
  const completion = data.catalog.total_standard_items === 0
    ? 0
    : data.catalog.active_price_items / data.catalog.total_standard_items * 100;

  return (
    <main className="dashboard-page">
      <div className="dashboard-grid-field" aria-hidden="true" />
      <header className="dashboard-hero">
        <div>
          <motion.span
            className="dashboard-kicker"
            initial={{ opacity: 0, x: -12 }}
            animate={{ opacity: 1, x: 0 }}
          >
            <i /> PROCUREMENT COMMAND CENTER
          </motion.span>
          <motion.h1
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.08 }}
          >
            견적을 받는 순간,<br /><em>협상 근거가 움직입니다.</em>
          </motion.h1>
        </div>
        <div className="dashboard-hero-status">
          <span>DATA PULSE</span>
          <strong>{data.catalog.active_price_items.toLocaleString("ko-KR")}</strong>
          <small>즉시 활용 가능한 표준 가격</small>
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
                <strong>{completion.toFixed(1)}<small>%</small></strong>
                <span>가격 활용 가능</span>
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
              <FunnelLine label="전체 표준 품목" value={data.catalog.total_standard_items} max={data.catalog.total_standard_items} />
              <FunnelLine label="카테고리 분류" value={data.catalog.categorized_items} max={data.catalog.total_standard_items} />
              <FunnelLine label="가격 즉시 활용" value={data.catalog.active_price_items} max={data.catalog.total_standard_items} accent />
              <FunnelLine label="근거 보완 필요" value={data.catalog.no_evidence_items + data.catalog.rebuild_required_items} max={data.catalog.total_standard_items} warning />
            </div>
            <button className="dashboard-text-action" type="button" onClick={() => onNavigate("/standard-prices")}>
              표준 DB 탐색 <ArrowRight aria-hidden="true" size={16} />
            </button>
          </section>

          <section className="category-spectrum" aria-labelledby="category-spectrum-title">
            <header>
              <div>
                <span>CATEGORY SPECTRUM</span>
                <h2 id="category-spectrum-title">품목군 분포</h2>
              </div>
              <small>동일 카테고리는 가격을 합치지 않고 탐색 축으로만 사용합니다.</small>
            </header>
            <div className="category-spectrum-list">
              {data.categories.map((category, index) => (
                <motion.button
                  key={category.code}
                  type="button"
                  initial={{ opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: Math.min(index * 0.035, 0.3) }}
                  onClick={() => onNavigate(`/standard-prices?category=${category.code}`)}
                >
                  <span>{String(index + 1).padStart(2, "0")}</span>
                  <strong>{category.name}</strong>
                  <em>{category.count.toLocaleString("ko-KR")}</em>
                  <i style={{ "--share": `${Math.max(Number(category.share_percent), 2)}%` } as React.CSSProperties} />
                </motion.button>
              ))}
            </div>
          </section>

          <section className="todo-command">
            <div className="todo-signal"><ShieldAlert aria-hidden="true" /></div>
            <div>
              <span>STANDARD DB / TO-DO</span>
              <strong>미분류·검토 대기 {data.cleansing_todo.count.toLocaleString("ko-KR")}건</strong>
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
                <h2 id="performance-title">월별 견적 분석 계획</h2>
              </div>
              <Activity aria-hidden="true" />
            </header>
            <div className="performance-chart">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.monthly_performance.series} barCategoryGap="28%">
                  <CartesianGrid vertical={false} stroke="var(--dashboard-grid)" />
                  <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: "var(--muted)", fontSize: 10 }} />
                  <YAxis hide />
                  <Tooltip content={<PerformanceTooltip />} cursor={{ fill: "rgba(255,0,0,.035)" }} />
                  <Bar dataKey="count" radius={[2, 2, 0, 0]} animationDuration={950}>
                    {data.monthly_performance.series.map((entry) => (
                      <Cell key={entry.month} fill={entry.kind === "ACTUAL" ? "#ff0000" : "rgba(255,0,0,.24)"} stroke={entry.kind === "FORECAST" ? "#ff0000" : "none"} strokeDasharray={entry.kind === "FORECAST" ? "3 3" : undefined} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <footer>
              <span><i className="is-actual" /> 실적</span>
              <span><i className="is-forecast" /> 예상</span>
              <small>{data.monthly_performance.forecast_method}</small>
            </footer>
          </section>

          {indicator ? (
            <section className="indicator-command" aria-labelledby="indicator-title">
              <header>
                <div>
                  <span>MARKET SIGNAL / {indicator.group}</span>
                  <h2 id="indicator-title">{indicator.name}</h2>
                </div>
                <IndicatorDelta indicator={indicator} />
              </header>
              <div className="indicator-chart">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={indicator.points}>
                    <defs>
                      <linearGradient id="signalFill" x1="0" x2="0" y1="0" y2="1">
                        <stop offset="0%" stopColor="#ff0000" stopOpacity={0.28} />
                        <stop offset="100%" stopColor="#ff0000" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke="var(--dashboard-grid)" vertical={false} />
                    <XAxis dataKey="period" hide />
                    <YAxis hide domain={["dataMin - 2", "dataMax + 2"]} />
                    <Tooltip content={<IndicatorTooltip unit={indicator.unit} />} cursor={{ stroke: "#ff0000", strokeDasharray: "2 3" }} />
                    <Area type="monotone" dataKey="value" stroke="#ff0000" strokeWidth={2.4} fill="url(#signalFill)" animationDuration={1200} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
              <div className="indicator-selector" role="tablist" aria-label="구매 참고 지표">
                {indicators.map((item, index) => (
                  <button
                    type="button"
                    role="tab"
                    aria-selected={indicatorIndex === index}
                    key={item.code}
                    onClick={() => setIndicatorIndex(index)}
                  >
                    {item.name}
                  </button>
                ))}
              </div>
              <footer className={indicator.source_status === "DEMO" ? "is-demo" : "is-official"}>
                <Radar aria-hidden="true" size={14} /> {indicator.source_label}
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
          <span>STANDARD ITEMS <b>{data.catalog.total_standard_items.toLocaleString("ko-KR")}</b></span>
          <span>ACTIVE PRICE <b>{data.catalog.active_price_items.toLocaleString("ko-KR")}</b></span>
          <span>UNCATEGORIZED SOURCE <b>{data.catalog.unmatched_included_items.toLocaleString("ko-KR")}</b></span>
          <span>REVIEW TODO <b>{data.catalog.cleansing_todo_items.toLocaleString("ko-KR")}</b></span>
        </div>
      </div>
      <img className="dashboard-wia-corner" src="/brand/hyundai-wia.png" alt="HYUNDAI WIA" />
    </main>
  );
}

function FunnelLine({ label, value, max, accent = false, warning = false }: { label: string; value: number; max: number; accent?: boolean; warning?: boolean }) {
  const width = max === 0 ? 0 : Math.max(1.2, value / max * 100);
  return (
    <div className={accent ? "is-accent" : warning ? "is-warning" : ""}>
      <span>{label}</span><strong>{value.toLocaleString("ko-KR")}</strong>
      <i><motion.b initial={{ width: 0 }} animate={{ width: `${width}%` }} transition={{ duration: 0.9, ease: "easeOut" }} /></i>
    </div>
  );
}

function IndicatorDelta({ indicator }: { indicator: DashboardOverview["indicators"][number] }) {
  const first = Number(indicator.points[0]?.value ?? 0);
  const last = Number(indicator.points.at(-1)?.value ?? 0);
  const delta = first === 0 ? 0 : (last - first) / first * 100;
  const UpIcon = delta >= 0 ? ArrowUpRight : ArrowDownRight;
  return <em className={delta >= 0 ? "is-up" : "is-down"}><UpIcon size={15} /> {delta >= 0 ? "+" : ""}{delta.toFixed(1)}%</em>;
}

function PerformanceTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ value: number; payload: { kind: string } }>; label?: string }) {
  if (!active || !payload?.length) return null;
  return <div className="dashboard-tooltip"><span>{label} · {payload[0].payload.kind === "ACTUAL" ? "실적" : "예상"}</span><strong>{payload[0].value.toLocaleString("ko-KR")}건</strong></div>;
}

function IndicatorTooltip({ active, payload, label, unit }: { active?: boolean; payload?: Array<{ value: number }>; label?: string; unit: string }) {
  if (!active || !payload?.length) return null;
  return <div className="dashboard-tooltip"><span>{label}</span><strong>{payload[0].value.toLocaleString("ko-KR")} {unit}</strong></div>;
}
