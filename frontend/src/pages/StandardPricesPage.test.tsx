import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { jsonResponse, renderApp } from "../test/renderApp";

afterEach(() => vi.unstubAllGlobals());

it("shows a draft, immutable history, and records the approval actor", async () => {
  const requests: Array<{ url: string; body?: unknown }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({
        url,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      if (url.includes("/api/catalog/standard-items?")) {
        return jsonResponse({
          items: [
            {
              id: 2,
              member_count: 2,
              current_version: {
                id: 5,
                standard_item_id: 2,
                version_number: 1,
                canonical_name: "BALL BEARING",
                canonical_spec: "6204 ZZ",
                canonical_unit: "EA",
                aliases: ["BEARING"],
                created_by: "buyer-1",
                reason_detail: "initial",
                created_at: "2026-07-25T10:00:00",
              },
            },
          ],
          next_cursor: null,
          limit: 50,
        });
      }
      if (url.endsWith("/draft")) {
        return jsonResponse({
          standard_item_id: 2,
          standard_item_version_id: 5,
          canonical_unit: "EA",
          observation_count: 2,
          supplier_count: 2,
          latest_quote_date: "2026-07-02",
          prices: {
            minimum: "100.000000",
            median: "110.000000",
            average: "110.000000",
            maximum: "120.000000",
          },
          observations: [
            {
              raw_item_id: 7,
              clean_decision_id: 41,
              membership_decision_id: 51,
              metadata_version_id: 61,
              unit_price: "100.000000",
              supplier_name: "A",
              quote_date: "2026-07-01",
              source: {
                document_id: 3,
                logical_name: "a.xlsx",
                variant_id: 8,
                path: "quotes/a.xlsx",
                sheet: "Sheet1",
                page: null,
                row: 12,
              },
            },
          ],
          exclusions: [],
          context: {
            excluded_count: 0,
            review_required_count: 0,
            membership_rejected_count: 0,
            other_target_count: 0,
            invalid_price_count: 0,
            unit_incompatible_count: 0,
          },
          calculation_version: "standard-price-v1",
          fingerprint: "a".repeat(64),
        });
      }
      if (url.includes("/versions?") && init?.method !== "POST") {
        return jsonResponse({
          standard_item_id: 2,
          versions: [
            {
              id: 70,
              standard_item_id: 2,
              version_number: 1,
              observation_count: 2,
              supplier_count: 2,
              latest_quote_date: "2026-06-01",
              prices: {
                minimum: "90.000000",
                median: "100.000000",
                average: "100.000000",
                maximum: "110.000000",
              },
              calculation_version: "standard-price-v1",
              audit_status: "CAPTURED",
              draft_fingerprint: "b".repeat(64),
              standard_item_version: {
                id: 5,
                version_number: 1,
                canonical_name: "BALL BEARING",
                canonical_spec: "6204 ZZ",
                canonical_unit: "EA",
              },
              excluded_count: 0,
              review_required_count: 0,
              exclusions: [],
              exclusion_context_valid: true,
              exclusion_context_error: null,
              approved_by: "buyer-old",
              approved_at: "2026-07-01T10:00:00",
              observations: [],
            },
          ],
          next_cursor: null,
          limit: 50,
        });
      }
      if (url.endsWith("/versions") && init?.method === "POST") {
        return jsonResponse(
          {
            id: 71,
            standard_item_id: 2,
            version_number: 2,
            observation_count: 2,
            supplier_count: 2,
            latest_quote_date: "2026-07-02",
            prices: {
              minimum: "100.000000",
              median: "110.000000",
              average: "110.000000",
              maximum: "120.000000",
            },
            calculation_version: "standard-price-v1",
            audit_status: "CAPTURED",
            draft_fingerprint: "a".repeat(64),
            standard_item_version: null,
            excluded_count: 0,
            review_required_count: 0,
            exclusions: [],
            exclusion_context_valid: true,
            exclusion_context_error: null,
            approved_by: "buyer-2",
            approved_at: "2026-07-26T10:00:00",
            observations: [],
          },
          { status: 201 },
        );
      }
      throw new Error(`unexpected request: ${url}`);
    }),
  );
  const user = userEvent.setup();
  renderApp("/standard-prices");

  expect((await screen.findAllByText("110.000000"))[0]).toBeVisible();
  expect(screen.getByText("공급사 2곳")).toBeVisible();
  expect(screen.getByText("quotes/a.xlsx")).toBeVisible();
  expect(
    screen.getByRole("link", { name: "원천행 7 감사 보기" }),
  ).toHaveAttribute("href", "/grouping?raw_item_id=7");
  expect(screen.getByText("v1 · buyer-old")).toBeVisible();
  expect(
    screen.getByRole("link", { name: "표준단가 v1 감사 링크" }),
  ).toHaveAttribute(
    "href",
    "/standard-prices?item_id=2&version_id=70",
  );
  await user.type(screen.getByLabelText("승인자"), "buyer-2");
  await user.click(screen.getByRole("button", { name: "표준단가 버전 승인" }));

  await screen.findByText("표준단가 v2를 승인했습니다.");
  const approval = requests.find(
    ({ url, body }) => url.endsWith("/versions") && body,
  );
  expect(approval?.body).toEqual({
    expected_fingerprint: "a".repeat(64),
    expected_current_version_id: 70,
    approved_by: "buyer-2",
  });
  await waitFor(() =>
    expect(
      requests.filter(({ url }) => url.includes("/versions")).length,
    ).toBeGreaterThanOrEqual(3),
  );
});

