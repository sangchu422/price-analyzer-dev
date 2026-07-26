import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { jsonResponse, renderApp } from "../test/renderApp";

afterEach(() => vi.unstubAllGlobals());

const build = {
  build_run_id: 9,
  status: "SUCCEEDED",
  built_at: "2026-07-27T00:22:05",
  rule_version: "STANDARD_DB_EXACT_V2",
};

const sensor = {
  id: 12,
  member_count: 1,
  observation_count: 1,
  evidence_quality: "SINGLE_OBSERVATION",
  current_price: {
    minimum: "50000.000000",
    median: "50000.000000",
    average: "50000.000000",
    maximum: "50000.000000",
  },
  supplier_summary: ["SUPPLIER C"],
  maker_summary: ["OMRON"],
  quote_date_start: "2026-07-03",
  quote_date_end: "2026-07-03",
  provenance: build,
  current_version: {
    id: 22,
    standard_item_id: 12,
    version_number: 1,
    canonical_name: "SENSOR",
    canonical_spec: "PX-1",
    canonical_unit: "EA",
    aliases: [],
    created_by: "LOCAL_STANDARD_DB_BUILD",
    reason_detail: "STANDARD_DB_EXACT_V2",
    created_at: "2026-07-27T00:22:05",
  },
};

function version(id: number) {
  return {
    id,
    standard_item_id: 12,
    version_number: id,
    observation_count: 1,
    evidence_quality: "SINGLE_OBSERVATION",
    supplier_count: 1,
    latest_quote_date: "2026-07-03",
    prices: sensor.current_price,
    calculation_version: "standard-price-v1",
    audit_status: "CAPTURED",
    draft_fingerprint: "a".repeat(64),
    standard_item_version: {
      id: 22,
      version_number: 1,
      canonical_name: "SENSOR",
      canonical_spec: "PX-1",
      canonical_unit: "EA",
    },
    excluded_count: 0,
    review_required_count: 0,
    exclusions: [],
    exclusion_context_valid: true,
    exclusion_context_error: null,
    approved_by: "LOCAL_STANDARD_DB_BUILD",
    approved_at: "2026-07-27T00:22:05",
    observations: [],
  };
}

it("renders the standard DB as a read-only evidence explorer", async () => {
  const requests: Array<{ url: string; method: string }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, method: init?.method ?? "GET" });
      if (url.includes("/api/catalog/standard-items?")) {
        return jsonResponse({
          items: [sensor],
          next_cursor: null,
          limit: 50,
          latest_build: build,
        });
      }
      if (url.includes("/standard-items/12/evidence?")) {
        return jsonResponse({
          standard_item_id: 12,
          standard_price_version_id: 31,
          observation_count: 1,
          evidence_quality: "SINGLE_OBSERVATION",
          provenance: build,
          observations: [
            {
              raw_item_id: 7,
              unit_price: "50000.000000",
              supplier_name: "SUPPLIER C",
              maker: "OMRON",
              quote_date: "2026-07-03",
              source: {
                document_id: 3,
                logical_name: "quotes/vendor-c.xlsx",
                variant_id: 8,
                path: "quotes/vendor-c.xlsx",
                sheet: "견적",
                page: null,
                row: 12,
                cells: "A12:G12",
              },
            },
          ],
          next_cursor: null,
          limit: 50,
        });
      }
      if (url.includes("/standard-items/12/versions?")) {
        return jsonResponse({
          standard_item_id: 12,
          versions: [version(1)],
          next_cursor: null,
          limit: 50,
          latest_build: build,
        });
      }
      throw new Error(`unexpected request: ${url}`);
    }),
  );

  renderApp("/standard-prices");

  expect(
    await screen.findByRole("heading", { name: "표준 DB" }),
  ).toBeVisible();
  expect(
    await screen.findByRole("button", { name: /SENSOR/ }),
  ).toBeVisible();
  expect(screen.getAllByText("근거 1건").length).toBeGreaterThan(0);
  expect(screen.getByText(/마지막 구축/)).toBeVisible();
  expect(screen.getAllByText("50,000원").length).toBeGreaterThan(0);
  expect(
    await screen.findByRole("columnheader", { name: "공급사" }),
  ).toBeVisible();
  expect(screen.getAllByText("SUPPLIER C").length).toBeGreaterThan(0);
  expect(
    screen.getByRole("link", { name: "원본 견적 근거" }),
  ).toHaveAttribute("href", "/grouping?raw_item_id=7");
  expect(screen.getByRole("heading", { name: "가격 버전 이력" })).toBeVisible();
  expect(screen.queryByRole("button", { name: /승인/ })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("승인자")).not.toBeInTheDocument();
  expect(requests.every(({ method }) => method === "GET")).toBe(true);
});

it("searches and filters standard groups through the paginated API", async () => {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      urls.push(url);
      if (url.includes("/api/catalog/standard-items?")) {
        return jsonResponse({
          items: url.includes("search=PX-1") ? [sensor] : [],
          next_cursor: null,
          limit: 50,
          latest_build: build,
        });
      }
      if (url.includes("/standard-items/12/evidence?")) {
        return jsonResponse({
          standard_item_id: 12,
          standard_price_version_id: 31,
          observation_count: 1,
          evidence_quality: "SINGLE_OBSERVATION",
          provenance: build,
          observations: [],
          next_cursor: null,
          limit: 50,
        });
      }
      if (url.includes("/standard-items/12/versions?")) {
        return jsonResponse({
          standard_item_id: 12,
          versions: [],
          next_cursor: null,
          limit: 50,
          latest_build: build,
        });
      }
      throw new Error(`unexpected request: ${url}`);
    }),
  );
  const user = userEvent.setup();
  renderApp("/standard-prices");

  expect(await screen.findByText("검색 결과가 없습니다.")).toBeVisible();
  await user.type(screen.getByRole("searchbox", { name: "표준 품목 검색" }), "PX-1");
  await user.click(screen.getByRole("button", { name: "검색" }));

  expect(await screen.findByRole("button", { name: /SENSOR/ })).toBeVisible();
  expect(urls.some((url) => url.includes("search=PX-1"))).toBe(true);
});
