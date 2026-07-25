import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderApp } from "../test/renderApp";

const unmatched = {
  items: [
    {
      raw_item_id: 7,
      name: "BEARING",
      spec: "6204 ZZ",
      unit: "EA",
      current_cleansing_decision_id: 41,
      current_membership_decision_id: null,
    },
  ],
  next_cursor: null,
  limit: 50,
};

const candidate = {
  match_status: "CANDIDATE",
  raw_item: {
    id: 7,
    name: " Bearing ",
    spec: "6204-ZZ",
    unit: "EA",
    quantity: "2",
    unit_price: "2,800",
    amount: "5,600",
  },
  normalized: {
    name: "BEARING",
    spec: "6204 ZZ",
    unit: "EA",
    quantity: "2.000000",
    unit_price: "2800.000000",
    amount: "5600.000000",
  },
  current_cleansing_decision: {
    id: 41,
    status: "INCLUDED",
    reason_code: "VALID",
    reason_detail: null,
    rule_version: "clean-v1",
  },
  current_membership_decision_id: null,
  current_document_metadata: null,
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
  candidates: [
    {
      standard_item_id: 2,
      standard_item_version_id: 5,
      canonical_name: "BALL BEARING",
      canonical_spec: "6204 ZZ",
      canonical_unit: "EA",
      aliases: ["BEARING"],
      name_score: "0.910000",
      spec_score: "1.000000",
      token_score: "1.000000",
      embedding_score: null,
      embedding_status: "DISABLED",
      embedding_model: null,
      final_score: "0.960000",
      matched_tokens: ["6204"],
      method: "LEXICAL",
      unit_compatible: true,
      model_tokens_compatible: true,
    },
  ],
};

afterEach(() => vi.unstubAllGlobals());

