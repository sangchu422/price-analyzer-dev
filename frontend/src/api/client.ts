export type ReviewStatus = "REVIEW_REQUIRED" | "INCLUDED" | "EXCLUDED";
export type ManualDecisionStatus = "INCLUDED" | "EXCLUDED";

export interface DisplayValues {
  item_name: string | null;
  spec: string | null;
  unit: string | null;
  quantity: string | null;
  unit_price: string | null;
  amount: string | null;
  maker: string | null;
}

export interface Decision {
  id: number;
  status: ReviewStatus;
  reason_code: string;
  reason_detail: string | null;
  rule_version: string;
  decided_by: string;
  decided_at: string;
}

export interface SourceEvidence {
  document_id: number;
  logical_name: string;
  variant_id: number;
  path: string;
  sha256: string;
  security_state: string;
  selected_for_parsing_at_ingest: boolean;
  sheet: string | null;
  page: number | null;
  row: number | null;
  cells: string | null;
  parser_name: string;
  parser_version: string;
  parser_warnings: unknown[];
}

export interface ReviewQueueItem {
  raw_item_id: number;
  raw: DisplayValues;
  normalized: DisplayValues;
  reason_code: string;
  reason_detail: string | null;
  decision: Decision;
  source: SourceEvidence;
}

export interface ReviewQueueResponse {
  items: ReviewQueueItem[];
  remaining: number;
  limit: number;
  next_cursor: number | null;
}

export interface ManualDecisionRequest {
  status: ManualDecisionStatus;
  reason_code: "MANUAL_REVIEW";
  reason_detail: string;
  decided_by: string;
  expected_current_decision_id: number;
}

interface ApiErrorDetail {
  error_code?: string;
  message?: string;
  current_decision_id?: number;
}

interface ApiErrorBody {
  detail?: string | ApiErrorDetail;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body: ApiErrorBody | null,
  ) {
    super(message);
    this.name = "ApiError";
  }

  get errorCode() {
    const detail = this.body?.detail;
    return typeof detail === "object" ? detail.error_code : undefined;
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  const body = (await response.json().catch(() => null)) as T | ApiErrorBody | null;
  if (!response.ok) {
    const errorBody = body as ApiErrorBody | null;
    const detail = errorBody?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message ?? `API 요청에 실패했습니다. (${response.status})`;
    throw new ApiError(message, response.status, errorBody);
  }
  return body as T;
}

export function getReviewQueue(afterId?: number, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: "50" });
  if (afterId !== undefined) params.set("after_id", String(afterId));
  return requestJson<ReviewQueueResponse>(
    `/api/cleansing/review-queue?${params.toString()}`,
    { signal },
  );
}

export function submitManualDecision(
  rawItemId: number,
  body: ManualDecisionRequest,
) {
  return requestJson<Decision>(`/api/cleansing/${rawItemId}/decisions`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
