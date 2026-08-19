import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { jsonResponse, renderApp } from "../test/renderApp";

afterEach(() => vi.unstubAllGlobals());

const source = {
  document_id: 91,
  logical_name: "신규견적.xlsx",
  variant_id: 8,
  path: "submissions/new.xlsx",
  sha256: "a".repeat(64),
  sheet: "Sheet1",
  page: null,
  row: 12,
  cells: "A12:G12",
  parser_name: "xlsx",
  parser_version: "reader-v1",
};

function line(
  id: number,
  assessment:
    | "HIGH"
    | "WITHIN_RANGE"
    | "REVIEW"
    | "REVIEW_REQUIRED"
    | "NOT_APPLICABLE",
  overrides: Record<string, unknown> = {},
) {
  const matched = assessment !== "REVIEW_REQUIRED";
  return {
    raw_item_id: id,
    item_name: `ITEM ${id}`,
    spec: `SPEC-${id}`,
    spec_source_status: "PRESENT",
    unit: "EA",
    quantity: "2.000000",
    quote_unit_price: "130.000000",
    quote_amount: "260.000000",
    match_status: matched ? "MATCHED" : "NO_MATCH",
    assessment,
    reference_price: matched ? "100.000000" : null,
    minimum_price: matched ? "90.000000" : null,
    average_price: matched ? "100.000000" : null,
    maximum_price: matched ? "110.000000" : null,
    variance_amount: matched ? "30.000000" : null,
    variance_percent: matched ? "30.000000" : null,
    clean_decision_id: id,
    membership_decision_id: null,
    standard_item_id: matched ? id : null,
    standard_item_version_id: matched ? id : null,
    canonical_name: matched ? `STANDARD ${id}` : null,
    canonical_spec: matched ? `SPEC-${id}` : null,
    canonical_unit: matched ? "EA" : null,
    standard_price_version_id: matched ? id : null,
    standard_price_item_version_id: matched ? id : null,
    standard_observation_count: matched ? 2 : null,
    evidence_quality: matched ? "MULTI_OBSERVATION" : null,
    market_price_lookup_required: !matched,
    market_price_lookup_status: matched
      ? "NOT_REQUIRED"
      : "FUTURE_MARKET_LOOKUP",
    candidates: [],
    source: { ...source, row: id + 10 },
    ...overrides,
  };
}

const analysis = {
  document: {
    id: 91,
    logical_name: "신규견적.xlsx",
    display_name: "신규견적.xlsx",
    purpose: "INCOMING_BID",
  },
  lines: [
    line(1, "HIGH"),
    line(2, "HIGH"),
    ...Array.from({ length: 5 }, (_, index) =>
      line(index + 3, "WITHIN_RANGE"),
    ),
    line(9, "REVIEW", {
      reference_price: "100.000000",
      variance_amount: "15.000000",
      variance_percent: "15.000000",
    }),
    line(8, "REVIEW_REQUIRED", {
      item_name: "SERVO MOTOR",
      spec: "SGMAH-04AAA61",
      quote_amount: null,
    }),
  ],
  next_cursor: null,
  limit: 100,
  price_policy: {
    within_percent: "10.000000",
    high_low_percent: "20.000000",
    description:
      "표준 대비 ±10% 이내 적정, ±10% 초과~±20% 주의, ±20% 초과 고가·저가",
  },
  run_id: 14,
  target_period: "2025",
  target_index_value: null,
  inflation_sync_run_id: 22,
  inflation_series_kind: "CPI_ALL",
  inflation_source_url:
    "https://kosis.kr/statHtml/statHtml.do?orgId=101&tblId=DT_1J22041",
  inflation_source_last_changed: "2025-12-31",
  quote_total_amount: "2080.000000",
  target_total_amount: "1260.000000",
  target_available_count: 7,
  target_unavailable_count: 2,
  target_lines: Array.from({ length: 9 }, (_, index) => ({
    raw_item_id: index + 1,
    status: index < 7 ? "AVAILABLE" : "DATE_UNAVAILABLE",
    target_unit_price: index < 7 ? "90.000000" : null,
    target_amount: index < 7 ? "180.000000" : null,
    variance_amount: index < 7 ? "80.000000" : null,
    variance_percent: index < 7 ? "44.444444" : null,
    unit_variance_amount: index < 7 ? "40.000000" : null,
    used_observation_count: index < 7 ? 2 : 0,
    excluded_observation_count: index < 7 ? 0 : 2,
    reason: index < 7
      ? "원본 날짜가 확인된 과거 단가 2건을 보정했습니다."
      : "원본 본문·머리말에서 확인된 견적일이 없습니다.",
    evidence: index === 0
      ? [
          {
            raw_item_id: 1,
            metadata_version_id: 11,
            source_document_id: 3,
            source_variant_id: 8,
            source_logical_name: "과거견적/원본.xlsx",
            source_sheet: "Sheet1",
            source_page: null,
            source_row: 12,
            source_cells: "A12:G12",
            quote_date: "2025-06-15",
            source_period: "2016",
            original_unit_price: "80.000000",
            source_index_value: null,
            target_index_value: null,
            adjusted_unit_price: "86.686667",
            inflation: {
              sync_run_id: 22,
              latest_confirmed_year: "2025",
              annual_rates: [
                { year: "2017", rate: "1.900000" },
                { year: "2018", rate: "1.500000" },
              ],
              factor: "1.034285",
              cumulative_percent: "3.428500",
            },
          },
        ]
      : [],
  })),
};