describe("GroupingReviewPage", () => {
  it("shows provenance and submits a human-approved candidate match", async () => {
    const requests: Array<{ url: string; body?: unknown }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        requests.push({
          url,
          body: init?.body ? JSON.parse(String(init.body)) : undefined,
        });
        if (url.includes("/unmatched")) return jsonResponse(unmatched);
        if (url.includes("/candidates")) return jsonResponse(candidate);
        if (url.includes("/memberships")) {
          return jsonResponse(
            {
              id: 91,
              raw_item_id: 7,
              standard_item_id: 2,
              status: "MATCHED",
              candidate_score: "0.960000",
              method: "MANUAL_CANDIDATE",
              evidence: {},
              supersedes_decision_id: null,
              decided_by: "buyer-1",
              decided_at: "2026-07-26T10:00:00",
            },
            { status: 201 },
          );
        }
        throw new Error(`unexpected request: ${url}`);
      }),
    );
    const user = userEvent.setup();
    renderApp("/grouping");

    await user.click(
      await screen.findByRole("button", { name: /BEARING/ }),
    );
    expect(screen.getByText("단위 호환")).toBeVisible();
    expect(screen.getByText("모델 토큰 6204")).toBeVisible();
    expect(screen.getByText("토큰 점수")).toBeVisible();
    expect(screen.getByText("모델 토큰 호환")).toBeVisible();
    expect(
      within(
        screen.getByRole("table", { name: "원본과 정제 값 비교" }),
      ).getByRole("row", { name: "단가 2,800 2800.000000" }),
    ).toBeVisible();
    expect(screen.getByText("quotes/sample.xlsx")).toBeVisible();
    expect(
      screen.getByRole("link", { name: "원천행 감사 보기" }),
    ).toHaveAttribute("href", "/grouping?raw_item_id=7");
    await user.type(screen.getByLabelText("후보 검토자"), "buyer-1");
    await user.type(
      screen.getByLabelText("후보 판정 근거"),
      "모델 번호와 단위를 확인함",
    );
    await user.click(
      screen.getByRole("button", { name: "표준품목으로 확정" }),
    );

    await screen.findByText("그룹핑 판정을 저장했습니다.");
    const request = requests.find(({ url }) => url.includes("/memberships"));
    expect(request?.body).toMatchObject({
      standard_item_id: 2,
      status: "MATCHED",
      expected_current_decision_id: null,
      candidate_score: "0.960000",
      decided_by: "buyer-1",
    });
  });

  it("loads the next cursor once and focuses the next row after approval", async () => {
    const urls: string[] = [];
    const second = {
      ...unmatched.items[0],
      raw_item_id: 8,
      name: "SENSOR",
      spec: "PX-01",
      current_cleansing_decision_id: 42,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        urls.push(url);
        if (url.includes("/unmatched") && url.includes("after_id=7")) {
          return jsonResponse({
            items: [unmatched.items[0], second],
            next_cursor: null,
            limit: 50,
          });
        }
        if (url.includes("/unmatched")) {
          return jsonResponse({
            items: unmatched.items,
            next_cursor: 7,
            limit: 50,
          });
        }
        if (url.includes("/raw-items/7/candidates")) {
          return jsonResponse(candidate);
        }
        if (url.includes("/raw-items/8/candidates")) {
          return jsonResponse({
            ...candidate,
            raw_item: { ...candidate.raw_item, id: 8, name: "SENSOR" },
            normalized: {
              ...candidate.normalized,
              name: "SENSOR",
              spec: "PX-01",
            },
            candidates: [],
          });
        }
        if (url.includes("/memberships") && init?.method === "POST") {
          return jsonResponse({ id: 91, status: "MATCHED" }, { status: 201 });
        }
        throw new Error(`unexpected request: ${url}`);
      }),
    );
    const user = userEvent.setup();
    renderApp("/grouping");

    await user.click(await screen.findByRole("button", { name: /BEARING/ }));
    await user.type(screen.getByLabelText("후보 검토자"), "buyer-1");
    await user.type(screen.getByLabelText("후보 판정 근거"), "근거 확인");
    await user.click(screen.getByRole("button", { name: "표준품목으로 확정" }));

    const nextRow = await screen.findByRole("button", { name: /SENSOR/ });
    expect(nextRow).toHaveFocus();
    expect(
      await screen.findByRole("heading", { name: "SENSOR" }),
    ).toBeVisible();
    expect(
      urls.filter((url) => url.includes("after_id=7")),
    ).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /SENSOR/ })).toHaveLength(1);
    expect(
      screen.queryByRole("button", { name: "다음 품목 불러오기" }),
    ).not.toBeInTheDocument();
  });

  it("opens a linked raw item even when it is absent from the unmatched page", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/unmatched")) {
          return jsonResponse({ items: [], next_cursor: null, limit: 50 });
        }
        if (url.includes("/raw-items/7/candidates")) {
          return jsonResponse(candidate);
        }
        throw new Error(`unexpected request: ${url}`);
      }),
    );

    renderApp("/grouping?raw_item_id=7");

    expect(
      await screen.findByRole("heading", { name: "BEARING" }),
    ).toBeVisible();
    expect(screen.getByText("딥링크로 연 원천행")).toBeVisible();
  });

  it("keeps a saved decision successful when loading the next page fails", async () => {
    let nextAttempts = 0;
    let membershipWrites = 0;
    const second = {
      ...unmatched.items[0],
      raw_item_id: 8,
      name: "SENSOR",
      spec: "PX-01",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/unmatched") && url.includes("after_id=7")) {
          nextAttempts += 1;
          return nextAttempts === 1
            ? Promise.reject(new Error("next page unavailable"))
            : jsonResponse({ items: [second], next_cursor: null, limit: 50 });
        }
        if (url.includes("/unmatched")) {
          return jsonResponse({
            items: unmatched.items,
            next_cursor: 7,
            limit: 50,
          });
        }
        if (url.includes("/raw-items/7/candidates")) {
          return jsonResponse(candidate);
        }
        if (url.includes("/raw-items/8/candidates")) {
          return jsonResponse({
            ...candidate,
            raw_item: { ...candidate.raw_item, id: 8, name: "SENSOR" },
            normalized: { ...candidate.normalized, name: "SENSOR" },
            candidates: [],
          });
        }
        if (url.includes("/memberships") && init?.method === "POST") {
          membershipWrites += 1;
          return jsonResponse({ id: 91, status: "MATCHED" }, { status: 201 });
        }
        throw new Error(`unexpected request: ${url}`);
      }),
    );
    const user = userEvent.setup();
    renderApp("/grouping");

    await user.click(await screen.findByRole("button", { name: /BEARING/ }));
    await user.type(screen.getByLabelText("후보 검토자"), "buyer-1");
    await user.type(screen.getByLabelText("후보 판정 근거"), "근거 확인");
    await user.click(screen.getByRole("button", { name: "표준품목으로 확정" }));

    expect(
      await screen.findByText("저장 완료, 다음 목록 로드 실패"),
    ).toBeVisible();
    expect(membershipWrites).toBe(1);
    await user.click(
      screen.getByRole("button", { name: "다음 목록 다시 불러오기" }),
    );
    expect(
      await screen.findByRole("button", { name: /SENSOR/ }),
    ).toHaveFocus();
    expect(membershipWrites).toBe(1);
  });

  it("creates an item, edits metadata, and locks stale 409 writes", async () => {
    let atomicAttempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/unmatched")) return jsonResponse(unmatched);
        if (url.includes("/candidates")) {
          return jsonResponse({ ...candidate, candidates: [] });
        }
        if (url.includes("/raw-items/7/standard-item")) {
          atomicAttempts += 1;
          expect(JSON.parse(String(init?.body))).toMatchObject({
            canonical_name: "BEARING",
            expected_current_decision_id: null,
          });
          return jsonResponse(
            {
              detail: {
                error_code: "STALE_CATALOG_DECISION",
                message: "catalog decision changed",
                current_decision_id: 99,
              },
            },
            { status: 409 },
          );
        }
        if (url.includes("/metadata")) {
          const body = JSON.parse(String(init?.body));
          expect(body.expected_current_version_id).toBeNull();
          return jsonResponse(
            {
              id: 30,
              source_document_id: 3,
              version_number: 1,
              supplier_name: "KBC",
              quote_date: "2026-07-01",
              project_name: null,
              decided_by: "buyer-2",
              reason_detail: "원본 헤더 확인",
              created_at: "2026-07-26T10:00:00",
            },
            { status: 201 },
          );
        }
        throw new Error(`unexpected request: ${url}`);
      }),
    );
    const user = userEvent.setup();
    renderApp("/grouping");
    await user.click(
      await screen.findByRole("button", { name: /BEARING/ }),
    );

    const metadata = screen.getByRole("region", { name: "문서 메타데이터" });
    await user.type(within(metadata).getByLabelText("공급사"), "KBC");
    await user.type(within(metadata).getByLabelText("견적일"), "2026-07-01");
    await user.type(within(metadata).getByLabelText("메타데이터 검토자"), "buyer-2");
    await user.type(
      within(metadata).getByLabelText("변경 근거"),
      "원본 헤더 확인",
    );
    await user.click(within(metadata).getByRole("button", { name: "메타데이터 저장" }));
    expect(await screen.findByText("문서 메타데이터를 저장했습니다.")).toBeVisible();

    const create = screen.getByRole("region", { name: "새 표준품목" });
    await user.clear(within(create).getByLabelText("표준 품명"));
    await user.type(within(create).getByLabelText("표준 품명"), "BEARING");
    await user.clear(within(create).getByLabelText("표준 사양"));
    await user.type(within(create).getByLabelText("표준 사양"), "6204 ZZ");
    await user.clear(within(create).getByLabelText("표준 단위"));
    await user.type(within(create).getByLabelText("표준 단위"), "EA");
    await user.type(within(create).getByLabelText("신규품목 검토자"), "buyer-2");
    await user.type(within(create).getByLabelText("신규품목 판정 근거"), "신규 표준품목");
    await user.click(within(create).getByRole("button", { name: "생성 후 확정" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "다른 검토자가 먼저 변경했습니다",
    );
    expect(atomicAttempts).toBe(1);
    expect(
      within(create).getByRole("button", { name: "생성 후 확정" }),
    ).toBeDisabled();
  });
});
