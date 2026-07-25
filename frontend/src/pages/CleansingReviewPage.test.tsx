import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CleansingReviewPage } from "./CleansingReviewPage";

const firstItem = {
  raw_item_id: 7,
  raw: {
    item_name: " BEARING ",
    spec: "6204 ZZ",
    unit: "EA",
    quantity: "2",
    unit_price: "2,800",
    amount: "5,600",
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
  reason_detail: "수량 × 단가와 금액을 확인해 주세요.",
  decision: {
    id: 41,
    status: "REVIEW_REQUIRED",
    reason_code: "AMOUNT_MISMATCH",
    reason_detail: "수량 × 단가와 금액을 확인해 주세요.",
    rule_version: "clean-v1",
    decided_by: "SYSTEM",
    decided_at: "2026-07-25T09:30:00",
  },
  source: {
    document_id: 3,
    logical_name: "260707_러닝랩_견적.xlsx",
    variant_id: 8,
    path: "견적서/260707_러닝랩_견적_보안해제.xlsx",
    sha256: "a".repeat(64),
    security_state: "UNLOCKED",
    selected_for_parsing_at_ingest: true,
    sheet: "견적서",
    page: null,
    row: 12,
    cells: "A12:G12",
    parser_name: "openpyxl-profile",
    parser_version: "1.2.0",
    parser_warnings: [{ code: "FORMULA_VALUE_USED", cell: "G12" }],
  },
};

const secondItem = {
  ...firstItem,
  raw_item_id: 12,
  raw: { ...firstItem.raw, item_name: "SENSOR" },
  normalized: { ...firstItem.normalized, item_name: "SENSOR" },
  reason_code: "PRICE_OUTLIER",
  decision: { ...firstItem.decision, id: 52, reason_code: "PRICE_OUTLIER" },
  source: { ...firstItem.source, row: 19, cells: "A19:G19" },
};

function queue(items = [firstItem], nextCursor: number | null = null) {
  return { items, remaining: items.length, limit: 50, next_cursor: nextCursor };
}

function jsonResponse(body: unknown, init?: ResponseInit) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
      ...init,
    }),
  );
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <CleansingReviewPage />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("CleansingReviewPage", () => {
  it("shows exact source provenance and raw versus normalized values", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse(queue())));
    renderPage();

    expect(await screen.findByRole("heading", { name: "BEARING", level: 1 })).toBeVisible();
    expect(screen.getByText((_, node) => node?.tagName === "DD" && node.textContent === "BEARING")).toBeVisible();
    expect(screen.getByText((_, node) => node?.tagName === "DD" && node.textContent === " BEARING ")).toBeVisible();
    expect(screen.getByText("2,800")).toBeVisible();
    expect(screen.getByText("2800.000000")).toBeVisible();
    expect(screen.getByText("견적서/260707_러닝랩_견적_보안해제.xlsx")).toBeVisible();
    expect(screen.getByText("a".repeat(64))).toBeVisible();
    expect(screen.getByText("견적서 · 12행 · A12:G12")).toBeVisible();
    expect(screen.getByText(/openpyxl-profile 1\.2\.0/)).toBeVisible();
    expect(screen.getByText(/FORMULA_VALUE_USED/)).toBeVisible();
  });

  it.each([
    ["포함", "INCLUDED"],
    ["제외", "EXCLUDED"],
  ])("submits %s with actor, detail, and expected decision id", async (label, status) => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        return jsonResponse(
          { ...firstItem.decision, id: 42, status, reason_code: "MANUAL_REVIEW" },
          { status: 201 },
        );
      }
      return jsonResponse(queue());
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("heading", { name: "BEARING", level: 1 });
    await user.type(screen.getByLabelText("검토자"), "sangwoo");
    await user.type(screen.getByLabelText("판단 근거"), "원본 견적서와 금액을 대조함");
    await user.click(screen.getByRole("button", { name: label }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/cleansing/7/decisions",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            status,
            reason_code: "MANUAL_REVIEW",
            reason_detail: "원본 견적서와 금액을 대조함",
            decided_by: "sangwoo",
            expected_current_decision_id: 41,
          }),
        }),
      ),
    );
    expect(screen.getByText("판단이 저장되었습니다.")).toBeVisible();
  });

  it("refreshes and explains a stale decision conflict", async () => {
    let getCount = 0;
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        return jsonResponse(
          {
            detail: {
              error_code: "STALE_DECISION",
              message: "cleansing decision changed; refresh and retry",
              current_decision_id: 43,
            },
          },
          { status: 409 },
        );
      }
      getCount += 1;
      return jsonResponse(queue([{ ...firstItem, decision: { ...firstItem.decision, id: getCount > 1 ? 43 : 41 } }]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("heading", { name: "BEARING", level: 1 });
    await user.type(screen.getByLabelText("검토자"), "sangwoo");
    await user.type(screen.getByLabelText("판단 근거"), "원본 대조 완료");
    await user.click(screen.getByRole("button", { name: "포함" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "다른 검토자가 먼저 변경했습니다. 최신 내용으로 새로고침했습니다.",
    );
    await waitFor(() => expect(getCount).toBeGreaterThan(1));
  });

  it("disables both actions while one decision is being saved", async () => {
    let resolvePost!: (value: Response) => void;
    let postCount = 0;
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        postCount += 1;
        return new Promise<Response>((resolve) => { resolvePost = resolve; });
      }
      return jsonResponse(queue());
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("heading", { name: "BEARING", level: 1 });
    await user.type(screen.getByLabelText("검토자"), "sangwoo");
    await user.type(screen.getByLabelText("판단 근거"), "원본 대조 완료");
    await user.click(screen.getByRole("button", { name: "포함" }));

    expect(screen.getByRole("button", { name: "포함" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "제외" })).toBeDisabled();
    expect(screen.getByText("판단을 저장하는 중입니다…")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "포함" }));
    expect(postCount).toBe(1);

    resolvePost(
      await jsonResponse(
        { ...firstItem.decision, id: 42, status: "INCLUDED", reason_code: "MANUAL_REVIEW" },
        { status: 201 },
      ),
    );
    expect(await screen.findByText("판단이 저장되었습니다.")).toBeVisible();
  });

  it("keeps selection and decision target inside the filtered result", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse(queue([firstItem, secondItem]))));
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("heading", { name: "BEARING", level: 1 });
    await user.selectOptions(screen.getByRole("combobox", { name: "검토 사유 필터" }), "PRICE_OUTLIER");

    expect(screen.queryByRole("button", { name: /BEARING/ })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "SENSOR", level: 1 })).toBeVisible();
    expect(screen.getByRole("region", { name: "검토 판단" })).toBeVisible();

    await user.selectOptions(screen.getByRole("combobox", { name: "검토 사유 필터" }), "AMOUNT_MISMATCH");
    await user.type(screen.getByRole("searchbox", { name: "품목 또는 파일 검색" }), "없는 품목");
    expect(screen.getByText("검색 조건에 맞는 검토 항목이 없습니다.")).toBeVisible();
    expect(screen.queryByRole("region", { name: "검토 판단" })).not.toBeInTheDocument();
  });

  it("shows filtered empty rather than server empty when a reason filter remains after resolution", async () => {
    let resolvePost!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        return new Promise<Response>((resolve) => { resolvePost = resolve; });
      }
      return jsonResponse(queue([firstItem, secondItem]));
    }));
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("heading", { name: "BEARING", level: 1 });
    await user.selectOptions(
      screen.getByRole("combobox", { name: "검토 사유 필터" }),
      "AMOUNT_MISMATCH",
    );
    await user.type(screen.getByLabelText("검토자"), "sangwoo");
    await user.type(screen.getByLabelText("판단 근거"), "불일치 사유 확인");
    await user.click(screen.getByRole("button", { name: "포함" }));
    resolvePost(
      await jsonResponse(
        { ...firstItem.decision, id: 42, status: "INCLUDED", reason_code: "MANUAL_REVIEW" },
        { status: 201 },
      ),
    );

    expect(await screen.findByText("검색 조건에 맞는 검토 항목이 없습니다.")).toBeVisible();
    expect(screen.getByText("판단이 저장되었습니다.")).toBeVisible();
    expect(screen.getByRole("combobox", { name: "검토 사유 필터" })).toHaveValue(
      "AMOUNT_MISMATCH",
    );
    expect(screen.queryByText("검토할 항목이 없습니다.")).not.toBeInTheDocument();
  });

  it("locks queue selection during save and resolves only the submitted item", async () => {
    let resolvePost!: (value: Response) => void;
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        return new Promise<Response>((resolve) => { resolvePost = resolve; });
      }
      return jsonResponse(queue([firstItem, secondItem]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("heading", { name: "BEARING", level: 1 });
    await user.type(screen.getByLabelText("검토자"), "sangwoo");
    await user.type(screen.getByLabelText("판단 근거"), "A 항목 원본 대조 완료");
    await user.click(screen.getByRole("button", { name: "포함" }));

    const sensorRow = screen.getByRole("button", { name: /SENSOR/ });
    expect(sensorRow).toBeDisabled();
    await user.click(sensorRow);
    expect(screen.getByRole("heading", { name: "BEARING", level: 1 })).toBeVisible();

    resolvePost(
      await jsonResponse(
        { ...firstItem.decision, id: 42, status: "INCLUDED", reason_code: "MANUAL_REVIEW" },
        { status: 201 },
      ),
    );
    expect(await screen.findByRole("heading", { name: "SENSOR", level: 1 })).toBeVisible();
    expect(screen.queryByRole("button", { name: /BEARING/ })).not.toBeInTheDocument();
  });

  it("rejects reserved or overlong manual decision fields without posting", async () => {
    const fetchMock = vi.fn(() => jsonResponse(queue()));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("heading", { name: "BEARING", level: 1 });
    const actor = screen.getByLabelText("검토자");
    const detail = screen.getByLabelText("판단 근거");
    expect(actor).toHaveAttribute("maxlength", "100");
    expect(detail).toHaveAttribute("maxlength", "2000");

    await user.type(actor, "  SyStEm  ");
    await user.type(detail, "원본 대조");
    await user.click(screen.getByRole("button", { name: "포함" }));
    expect(screen.getByText("SYSTEM은 자동 판단 전용 이름입니다.")).toBeVisible();

    fireEvent.change(actor, { target: { value: "가".repeat(101) } });
    fireEvent.change(detail, { target: { value: "나".repeat(2001) } });
    await user.click(screen.getByRole("button", { name: "제외" }));
    expect(screen.getByText("검토자는 100자 이내로 입력해 주세요.")).toBeVisible();
    expect(screen.getByText("판단 근거는 2,000자 이내로 입력해 주세요.")).toBeVisible();
    expect(screen.getByText("101 / 100")).toBeVisible();
    expect(screen.getByText("2,001 / 2,000")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("loads the next cursor page and keeps existing rows", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("after_id=7")) return jsonResponse(queue([secondItem], null));
      return jsonResponse(queue([firstItem], 7));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("heading", { name: "BEARING", level: 1 });
    await user.click(screen.getByRole("button", { name: "다음 항목 불러오기" }));

    expect(await screen.findByRole("button", { name: /SENSOR/ })).toBeVisible();
    expect(screen.getByRole("button", { name: /BEARING/ })).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("after_id=7"),
      expect.anything(),
    );
  });

  it("supports loading, empty, and error states", async () => {
    let resolveFetch!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((resolve) => { resolveFetch = resolve; })));
    const { unmount } = renderPage();
    expect(screen.getByText("검토 항목을 불러오는 중입니다.")).toBeVisible();
    resolveFetch(await jsonResponse(queue([])));
    expect(await screen.findByText("검토할 항목이 없습니다.")).toBeVisible();
    unmount();

    vi.stubGlobal("fetch", vi.fn(() => jsonResponse({ detail: "failure" }, { status: 500 })));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("검토 목록을 불러오지 못했습니다.");
  });

  it("offers labeled filters, keyboard row selection, and validates required decision fields", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse(queue([firstItem, secondItem]))));
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("heading", { name: "BEARING", level: 1 });
    expect(screen.getByRole("searchbox", { name: "품목 또는 파일 검색" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "검토 사유 필터" })).toBeVisible();

    const sensorRow = screen.getByRole("button", { name: /SENSOR/ });
    sensorRow.focus();
    await user.keyboard("{Enter}");
    expect(within(screen.getByRole("region", { name: "선택 항목 상세" })).getByRole("heading", { name: "SENSOR" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "포함" }));
    expect(screen.getByText("검토자를 입력해 주세요.")).toBeVisible();
    expect(screen.getByText("판단 근거를 입력해 주세요.")).toBeVisible();
  });
});
