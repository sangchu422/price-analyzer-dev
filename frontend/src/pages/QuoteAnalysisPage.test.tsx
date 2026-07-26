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
      throw new Error(`unexpected request: ${url}`);
    }),
  );
  const user = userEvent.setup();
  renderApp("/analysis");

  expect(
    screen.getByRole("heading", { name: "신규 견적 분석" }),
  ).toBeVisible();
  expect(screen.queryByLabelText("기존 견적 선택")).not.toBeInTheDocument();
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
  expect(calls.map((call) => call.url)).toEqual([
    "/api/submissions",
    "/api/analysis/documents/91?limit=100",
  ]);
  const upload = calls[0];
  expect(upload.init?.method).toBe("POST");
  expect(upload.init?.body).toBeInstanceOf(FormData);
  expect(upload.init?.headers).not.toMatchObject({
    "Content-Type": "application/json",
  });
  expect(screen.getByText("총 9개 품목")).toBeVisible();
  expect(screen.getByText("고가 2건")).toBeVisible();
  expect(screen.getByText("적정 5건")).toBeVisible();
  expect(screen.getByText("가격 검토 1건")).toBeVisible();
  expect(screen.getByText("시장가 확인 필요 1건")).toBeVisible();
  expect(screen.getByText("DeviceMart·Mouser DB/실시간 조회 연동 예정")).toBeVisible();
  const servo = screen.getByRole("row", { name: /SERVO MOTOR/ });
  expect(within(servo).getByText("시장가 확인 필요")).toBeVisible();
  expect(within(servo).getByText("판정대기")).toBeVisible();
  expect(within(servo).queryByText("0원")).not.toBeInTheDocument();
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
      market_price_lookup_required: false,
      market_price_lookup_status: "NOT_REQUIRED",
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
      market_price_lookup_required: false,
      market_price_lookup_status: "NOT_REQUIRED",
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

  expect(await screen.findByText("가격 검토 0건")).toBeVisible();
  const matched = await screen.findByRole("row", { name: /MATCHED ITEM/ });
  expect(within(matched).getByText("표준 DB 근거 매칭")).toBeVisible();
  expect(within(matched).getByText("101원")).toBeVisible();
  expect(within(matched).getByText("+30원")).toBeVisible();
  expect(within(matched).getByText("+30%")).toBeVisible();
  expect(
    within(matched).getByRole("link", { name: "표준 가격 근거 보기" }),
  ).toHaveAttribute("href", "/standard-prices?item_id=21&version_id=21");

  const noPrice = screen.getByRole("row", { name: /NO PRICE ITEM/ });
  expect(within(noPrice).getByText("표준단가 없음")).toBeVisible();
  expect(within(noPrice).queryByRole("link")).not.toBeInTheDocument();
  const noMatch = screen.getByRole("row", { name: /NO MATCH ITEM/ });
  expect(within(noMatch).getByText("매칭 없음")).toBeVisible();
  expect(within(noMatch).getByText("시장가 연동 예정")).toBeVisible();
  expect(within(noMatch).queryByText(/조회 완료/)).not.toBeInTheDocument();
  expect(
    within(screen.getByRole("row", { name: /CANDIDATE ITEM/ }))
      .getByText("유사 후보 검토"),
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

it("validates required inputs before making a request", async () => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  renderApp("/analysis");

  await user.click(screen.getByRole("button", { name: "견적 분석 시작" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "견적서 파일과 접수자를 모두 입력해 주세요.",
  );
  expect(fetchMock).not.toHaveBeenCalled();
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

it("retries analysis pagination without uploading the accepted quote again", async () => {
  let uploadCount = 0;
  let secondPageAttempts = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/submissions") {
        uploadCount += 1;
        return jsonResponse(successfulSubmission(), { status: 201 });
      }
      if (url === "/api/analysis/documents/91?limit=100") {
        return jsonResponse({
          ...analysis,
          lines: analysis.lines.slice(0, 4),
          next_cursor: 4,
        });
      }
      if (url === "/api/analysis/documents/91?limit=100&after_id=4") {
        secondPageAttempts += 1;
        return secondPageAttempts === 1
          ? jsonResponse(
              {
                detail: {
                  error_code: "ANALYSIS_PAGE_FAILED",
                  message: "다음 분석 결과를 불러오지 못했습니다.",
                },
              },
              { status: 500 },
            )
          : jsonResponse({
              ...analysis,
              lines: analysis.lines.slice(4),
              next_cursor: null,
            });
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
  expect(secondPageAttempts).toBe(2);
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