function successfulSubmission() {
  return {
    document_id: 91,
    sha256: "b".repeat(64),
    purpose: "INCOMING_BID",
    parser_name: "xlsx",
    parser_version: "reader-v1",
    status: "INGESTED",
    raw_item_count: 9,
    included_count: 9,
    excluded_count: 0,
    review_required_count: 0,
  };
}

it("uploads a new bid first and renders the complete assessment workspace", async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url === "/api/submissions") {
        return jsonResponse(successfulSubmission(), { status: 201 });
      }
      if (url.includes("/api/analysis/documents/91")) {
        return jsonResponse(analysis);
      }
      if (url === "/api/market/lookup-batch") {
        return jsonResponse({
          items: [
            {
              raw_item_id: 8,
              status: "NO_REFERENCE",
              detail: "검색 가능한 시장가가 없습니다.",
              result: null,
            },
          ],
          completed: 0,
          unavailable: 1,
        });
      }
      throw new Error(`unexpected request: ${url}`);
    }),
  );
  const user = userEvent.setup();
  renderApp("/analysis");

  expect(
    screen.getByRole("heading", { name: "신규 견적 분석" }),
  ).toBeVisible();
  expect(screen.queryByLabelText("기존 견적 선택")).not.toBeInTheDocument();
  expect(screen.getByPlaceholderText("예: 홍길동")).toBeVisible();
  expect(screen.getByText("미입력 시 익명으로 기록됩니다.")).toBeVisible();
  await user.upload(
    screen.getByLabelText("신규 견적서"),
    new File(["quote"], "신규견적.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }),
  );
  await user.type(screen.getByLabelText("접수자"), "설비구매팀");
  await user.click(screen.getByRole("button", { name: "견적 분석 시작" }));

  expect(
    await screen.findByRole("heading", { name: "신규견적.xlsx" }),
  ).toBeVisible();
  expect(
    await screen.findByText("시장가 자동 조회 완료 0건 · 불가 1건"),
  ).toBeVisible();
  expect(calls.map((call) => call.url)).toEqual([
    "/api/submissions",
    "/api/analysis/documents/91/runs",
    "/api/market/lookup-batch",
  ]);
  const batch = calls.find((call) => call.url === "/api/market/lookup-batch");
  expect(JSON.parse(String(batch?.init?.body))).toEqual({
    analysis_run_id: 14,
    raw_item_ids: [8],
    force_refresh: false,
  });
  const upload = calls[0];
  expect(upload.init?.method).toBe("POST");
  expect(upload.init?.body).toBeInstanceOf(FormData);
  expect(upload.init?.headers).not.toMatchObject({
    "Content-Type": "application/json",
  });
  expect(screen.getByText("총 9개 품목")).toBeVisible();
  expect(screen.getByText("고가 2건")).toBeVisible();
  expect(screen.getByText("적정 5건")).toBeVisible();
  expect(screen.getByText("주의 1건")).toBeVisible();
  expect(screen.getByText("시장가 확인 필요 1건")).toBeVisible();
  expect(screen.getByText("DeviceMart 캐시 우선 조회")).toBeVisible();
  expect(screen.getByRole("columnheader", { name: "개당 단가" })).toBeVisible();
  expect(screen.getByRole("columnheader", { name: "구매 금액" })).toBeVisible();
  expect(screen.getByRole("columnheader", { name: "참조 최저·중앙값·최고" })).toBeVisible();
  expect(screen.getAllByText("2 EA").length).toBeGreaterThan(0);
  expect(screen.queryByText("EA · —")).not.toBeInTheDocument();
  const servo = screen.getByRole("row", { name: /SERVO MOTOR/ });
  expect(within(servo).getByText("시장가 확인 필요")).toBeVisible();
  expect(within(servo).getByText(/시장가 근거 없음/)).toBeVisible();
  expect(within(servo).getByText("판정 대기")).toBeVisible();
  expect(within(servo).queryByText("0원")).not.toBeInTheDocument();

  await user.click(screen.getByRole("tab", { name: /구매 목표가/ }));
  expect(
    screen.getByRole("columnheader", { name: "협상 목표 단가(개당)" }),
  ).toBeVisible();
  expect(
    screen.getByRole("columnheader", { name: "네고 가능금액" }),
  ).toBeVisible();
  const targetTable = document.querySelector(".target-price-table");
  expect(targetTable).not.toBeNull();
  const targetItem1Row = within(targetTable as HTMLElement).getByRole("row", {
    name: /ITEM 1/,
  });
  const targetItem1Cells = within(targetItem1Row).getAllByRole("cell");
  expect(targetItem1Cells[6]).toHaveTextContent("80원");
  const totalRow = document.querySelector(".target-total-row");
  expect(totalRow).not.toBeNull();
  expect(totalRow).toHaveTextContent("2,080원");
  expect(totalRow).toHaveTextContent("1,520원");
  expect(totalRow).toHaveTextContent("560원");
  expect(screen.queryByText("물가보정 기준")).not.toBeInTheDocument();
  await user.click(screen.getByText("구매 목표가 산정 방식 보기"));
  expect(screen.getByText(/실제 확인된 최저 단가를 물가 보정해 협상 목표/)).toBeVisible();
  expect(screen.getByText(/2025년 확정 소비자물가까지 보정한 뒤 가장 낮은 단가/)).toBeVisible();
  expect(screen.getByText("전체 품목의 77.8%")).toBeVisible();
  expect(screen.getByRole("link", { name: "KOSIS 공식 통계 보기" })).toHaveAttribute(
    "href",
    expect.stringContaining("DT_1J22041"),
  );
  await user.click(screen.getAllByText("최저가 근거 · 독립 원본 2건")[0]);
  expect(screen.getByText("협상 목표로 채택")).toBeVisible();
  expect(screen.getByText(/보정계수 ×1.034285/)).toBeVisible();
  expect(document.querySelector(".inflation-evidence-detail")).toHaveTextContent(
    "2017년 1.9% · 2018년 1.5% · 누적 +3.43%",
  );
});

