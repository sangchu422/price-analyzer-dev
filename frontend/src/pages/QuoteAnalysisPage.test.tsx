import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { jsonResponse, renderApp } from "../test/renderApp";

afterEach(() => vi.unstubAllGlobals());

it("uses server filters and never applies a price to candidate rows", async () => {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      urls.push(url);
      if (url.includes("/api/analysis/documents?")) {
        return jsonResponse({
          items: [
            {
              id: 3,
              logical_name: "new-quote.xlsx",
              raw_item_count: 2,
              included_count: 2,
              excluded_count: 0,
              review_required_count: 0,
              undecided_count: 0,
              analysis_ready: true,
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        });
      }
      if (url.includes("/api/analysis/documents/3")) {
        return jsonResponse({
          document: { id: 3, logical_name: "new-quote.xlsx" },
          lines: [
            {
              raw_item_id: 7,
              item_name: "BEARING",
              spec: "6204 ZZ",
              unit: "EA",
              quote_unit_price: "130.000000",
              match_status: "MATCHED",
              assessment: "HIGH",
              reference_price: "100.000000",
              minimum_price: "90.000000",
              average_price: "100.000000",
              maximum_price: "110.000000",
              variance_amount: "30.000000",
              variance_percent: "30.000000",
              clean_decision_id: 41,
              membership_decision_id: 51,
              standard_item_id: 2,
              standard_item_version_id: 5,
              standard_price_version_id: 70,
              standard_price_item_version_id: 5,
              market_price_lookup_required: false,
              market_price_lookup_status: "NOT_REQUIRED",
              candidates: [],
              source: {
                document_id: 3,
                logical_name: "new-quote.xlsx",
                variant_id: 8,
                path: "quotes/new-quote.xlsx",
                sha256: "a".repeat(64),
                sheet: "Sheet1",
                page: null,
                row: 12,
                cells: "A12:G12",
                parser_name: "xlsx",
                parser_version: "1",
              },
            },
            {
              raw_item_id: 8,
              item_name: "SERVO MOTOR",
              spec: "SGMAH-04AAA61",
              unit: "EA",
              quote_unit_price: "500.000000",
              match_status: "CANDIDATE",
              assessment: "NOT_APPLICABLE",
              reference_price: null,
              minimum_price: null,
              average_price: null,
              maximum_price: null,
              variance_amount: null,
              variance_percent: null,
              clean_decision_id: 42,
              membership_decision_id: null,
              standard_item_id: null,
              standard_item_version_id: null,
              standard_price_version_id: null,
              standard_price_item_version_id: null,
              market_price_lookup_required: true,
              market_price_lookup_status: "FUTURE_MARKET_LOOKUP",
              candidates: [
                {
                  standard_item_id: 4,
                  standard_item_version_id: 9,
                  canonical_name: "AC SERVO MOTOR",
                  canonical_spec: "SGMAH-04AAA61",
                  canonical_unit: "EA",
                  final_score: "0.920000",
                  method: "LEXICAL",
                  matched_tokens: ["SGMAH-04AAA61"],
                  embedding_status: "DISABLED",
                  embedding_model: null,
                },
              ],
              source: {
                document_id: 3,
                logical_name: "new-quote.xlsx",
                variant_id: 8,
                path: "quotes/new-quote.xlsx",
                sha256: "a".repeat(64),
                sheet: "Sheet1",
                page: null,
                row: 13,
                cells: "A13:G13",
                parser_name: "xlsx",
                parser_version: "1",
              },
            },
          ],
          next_cursor: null,
          limit: 50,
        });
      }
      throw new Error(`unexpected request: ${url}`);
    }),
  );
  const user = userEvent.setup();
  renderApp("/analysis");

  expect(
    await screen.findByRole("heading", { name: "new-quote.xlsx" }),
  ).toBeVisible();
  const candidateRow = await screen.findByRole("row", { name: /SERVO MOTOR/ });
  expect(within(candidateRow).getByText("후보만 있음")).toBeVisible();
  expect(within(candidateRow).getByText("적용 안 함")).toBeVisible();
  expect(within(candidateRow).queryByText("100.000000")).not.toBeInTheDocument();

  await user.selectOptions(
    screen.getByRole("combobox", { name: "매칭 상태" }),
    "CANDIDATE",
  );
  await user.selectOptions(
    screen.getByRole("combobox", { name: "가격 판정" }),
    "HIGH",
  );

  await waitFor(() =>
    expect(
      urls.some(
        (url) =>
          url.includes("match_status=CANDIDATE") &&
          url.includes("assessment=HIGH"),
      ),
    ).toBe(true),
  );
  expect(screen.getByRole("main")).toBeVisible();
});
