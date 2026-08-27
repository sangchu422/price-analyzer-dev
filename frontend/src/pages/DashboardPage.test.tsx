import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { jsonResponse, renderApp } from "../test/renderApp";

afterEach(() => vi.unstubAllGlobals());

it("renders the procurement command center with honest data-source labels", async () => {
  let releaseFetch!: () => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      await new Promise<void>((resolve) => {
        releaseFetch = resolve;
      });
      return jsonResponse({
      as_of: "2026-08-26",
      catalog: {
        total_standard_items: 7684,
        categorized_items: 7684,
        family_classified_items: 7672,
        active_price_items: 7672,
        rebuild_required_items: 0,
        no_evidence_items: 12,
        unmatched_included_items: 1087,
        cleansing_todo_items: 1022,
        latest_build_run_id: 13,
        historical_quote_document_count: 483,
        eligible_item_count: 31669,
        standardized_item_count: 23436,
        unstandardized_item_count: 8233,
        standardization_percent: "74.0",
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
      families: [
        { code: "MOTOR", name: "모터류", display_name: "모터", item_count: 619, observation_count: 1200, share_percent: "8.1" },
        { code: "GENERAL", name: "공통 설비·부품류", display_name: "공통 설비·부품", item_count: 2842, observation_count: 3200, share_percent: "37.0" },
      ],
      cleansing_todo: {
        count: 1022,
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
          source_frequency: "DAILY",
          latest_period: "2026-08-21",
          points: [
            { period: "2026-08-20", value: 100 },
            { period: "2026-08-21", value: 107 },
          ],
          affected_families: [{
            family_code: "MOTOR",
            family_name: "모터류",
            direction: "COST_PRESSURE",
            strength: "HIGH",
            cost_driver: "수입 부품과 외화 결제 비중",
            rationale: "환율 상승 시 수입 부품의 원화 구매 부담이 커질 수 있습니다.",
            basis: "RULE_BASED",
          }],
        },
      ],
        alerts: [],
      });
    }),
  );

  renderApp("/dashboard");
  expect(
    await screen.findByRole(
      "heading",
      { name: "HYUNDAI WIA 구매 종합현황" },
      { timeout: 3_000 },
    ),
  ).toBeInTheDocument();
  const loadingLogo = screen.getByRole("img", { name: "HYUNDAI WIA" });
  expect(loadingLogo).toBeInTheDocument();

  releaseFetch();
  expect(await screen.findByText("표준화 완료", {}, { timeout: 3_000 })).toBeVisible();
  expect(document.querySelector(".wia-vector-logo")).toBe(loadingLogo);
  expect(screen.getByRole("img", { name: "HYUNDAI WIA" })).toBeInTheDocument();
  expect(document.querySelector(".wia-hero-mark img")).not.toBeInTheDocument();
  expect(document.querySelectorAll(".wia-logo-piece")).toHaveLength(5);
  expect(document.querySelector(".performance-readhead")).toBeInTheDocument();
  expect(document.querySelector(".market-readhead")).toBeInTheDocument();
  expect(screen.getAllByLabelText("23,436").length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "모터 619개, 전체의 8.1%" })).toBeInTheDocument();
  expect(screen.getByText("표준 DB 단계별 현황")).toBeVisible();
  expect(screen.getByText("수집부터 활용까지")).toBeVisible();
  expect(screen.queryByText("품목군 분포")).not.toBeInTheDocument();
  expect(document.querySelector(".todo-command strong")).toHaveTextContent("미분류·검토 대기");
  expect(screen.getByLabelText("1,022건")).toBeInTheDocument();
  expect(screen.getByText("시연 인덱스 · 공식 데이터 연동 전")).toBeVisible();
  expect(screen.getByText("완료 월의 월평균 접수 건수")).toBeVisible();
  expect(screen.getByText("구매 참고 지표")).toBeVisible();
  expect(screen.getByRole("heading", { name: "월별 품의 현황" })).toBeVisible();
  expect(screen.queryByText("월별 견적 분석 현황")).not.toBeInTheDocument();
  expect(document.querySelector(".market-signal-copy > span")).toHaveTextContent("원/달러");
  expect(screen.getByText("2026-08-21 · 일별 최신")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /원\/달러/ }));
  expect(screen.getByText("규칙 기반 참고 · 구매 목표가 계산에는 반영하지 않습니다.")).toBeVisible();
  expect(screen.getByText("환율 상승 시 수입 부품의 원화 구매 부담이 커질 수 있습니다.")).toBeVisible();
  expect(screen.getByLabelText("시연용 가격 변동 알림 예시")).toBeVisible();
  expect(screen.getByText("서보모터 감속기")).toBeVisible();
  expect(screen.getByText("+18.7%")).toBeVisible();
});