it("renders comparison basis, signed variance, and every operational status distinctly", async () => {
  const statusLines = [
    line(21, "HIGH", {
      item_name: "MATCHED ITEM",
      reference_price: "101.000000",
      variance_amount: "30.000000",
      variance_percent: "30.000000",
    }),
    line(22, "REVIEW_REQUIRED", {
      item_name: "NO PRICE ITEM",
      match_status: "MATCHED_NO_PRICE",
      standard_item_id: 22,
      standard_price_version_id: null,
      market_price_lookup_required: true,
      market_price_lookup_status: "FUTURE_MARKET_LOOKUP",
    }),
    line(23, "REVIEW_REQUIRED", {
      item_name: "NO MATCH ITEM",
      match_status: "NO_MATCH",
      standard_item_id: null,
      market_price_lookup_required: true,
      market_price_lookup_status: "FUTURE_MARKET_LOOKUP",
    }),
    line(24, "REVIEW_REQUIRED", {
      item_name: "CANDIDATE ITEM",
      match_status: "CANDIDATE",
      standard_item_id: null,
      market_price_lookup_required: true,
      market_price_lookup_status: "FUTURE_MARKET_LOOKUP",
      candidates: [
        {
          standard_item_id: 44,
          standard_item_version_id: 45,
          canonical_name: "CANDIDATE STANDARD",
          canonical_spec: "SPEC-24",
          canonical_unit: "EA",
          final_score: "0.91",
          method: "LEXICAL",
          matched_tokens: ["SPEC-24"],
          embedding_status: "DISABLED",
          embedding_model: null,
        },
      ],
    }),
    line(25, "NOT_APPLICABLE", {
      item_name: "EXCLUDED ITEM",
      match_status: "EXCLUDED",
      standard_item_id: null,
      market_price_lookup_required: false,
      market_price_lookup_status: "NOT_REQUIRED",
    }),
    line(26, "REVIEW_REQUIRED", {
      item_name: "CLEAN REVIEW ITEM",
      match_status: "REVIEW_REQUIRED",
      standard_item_id: null,
      market_price_lookup_required: false,
      market_price_lookup_status: "NOT_REQUIRED",
    }),
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) =>
      String(input) === "/api/submissions"
        ? jsonResponse(
            { ...successfulSubmission(), raw_item_count: 6 },
            { status: 201 },
          )
        : jsonResponse({ ...analysis, lines: statusLines }),
    ),
  );
  const user = userEvent.setup();
  renderApp("/analysis");
  await user.upload(
    screen.getByLabelText("신규 견적서"),
    new File(["quote"], "statuses.xlsx"),
  );
  await user.type(screen.getByLabelText("접수자"), "buyer");
  await user.click(screen.getByRole("button", { name: "견적 분석 시작" }));

  expect(await screen.findByText("주의 0건")).toBeVisible();
  const matched = await screen.findByRole("row", { name: /MATCHED ITEM/ });
  expect(within(matched).getByText("표준 DB 근거 매칭")).toBeVisible();
  expect(within(matched).getByText("101원")).toBeVisible();
  expect(within(matched).getByText("+30원")).toBeVisible();
  expect(within(matched).getByText("(+30%)")).toBeVisible();
  expect(
    within(matched).getByRole("link", { name: "표준 가격 근거 보기" }),
  ).toHaveAttribute("href", "/standard-prices?item_id=21&version_id=21");

  const noPrice = screen.getByRole("row", { name: /NO PRICE ITEM/ });
  expect(within(noPrice).getByText("표준단가 없음")).toBeVisible();
  expect(within(noPrice).queryByRole("link")).not.toBeInTheDocument();
  expect(
    within(noPrice).getByRole("button", { name: "시장가 조회" }),
  ).toHaveClass("stable-action");
  const noMatch = screen.getByRole("row", { name: /NO MATCH ITEM/ });
  expect(within(noMatch).getByText("매칭 없음")).toBeVisible();
  expect(within(noMatch).getByRole("button", { name: "시장가 조회" })).toBeVisible();
  expect(within(noMatch).queryByText(/조회 완료/)).not.toBeInTheDocument();
  expect(
    within(screen.getByRole("row", { name: /CANDIDATE ITEM/ }))
      .getByText("유사 후보 검토"),
  ).toBeVisible();
  expect(
    within(screen.getByRole("row", { name: /CANDIDATE ITEM/ }))
      .getByRole("button", { name: "시장가 조회" }),
  ).toBeVisible();
  expect(
    within(screen.getByRole("row", { name: /EXCLUDED ITEM/ }))
      .getByText("정제 제외"),
  ).toBeVisible();
  expect(
    within(screen.getByRole("row", { name: /CLEAN REVIEW ITEM/ }))
      .getByText("정제 판정대기"),
  ).toBeVisible();
});

