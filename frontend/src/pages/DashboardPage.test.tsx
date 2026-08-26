import { screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { jsonResponse, renderApp } from "../test/renderApp";

afterEach(() => vi.unstubAllGlobals());

it("renders the procurement command center with honest data-source labels", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => jsonResponse({
      as_of: "2026-08-26",
      catalog: {
        total_standard_items: 7684,
        categorized_items: 7684,
        active_price_items: 7672,
        rebuild_required_items: 0,
        no_evidence_items: 12,
        unmatched_included_items: 1087,
        cleansing_todo_items: 8336,
        latest_build_run_id: 13,
      },
      categories: [
        {
          code: "DRIVE_MOTION",
          name: "구동·모션",
          description: "모터와 감속기",
          count: 619,
          share_percent: "8.1",
        },
        {
          code: "GENERAL_COMPONENT",
          name: "공통 설비·부품",
          description: "추정 탐색 분류",
          count: 2842,
          share_percent: "37.0",
        },
      ],
      cleansing_todo: {
        count: 8336,
        top_reasons: [{ reason_code: "OUTLIER_UNIT_PRICE", count: 202 }],
      },
      monthly_performance: {
        year: 2026,
        current_month: 8,
        forecast_method: "완료 월의 월평균 접수 건수",
        source_status: "OPERATIONAL",
        series: Array.from({ length: 12 }, (_, index) => ({
          month: index + 1,
          label: `${index + 1}월`,
          count: index < 8 ? index : 6,
          kind: index < 8 ? "ACTUAL" : "FORECAST",
        })),
      },
      indicators: [
        {
          code: "USD_KRW",
          name: "원/달러",
          group: "환율",
          unit: "index",
          source_status: "DEMO",
          source_label: "시연 인덱스 · 공식 데이터 연동 전",
          points: [
            { period: "2026-01", value: 100 },
            { period: "2026-08", value: 107 },
          ],
        },
      ],
      alerts: [],
    })),
  );

  renderApp("/dashboard");

  expect(await screen.findByRole(
    "heading",
    { name: /견적을 받는 순간/ },
    { timeout: 3_000 },
  )).toBeInTheDocument();
  expect(screen.getAllByText("7,672").length).toBeGreaterThan(0);
  expect(screen.getByText("카테고리 분류")).toBeVisible();
  expect(screen.getByRole("button", { name: /구동·모션/ })).toBeInTheDocument();
  expect(screen.getByText("미분류·검토 대기 8,336건")).toBeVisible();
  expect(screen.getByText("시연 인덱스 · 공식 데이터 연동 전")).toBeVisible();
  expect(screen.getByText("완료 월의 월평균 접수 건수")).toBeVisible();
  expect(screen.getByText("신규 반영 가격의 이상징후가 없습니다.")).toBeVisible();
});
