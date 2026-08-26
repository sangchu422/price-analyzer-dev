import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { jsonResponse, renderApp } from "./test/renderApp";
import { THEME_STORAGE_KEY } from "./theme";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.removeItem(THEME_STORAGE_KEY);
  delete document.documentElement.dataset.theme;
});

it("switches themes with an accessible control and persists the selection", async () => {
  const user = userEvent.setup();
  renderApp("/unknown");

  expect(document.documentElement).toHaveAttribute("data-theme", "light");
  const toDark = screen.getByRole("button", { name: "다크 모드로 전환" });
  expect(toDark).toHaveAttribute("aria-pressed", "true");

  await user.click(toDark);

  expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  expect(
    screen.getByRole("button", { name: "라이트 모드로 전환" }),
  ).toHaveAttribute("aria-pressed", "false");
});

it("restores the saved theme when the app renders", () => {
  window.localStorage.setItem(THEME_STORAGE_KEY, "light");
  renderApp("/unknown");

  expect(document.documentElement).toHaveAttribute("data-theme", "light");
  expect(
    screen.getByRole("button", { name: "다크 모드로 전환" }),
  ).toHaveAttribute("aria-pressed", "true");
});

it("keeps the cleansing review page and URL available", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      jsonResponse({
        items: [
          {
            raw_item_id: 7,
            raw: {
              item_name: "BEARING",
              spec: "6204 ZZ",
              unit: "EA",
              quantity: "2",
              unit_price: "2800",
              amount: "5600",
              maker: "KBC",
            },
            normalized: {
              item_name: "BEARING",
              spec: "6204 ZZ",
              unit: "EA",
              quantity: "2.000000",
              unit_price: "2800.000000",
              amount: "5600.000000",
              maker: "KBC",
            },
            reason_code: "AMOUNT_MISMATCH",
            reason_detail: "수량과 금액을 확인해 주세요.",
            decision: {
              id: 41,
              status: "REVIEW_REQUIRED",
              reason_code: "AMOUNT_MISMATCH",
              reason_detail: "수량과 금액을 확인해 주세요.",
              rule_version: "clean-v1",
              decided_by: "SYSTEM",
              decided_at: "2026-07-25T09:30:00",
            },
            source: {
              document_id: 3,
              logical_name: "sample.xlsx",
              variant_id: 8,
              path: "quotes/sample.xlsx",
              sha256: "a".repeat(64),
              security_state: "UNLOCKED",
              selected_for_parsing_at_ingest: true,
              sheet: "Sheet1",
              page: null,
              row: 12,
              cells: "A12:G12",
              parser_name: "xlsx",
              parser_version: "1",
              parser_warnings: [],
            },
          },
        ],
        remaining: 1,
        limit: 50,
        next_cursor: null,
        available_reason_codes: ["AMOUNT_MISMATCH"],
      }),
    ),
  );

  renderApp("/cleansing");

  expect(
    await screen.findByRole("heading", { name: "BEARING" }),
  ).toBeVisible();
  expect(
    screen.getByRole("link", { name: "정제 검토" }),
  ).toHaveAttribute("aria-current", "page");
  expect(
    screen.getByRole("heading", { name: "정제 검토" }),
  ).toHaveFocus();
  expect(window.location.pathname).toBe("/cleansing");
});

it("does not expose the legacy reconciliation page or navigation link", () => {
  renderApp("/reconciliation");

  expect(
    screen.getByText("요청한 작업 화면을 찾을 수 없습니다."),
  ).toBeVisible();
  expect(
    screen.queryByRole("heading", { name: "기존 표준단가 대조" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("link", { name: "기존 DB 대조" }),
  ).not.toBeInTheDocument();
  expect(document.querySelectorAll('a[href="/reconciliation"]')).toHaveLength(0);
});

it("presents the command center and three primary workflow destinations", () => {
  renderApp("/unknown");

  const navigation = screen.getByRole("navigation", { name: "주요 작업" });
  expect(screen.getByRole("link", { name: "종합현황" })).toBeVisible();
  expect(
    screen.getAllByRole("link", { name: /종합현황|정제 검토|표준 DB|신규 견적 분석/ }),
  ).toHaveLength(4);
  expect(navigation).toHaveTextContent("정제 검토");
  expect(navigation).toHaveTextContent("표준 DB");
  expect(navigation).toHaveTextContent("신규 견적 분석");
  expect(navigation).not.toHaveTextContent("품목 그룹핑");
  expect(navigation).not.toHaveTextContent("표준단가");
  expect(navigation).not.toHaveTextContent("견적 비교");
  expect(navigation).not.toHaveTextContent("LOCAL MODE");
  expect(
    Array.from(navigation.querySelectorAll(".navigation-links a"), (link) => link.textContent),
  ).toEqual(["종합현황", "표준 DB", "신규 견적 분석", "정제 검토", "설정"]);
});

it("changes primary pages without overlapping scroll and focus movement", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      jsonResponse({
        items: [],
        next_cursor: null,
        limit: 50,
        latest_build: null,
      }),
    ),
  );
  const scrollTo = vi.fn();
  vi.stubGlobal("scrollTo", scrollTo);
  const focus = vi.spyOn(HTMLElement.prototype, "focus");
  renderApp("/analysis");

  await userEvent.click(screen.getByRole("link", { name: "표준 DB" }));

  await waitFor(() => expect(window.location.pathname).toBe("/standard-prices"));
  expect(scrollTo).toHaveBeenCalledWith({
    top: 0,
    left: 0,
    behavior: "auto",
  });
  expect(focus).toHaveBeenCalledWith({ preventScroll: true });
});