it("applies a collected market assessment to the row and overall summary", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/submissions") {
        return jsonResponse(successfulSubmission(), { status: 201 });
      }
      if (url.includes("/api/analysis/documents/91")) {
        return jsonResponse(analysis);
      }
      if (url.includes("/api/market/lookup/8")) {
        return jsonResponse({
          raw_item_id: 8,
          query: "SERVO MOTOR SGMAH-04AAA61",
          quote_unit_price: "130",
          quantity: "2",
          cache_state: "LIVE",
          assessment: "HIGH",
          minimum_price: "90",
          median_price: "100",
          maximum_price: "110",
          variance_percent: "30",
          products: [],
          source_failures: [],
        });
      }
      throw new Error(`unexpected request: ${url}`);
    }),
  );
  renderApp("/analysis");

  await userEvent.upload(
    screen.getByLabelText("신규 견적서"),
    new File(["quote"], "신규견적.xlsx"),
  );
  await userEvent.type(screen.getByLabelText("접수자"), "설비구매팀");
  await userEvent.click(screen.getByRole("button", { name: "견적 분석 시작" }));
  await screen.findByRole("heading", { name: "신규견적.xlsx" });
  const row = screen.getByRole("row", { name: /SERVO MOTOR/ });
  await userEvent.click(
    within(row).getByRole("button", { name: "시장가 조회" }),
  );

  expect(await within(row).findByText("시장가 대비 고가")).toBeVisible();
  expect(within(row).getByText("100원")).toBeVisible();
  expect(screen.getByText("고가 3건")).toBeVisible();
  expect(screen.getByText("시장가 확인 필요 0건")).toBeVisible();
});