it("follows catalog cursors without duplicates and opens a linked version", async () => {
  const catalogUrls: string[] = [];
  const item = (id: number, name: string) => ({
    id,
    member_count: id,
    current_version: {
      id: id + 10,
      standard_item_id: id,
      version_number: 1,
      canonical_name: name,
      canonical_spec: `SPEC-${id}`,
      canonical_unit: "EA",
      aliases: [],
      created_by: "buyer",
      reason_detail: "initial",
      created_at: "2026-07-25T10:00:00",
    },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/catalog/standard-items?")) {
        catalogUrls.push(url);
        return url.includes("after_id=1")
          ? jsonResponse({ items: [item(2, "ITEM TWO")], next_cursor: null, limit: 50 })
          : jsonResponse({ items: [item(1, "ITEM ONE")], next_cursor: 1, limit: 50 });
      }
      if (url.endsWith("/standard-items/2/draft")) {
        return jsonResponse({
          standard_item_id: 2,
          standard_item_version_id: 12,
          canonical_unit: "EA",
          observation_count: 1,
          supplier_count: 1,
          latest_quote_date: "2026-07-01",
          prices: {
            minimum: "10.000000",
            median: "10.000000",
            average: "10.000000",
            maximum: "10.000000",
          },
          observations: [],
          exclusions: [],
          context: {},
          calculation_version: "standard-price-v1",
          fingerprint: "c".repeat(64),
        });
      }
      if (url.includes("/standard-items/2/versions?")) {
        return jsonResponse({
          standard_item_id: 2,
          versions: [
            {
              id: 71,
              standard_item_id: 2,
              version_number: 3,
              observation_count: 1,
              supplier_count: 1,
              latest_quote_date: "2026-07-01",
              prices: {
                minimum: "10.000000",
                median: "10.000000",
                average: "10.000000",
                maximum: "10.000000",
              },
              calculation_version: "standard-price-v1",
              audit_status: "CAPTURED",
              draft_fingerprint: "c".repeat(64),
              standard_item_version: null,
              excluded_count: 0,
              review_required_count: 0,
              exclusions: [],
              exclusion_context_valid: true,
              exclusion_context_error: null,
              approved_by: "buyer-linked",
              approved_at: "2026-07-26T10:00:00",
              observations: [],
            },
          ],
          next_cursor: null,
          limit: 50,
        });
      }
      throw new Error(`unexpected request: ${url}`);
    }),
  );

  renderApp("/standard-prices?item_id=2&version_id=71");

  expect(
    await screen.findByRole("heading", { name: "ITEM TWO" }),
  ).toBeVisible();
  expect(await screen.findByText("v3 · buyer-linked")).toBeVisible();
  expect(
    screen.getByRole("group", { name: "버전 근거" }),
  ).toHaveAttribute("open");
  expect(catalogUrls.filter((url) => url.includes("after_id=1"))).toHaveLength(1);
  expect(screen.getAllByRole("button", { name: /ITEM/ })).toHaveLength(2);
  expect(
    screen.queryByRole("button", { name: "다음 표준품목 불러오기" }),
  ).not.toBeInTheDocument();
});
