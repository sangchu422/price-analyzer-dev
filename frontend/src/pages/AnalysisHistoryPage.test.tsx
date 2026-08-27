import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { jsonResponse, renderApp } from "../test/renderApp";

afterEach(() => vi.unstubAllGlobals());

it("keeps analyzed quotes out of the standard DB until the buyer includes them", async () => {
  const user = userEvent.setup();
  let included = false;
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    requests.push({ url, init });
    if (url.includes("/api/analysis/history")) {
      return jsonResponse({
        items: [{
          run_id: 71,
          document_id: 33,
          file_name: "한로기술 견적서.xlsx",
          created_by: "통합구매팀",
          analyzed_at: "2026-08-27T09:20:00",
          total_line_count: 234,
          target_available_count: 196,
          quote_total_amount: "2419965250",
          target_total_amount: "2211642750",
          catalog_state: included ? "INCLUDED" : "NOT_INCLUDED",
          current_decision_id: included ? 91 : null,
          state_decided_by: included ? "손철호" : null,
          state_decided_at: included ? "2026-08-27T10:00:00" : null,
        }],
        total: 1,
        next_cursor: null,
        limit: 30,
      });
    }
    if (url.includes("/catalog-state")) {
      included = true;
      return jsonResponse({ decision_id: 91, state: "INCLUDED", decided_at: "2026-08-27T10:00:00" });
    }
    return jsonResponse({});
  }));

  renderApp("/analysis/history");

  expect(await screen.findByRole("heading", { name: "분석 이력 관리" }, { timeout: 3_000 })).toBeVisible();
  expect(await screen.findByText("한로기술 견적서.xlsx")).toBeVisible();
  expect(screen.getByText("234개")).toBeVisible();
  const historyRow = screen.getByRole("row", { name: /한로기술 견적서/ });
  expect(within(historyRow).getByText("표준 DB 미반영")).toBeVisible();
  await user.click(within(historyRow).getByRole("button", { name: /표준 DB 반영/ }));
  const dialog = screen.getByRole("dialog", { name: "표준 DB에 반영할까요?" });
  await user.type(within(dialog).getByLabelText("담당자"), "손철호");
  await user.click(within(dialog).getByRole("button", { name: "확인 후 반영" }));

  await waitFor(() => expect(requests.some((request) => request.url.endsWith("/api/analysis/runs/71/catalog-state"))).toBe(true));
  const stateRequest = requests.find((request) => request.url.endsWith("/api/analysis/runs/71/catalog-state"));
  expect(JSON.parse(String(stateRequest?.init?.body))).toMatchObject({
    state: "INCLUDED",
    decided_by: "손철호",
    expected_current_decision_id: null,
  });
  expect(await screen.findByText("현재 표준 DB 가격 근거에 포함되어 있습니다.")).toBeVisible();
});