it("shows a busy label on the market panel while a refresh is in flight", async () => {
  let resolveRefresh!: (response: Response) => void;
  const refreshResponse = new Promise<Response>((resolve) => {
    resolveRefresh = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/submissions") {
        return jsonResponse(successfulSubmission(), { status: 201 });
      }
      if (url.includes("/api/analysis/documents/91")) {
        return jsonResponse(analysis);
      }
      if (url.includes("/api/market/lookup/8")) {
        if (url.includes("force_refresh=true")) {
          return refreshResponse;
        }
        return jsonResponse({
          raw_item_id: 8,
          query: "SERVO MOTOR SGMAH-04AAA61",
          quote_unit_price: "130",
          quantity: "2",
          cache_state: "LIVE",
          assessment: "HIGH",
          minimum_price: "90",
          median_price: "100",
          maximum_price: "110",
          variance_percent: "30",
          products: [],
          source_failures: [],
        });
      }
      throw new Error(`unexpected request: ${url}`);
    }),
  );
  renderApp("/analysis");

  await userEvent.upload(
    screen.getByLabelText("신규 견적서"),
    new File(["quote"], "신규견적.xlsx"),
  );
  await userEvent.type(screen.getByLabelText("접수자"), "설비구매팀");
  await userEvent.click(screen.getByRole("button", { name: "견적 분석 시작" }));
  await screen.findByRole("heading", { name: "신규견적.xlsx" });
  const row = screen.getByRole("row", { name: /SERVO MOTOR/ });
  await userEvent.click(
    within(row).getByRole("button", { name: "시장가 조회" }),
  );
  await within(row).findByText("시장가 대비 고가");

  await userEvent.click(
    screen.getByRole("button", { name: "실시간 갱신" }),
  );

  expect(screen.getByRole("button", { name: "갱신 중…" })).toBeDisabled();

  resolveRefresh(
    await jsonResponse({
      raw_item_id: 8,
      query: "SERVO MOTOR SGMAH-04AAA61",
      quote_unit_price: "130",
      quantity: "2",
      cache_state: "LIVE",
      assessment: "REVIEW",
      minimum_price: "90",
      median_price: "100",
      maximum_price: "110",
      variance_percent: "10",
      products: [],
      source_failures: [],
    }),
  );

  expect(await within(row).findByText("시장가 대비 주의")).toBeVisible();
});

it("treats a market-sourced REVIEW assessment as 주의, not 판정 대기", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/submissions") {
        return jsonResponse(successfulSubmission(), { status: 201 });
      }
      if (url.includes("/api/analysis/documents/91")) {
        return jsonResponse(analysis);
      }
      if (url.includes("/api/market/lookup/8")) {
        return jsonResponse({
          raw_item_id: 8,
          query: "SERVO MOTOR SGMAH-04AAA61",
          quote_unit_price: "130",
          quantity: "2",
          cache_state: "LIVE",
          assessment: "REVIEW",
          minimum_price: "90",
          median_price: "115",
          maximum_price: "120",
          variance_percent: "13",
          products: [],
          source_failures: [],
        });
      }
      throw new Error(`unexpected request: ${url}`);
    }),
  );
  renderApp("/analysis");

  await userEvent.upload(
    screen.getByLabelText("신규 견적서"),
    new File(["quote"], "신규견적.xlsx"),
  );
  await userEvent.type(screen.getByLabelText("접수자"), "설비구매팀");
  await userEvent.click(screen.getByRole("button", { name: "견적 분석 시작" }));
  await screen.findByRole("heading", { name: "신규견적.xlsx" });
  const row = screen.getByRole("row", { name: /SERVO MOTOR/ });
  await userEvent.click(
    within(row).getByRole("button", { name: "시장가 조회" }),
  );

  expect(await within(row).findByText("시장가 대비 주의")).toBeVisible();
  expect(within(row).queryByText("판정 대기")).not.toBeInTheDocument();
  expect(screen.getByText("주의 2건")).toBeVisible();
  expect(screen.getByText("시장가 확인 필요 0건")).toBeVisible();
});

