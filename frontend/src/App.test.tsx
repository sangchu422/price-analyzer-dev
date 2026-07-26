import { screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { jsonResponse, renderApp } from "./test/renderApp";

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
