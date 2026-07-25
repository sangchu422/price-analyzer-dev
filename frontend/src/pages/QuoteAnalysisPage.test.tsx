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
  expect(
    within(candidateRow).getByRole("link", { name: "원천행 감사 보기" }),
  ).toHaveAttribute("href", "/grouping?raw_item_id=8");

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

it("pages beyond 50 documents and rows without retaining a previous filter result", async () => {
  const user = userEvent.setup();
  let resolveHigh:
    | ((response: Response | PromiseLike<Response>) => void)
    | undefined;
  const document = (id: number) => ({
    id,
    logical_name: `quote-${id}.xlsx`,
    raw_item_count: id === 51 ? 51 : 0,
    included_count: id === 51 ? 51 : 0,
    excluded_count: 0,
    review_required_count: 0,
    undecided_count: 0,
    analysis_ready: true,
  });
  const line = (id: number) => ({
    raw_item_id: id,
    item_name: `ROW ${id}`,
    spec: null,
    unit: "EA",
    quote_unit_price: "10.000000",
    match_status: "NO_MATCH",
    assessment: "REVIEW_REQUIRED",
    reference_price: null,
    minimum_price: null,
    average_price: null,
    maximum_price: null,
    variance_amount: null,
    variance_percent: null,
    clean_decision_id: id,
    membership_decision_id: null,
    standard_item_id: null,
    standard_item_version_id: null,
    canonical_name: null,
    canonical_spec: null,
    canonical_unit: null,
    standard_price_version_id: null,
    standard_price_item_version_id: null,
    market_price_lookup_required: true,
    market_price_lookup_status: "FUTURE_MARKET_LOOKUP",
    candidates: [],
    source: {
      document_id: 51,
      logical_name: "quote-51.xlsx",
      variant_id: 1,
      path: "quotes/quote-51.xlsx",
      sha256: "a".repeat(64),
      sheet: "Sheet1",
      page: null,
      row: id,
      cells: null,
      parser_name: "test",
      parser_version: "1",
    },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/analysis/documents?")) {
        return url.includes("after_id=50")
          ? jsonResponse({
              items: [document(50), document(51)],
              total: 51,
              limit: 50,
              offset: 0,
              next_cursor: 50,
            })
          : jsonResponse({
              items: Array.from({ length: 50 }, (_, index) =>
                document(index + 1),
              ),
              total: 51,
              limit: 50,
              offset: 0,
              next_cursor: 50,
            });
      }
      if (url.includes("/api/analysis/documents/51")) {
        if (url.includes("assessment=HIGH")) {
          return new Promise<Response>((resolve) => {
            resolveHigh = resolve;
          });
        }
        return url.includes("after_id=50")
          ? jsonResponse({
              document: { id: 51, logical_name: "quote-51.xlsx" },
              lines: [line(50), line(51)],
              next_cursor: 50,
              limit: 50,
            })
          : jsonResponse({
              document: { id: 51, logical_name: "quote-51.xlsx" },
              lines: Array.from({ length: 50 }, (_, index) => line(index + 1)),
              next_cursor: 50,
              limit: 50,
            });
      }
      if (url.includes("/api/analysis/documents/")) {
        const id = Number(url.match(/documents\/(\d+)/)?.[1]);
        return jsonResponse({
          document: { id, logical_name: `quote-${id}.xlsx` },
          lines: [],
          next_cursor: null,
          limit: 50,
        });
      }
      throw new Error(`unexpected request: ${url}`);
    }),
  );

  renderApp("/analysis");
  await user.click(
    await screen.findByRole("button", { name: "다음 견적서 불러오기" }),
  );
  await user.selectOptions(screen.getByLabelText("견적서"), "51");
  expect(await screen.findByText("ROW 50")).toBeVisible();
  await user.click(
    screen.getByRole("button", { name: "다음 분석 행 불러오기" }),
  );
  expect(await screen.findByText("ROW 51")).toBeVisible();
  expect(screen.getAllByText("ROW 50")).toHaveLength(1);
  expect(screen.getByText("표시 51개 / 원천 전체 51개")).toBeVisible();

  await user.selectOptions(screen.getByLabelText("가격 판정"), "HIGH");
  await waitFor(() =>
    expect(screen.queryByText("ROW 1")).not.toBeInTheDocument(),
  );
  expect(screen.getByRole("status")).toHaveTextContent(
    "새 조건의 결과를 불러오는 중",
  );
  resolveHigh?.(
    jsonResponse({
      document: { id: 51, logical_name: "quote-51.xlsx" },
      lines: [],
      next_cursor: null,
      limit: 50,
    }),
  );
  await screen.findByText("현재 서버 필터에 맞는 행이 없습니다.");
});