it("validates required inputs before making a request", async () => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp("/analysis");

  await user.click(screen.getByRole("button", { name: "견적 분석 시작" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "견적서 파일을 선택해 주세요.",
  );
  expect(fetchMock).not.toHaveBeenCalled();
});

it("uses 익명 when the submitter is left blank", async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url === "/api/submissions") {
        return jsonResponse(successfulSubmission(), { status: 201 });
      }
      if (url === "/api/analysis/documents/91/runs") {
        return jsonResponse(analysis);
      }
      throw new Error(`unexpected request: ${url}`);
    }),
  );
  const user = userEvent.setup();
  renderApp("/analysis");

  await user.upload(
    screen.getByLabelText("신규 견적서"),
    new File(["quote"], "anonymous.xlsx"),
  );
  await user.click(screen.getByRole("button", { name: "견적 분석 시작" }));

  await screen.findByRole("heading", { name: "신규견적.xlsx" });
  const uploadBody = calls[0].init?.body as FormData;
  expect(uploadBody.get("submitted_by")).toBe("익명");
  expect(JSON.parse(String(calls[1].init?.body))).toMatchObject({
    created_by: "익명",
  });
});

it("shows a structured upload error and retries without clearing inputs", async () => {
  let attempt = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/submissions") {
        attempt += 1;
        return attempt === 1
          ? jsonResponse(
              {
                detail: {
                  error_code: "UNSUPPORTED_LAYOUT",
                  message: "견적서 표 구조를 인식하지 못했습니다.",
                },
              },
              { status: 422 },
            )
          : jsonResponse(successfulSubmission(), { status: 201 });
      }
      return jsonResponse(analysis);
    }),
  );
  const user = userEvent.setup();
  renderApp("/analysis");
  const fileInput = screen.getByLabelText("신규 견적서");
  const submitter = screen.getByLabelText("접수자");
  await user.upload(
    fileInput,
    new File(["quote"], "retry.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }),
  );
  await user.type(submitter, "구매담당");
  await user.click(screen.getByRole("button", { name: "견적 분석 시작" }));

  const error = await screen.findByRole("alert");
  expect(error).toHaveTextContent("UNSUPPORTED_LAYOUT");
  expect(error).toHaveTextContent("견적서 표 구조를 인식하지 못했습니다.");
  expect(submitter).toHaveValue("구매담당");
  expect((fileInput as HTMLInputElement).files?.[0]?.name).toBe("retry.xlsx");

  await user.click(within(error).getByRole("button", { name: "다시 시도" }));
  expect(
    await screen.findByRole("heading", { name: "신규견적.xlsx" }),
  ).toBeVisible();
  expect(attempt).toBe(2);
});

it("retries an analysis run without uploading the accepted quote again", async () => {
  let uploadCount = 0;
  let analysisAttempts = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/submissions") {
        uploadCount += 1;
        return jsonResponse(successfulSubmission(), { status: 201 });
      }
      if (url === "/api/analysis/documents/91/runs") {
        analysisAttempts += 1;
        return analysisAttempts === 1
          ? jsonResponse(
              {
                detail: {
                  error_code: "ANALYSIS_PAGE_FAILED",
                  message: "다음 분석 결과를 불러오지 못했습니다.",
                },
              },
              { status: 500 },
            )
          : jsonResponse(analysis);
      }
      throw new Error(`unexpected request: ${url}`);
    }),
  );
  const user = userEvent.setup();
  renderApp("/analysis");
  const fileInput = screen.getByLabelText("신규 견적서");
  const submitter = screen.getByLabelText("접수자");
  await user.upload(fileInput, new File(["quote"], "retry-analysis.xlsx"));
  await user.type(submitter, "buyer");
  await user.click(screen.getByRole("button", { name: "견적 분석 시작" }));

  const error = await screen.findByRole("alert");
  expect(error).toHaveTextContent("ANALYSIS_PAGE_FAILED");
  expect(submitter).toHaveValue("buyer");
  expect((fileInput as HTMLInputElement).files?.[0]?.name).toBe(
    "retry-analysis.xlsx",
  );

  await user.click(within(error).getByRole("button", { name: "다시 시도" }));

  expect(
    await screen.findByRole("heading", { name: "신규견적.xlsx" }),
  ).toBeVisible();
  expect(uploadCount).toBe(1);
  expect(analysisAttempts).toBe(2);
});

it("exposes upload, parsing, and analysis stages and prevents duplicate submits", async () => {
  let resolveSubmission!: (response: Response) => void;
  let resolveAnalysis!: (response: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      if (String(input) === "/api/submissions") {
        return new Promise<Response>((resolve) => {
          resolveSubmission = resolve;
        });
      }
      return new Promise<Response>((resolve) => {
        resolveAnalysis = resolve;
      });
    }),
  );
  const user = userEvent.setup();
  renderApp("/analysis");
  await user.upload(
    screen.getByLabelText("신규 견적서"),
    new File(["quote"], "pending.xlsx"),
  );
  await user.type(screen.getByLabelText("접수자"), "buyer");
  const button = screen.getByRole("button", { name: "견적 분석 시작" });
  await user.click(button);

  expect(button).toBeDisabled();
  expect(screen.getByText("파일 업로드")).toBeVisible();
  expect(screen.getByText("견적서 파싱")).toBeVisible();
  expect(screen.getByText("가격 분석")).toBeVisible();
  expect(screen.getByRole("status")).toHaveTextContent(
    "견적서를 업로드하고 품목을 파싱하는 중입니다.",
  );

  resolveSubmission(
    await jsonResponse(successfulSubmission(), { status: 201 }),
  );
  await waitFor(() =>
    expect(screen.getByRole("status")).toHaveTextContent(
      "표준 DB와 견적 품목을 비교하는 중입니다.",
    ),
  );
  resolveAnalysis(await jsonResponse(analysis));
  expect(
    await screen.findByRole("heading", { name: "신규견적.xlsx" }),
  ).toBeVisible();
});

it("cancels an in-flight upload when the page unmounts", async () => {
  let uploadSignal: AbortSignal | null = null;
  vi.stubGlobal(
    "fetch",
    vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      uploadSignal = init?.signal ?? null;
      return new Promise<Response>(() => undefined);
    }),
  );
  const user = userEvent.setup();
  const view = renderApp("/analysis");
  await user.upload(
    screen.getByLabelText("신규 견적서"),
    new File(["quote"], "cancel.xlsx"),
  );
  await user.type(screen.getByLabelText("접수자"), "buyer");
  await user.click(screen.getByRole("button", { name: "견적 분석 시작" }));
  await waitFor(() => expect(uploadSignal).not.toBeNull());

  view.unmount();

  expect((uploadSignal as AbortSignal | null)?.aborted).toBe(true);
});

it("keeps submitter and thresholds after navigating away and back", async () => {
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
  const user = userEvent.setup();
  renderApp("/analysis");

  await user.type(screen.getByLabelText("접수자"), "설비구매팀");
  await user.clear(screen.getByLabelText("적정 범위(±%)"));
  await user.type(screen.getByLabelText("적정 범위(±%)"), "15");

  await user.click(screen.getByRole("link", { name: "표준 DB" }));
  await waitFor(() => expect(window.location.pathname).toBe("/standard-prices"));

  await user.click(screen.getByRole("link", { name: "신규 견적 분석" }));
  await waitFor(() => expect(window.location.pathname).toBe("/analysis"));

  expect(screen.getByLabelText("접수자")).toHaveValue("설비구매팀");
  expect(screen.getByLabelText("적정 범위(±%)")).toHaveValue(15);
});
