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
  available_reason_codes: string[];
}

export interface ManualDecisionRequest {
  status: ManualDecisionStatus;
  reason_code: "MANUAL_REVIEW";
  reason_detail: string;
  decided_by: string;
  expected_current_decision_id: number;
}

export interface StandardItemVersion {
  id: number;
  standard_item_id: number;
  version_number: number;
  canonical_name: string;
  canonical_spec: string | null;
  canonical_unit: string | null;
  aliases: string[];
  created_by: string;
  reason_detail: string;
  created_at: string;
}

export interface StandardItemSummary {
  id: number;
  current_version: StandardItemVersion;
  member_count: number;
}

export interface StandardItemListResponse {
  items: StandardItemSummary[];
  next_cursor: number | null;
  limit: number;
}

export interface UnmatchedItem {
  raw_item_id: number;
  name: string | null;
  spec: string | null;
  unit: string | null;
  current_cleansing_decision_id: number;
  current_membership_decision_id: number | null;
}

export interface UnmatchedResponse {
  items: UnmatchedItem[];
  next_cursor: number | null;
  limit: number;
}

export interface DocumentMetadata {
  id: number;
  source_document_id: number;
  version_number: number;
  supplier_name: string | null;
  quote_date: string | null;
  project_name: string | null;
  decided_by: string;
  reason_detail: string;
  created_at: string;
}

export interface CatalogCandidate {
  standard_item_id: number;
  standard_item_version_id: number;
  canonical_name: string;
  canonical_spec: string | null;
  canonical_unit: string | null;
  aliases: string[];
  name_score: string;
  spec_score: string;
  token_score: string;
  embedding_score: string | null;
  embedding_status: "DISABLED" | "UNAVAILABLE" | "AVAILABLE" | "MOCK_ONLY";
  embedding_model: string | null;
  final_score: string;
  matched_tokens: string[];
  method: string;
  unit_compatible: boolean;
  model_tokens_compatible: boolean;
}

export interface CandidateResponse {
  match_status: "CANDIDATE" | "NO_MATCH";
  raw_item: {
    id: number;
    name: string | null;
    spec: string | null;
    unit: string | null;
    quantity: string | null;
    unit_price: string | null;
    amount: string | null;
  };
  normalized: {
    name: string | null;
    spec: string | null;
    unit: string | null;
    quantity: string | null;
    unit_price: string | null;
    amount: string | null;
  };
  current_cleansing_decision: {
    id: number;
    status: ReviewStatus;
    reason_code: string;
    reason_detail: string | null;
    rule_version: string;
  };
  current_membership_decision_id: number | null;
  current_document_metadata: DocumentMetadata | null;
  source: SourceEvidence;
  candidates: CatalogCandidate[];
}

export interface PriceSource {
  document_id: number;
  logical_name: string;
  variant_id: number;
  path: string;
  sheet: string | null;
  page: number | null;
  row: number | null;
}

export interface PriceStatistics {
  minimum: string;
  median: string;
  average: string;
  maximum: string;
}

export interface PriceDraft {
  standard_item_id: number;
  standard_item_version_id: number;
  current_standard_price_version_id: number | null;
  canonical_unit: string | null;
  observation_count: number;
  supplier_count: number;
  latest_quote_date: string | null;
  prices: PriceStatistics;
  observations: Array<{
    raw_item_id: number;
    clean_decision_id: number;
    membership_decision_id: number;
    metadata_version_id: number | null;
    unit_price: string;
    supplier_name: string | null;
    quote_date: string | null;
    source: PriceSource;
  }>;
  exclusions: unknown[];
  context: Record<string, number>;
  calculation_version: string;
  fingerprint: string;
}

export interface PriceVersion {
  id: number;
  standard_item_id: number;
  version_number: number;
  observation_count: number;
  supplier_count: number;
  latest_quote_date: string | null;
  prices: PriceStatistics;
  calculation_version: string;
  audit_status: "CAPTURED" | "LEGACY_BACKFILL";
  draft_fingerprint: string | null;
  standard_item_version: {
    id: number;
    version_number: number;
    canonical_name: string;
    canonical_spec: string | null;
    canonical_unit: string | null;
  } | null;
  excluded_count: number;
  review_required_count: number;
  exclusions: unknown[];
  exclusion_context_valid: boolean;
  exclusion_context_error: string | null;
  approved_by: string;
  approved_at: string;
  observations: Array<{
    raw_item_id: number;
    clean_decision_id: number;
    membership_decision_id: number;
    metadata_version_id: number | null;
    metadata: unknown;
    source: PriceSource;
  }>;
}

export interface PriceHistory {
  standard_item_id: number;
  versions: PriceVersion[];
  next_cursor: number | null;
  limit: number;
}

export type AnalysisMatchStatus =
  | "EXCLUDED"
  | "REVIEW_REQUIRED"
  | "CANDIDATE"
  | "NO_MATCH"
  | "MATCHED_NO_PRICE"
  | "MATCHED";
export type AnalysisAssessment =
  | "NOT_APPLICABLE"
  | "REVIEW_REQUIRED"
  | "LOW"
  | "WITHIN_RANGE"
  | "REVIEW"
  | "HIGH";

export interface AnalysisDocument {
  id: number;
  logical_name: string;
  raw_item_count: number;
  included_count: number;
  excluded_count: number;
  review_required_count: number;
  undecided_count: number;
  analysis_ready: boolean;
}

export interface AnalysisDocumentList {
  items: AnalysisDocument[];
  total: number;
  limit: number;
  offset: number;
  next_cursor: number | null;
}

export interface AnalysisLine {
  raw_item_id: number;
  item_name: string | null;
  spec: string | null;
  unit: string | null;
  quote_unit_price: string | null;
  match_status: AnalysisMatchStatus;
  assessment: AnalysisAssessment;
  reference_price: string | null;
  minimum_price: string | null;
  average_price: string | null;
  maximum_price: string | null;
  variance_amount: string | null;
  variance_percent: string | null;
  clean_decision_id: number | null;
  membership_decision_id: number | null;
  standard_item_id: number | null;
  standard_item_version_id: number | null;
  canonical_name: string | null;
  canonical_spec: string | null;
  canonical_unit: string | null;
  standard_price_version_id: number | null;
  standard_price_item_version_id: number | null;
  market_price_lookup_required: boolean;
  market_price_lookup_status: "NOT_REQUIRED" | "FUTURE_MARKET_LOOKUP";
  candidates: Array<{
    standard_item_id: number;
    standard_item_version_id: number;
    canonical_name: string;
    canonical_spec: string | null;
    canonical_unit: string | null;
    final_score: string;
    method: string;
    matched_tokens: string[];
    embedding_status: string;
    embedding_model: string | null;
  }>;
  source: {
    document_id: number;
    logical_name: string;
    variant_id: number;
    path: string;
    sha256: string;
    sheet: string | null;
    page: number | null;
    row: number | null;
    cells: string | null;
    parser_name: string;
    parser_version: string;
  };
}

export interface DocumentAnalysis {
  document: { id: number; logical_name: string };
  lines: AnalysisLine[];
  next_cursor: number | null;
  limit: number;
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
    return detail !== null &&
      typeof detail === "object" &&
      !Array.isArray(detail)
      ? detail.error_code
      : undefined;
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

export function getReviewQueue({
  afterId,
  search,
  reasonCode,
  signal,
}: {
  afterId?: number;
  search?: string;
  reasonCode?: string;
  signal?: AbortSignal;
} = {}) {
  const params = new URLSearchParams({ limit: "50" });
  if (search) params.set("search", search);
  if (reasonCode) params.set("reason_code", reasonCode);
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

export function getUnmatched({
  afterId,
  signal,
}: {
  afterId?: number;
  signal?: AbortSignal;
} = {}) {
  const params = new URLSearchParams({ limit: "50" });
  if (afterId !== undefined) params.set("after_id", String(afterId));
  return requestJson<UnmatchedResponse>(`/api/catalog/unmatched?${params.toString()}`, {
    signal,
  });
}

export function getCatalogCandidates(rawItemId: number, signal?: AbortSignal) {
  return requestJson<CandidateResponse>(
    `/api/catalog/raw-items/${rawItemId}/candidates`,
    { signal },
  );
}

export function submitMembership(
  rawItemId: number,
  body: {
    standard_item_id: number | null;
    status: "MATCHED" | "REJECTED";
    expected_current_decision_id: number | null;
    candidate_score: string | null;
    method: string;
    evidence: Record<string, unknown>;
    decided_by: string;
    reason_detail: string;
  },
) {
  return requestJson(`/api/catalog/raw-items/${rawItemId}/memberships`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function createAndMatchStandardItem(
  rawItemId: number,
  body: {
  canonical_name: string;
  canonical_spec: string | null;
  canonical_unit: string | null;
  aliases: string[];
  created_by: string;
  reason_detail: string;
  expected_current_decision_id: number | null;
  },
) {
  return requestJson<{
    standard_item: { id: number; current_version: StandardItemVersion };
    membership: { id: number; status: "MATCHED" };
  }>(
    `/api/catalog/raw-items/${rawItemId}/standard-item`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export function saveDocumentMetadata(
  documentId: number,
  body: {
    supplier_name: string | null;
    quote_date: string | null;
    project_name: string | null;
    expected_current_version_id: number | null;
    decided_by: string;
    reason_detail: string;
  },
) {
  return requestJson<DocumentMetadata>(
    `/api/catalog/documents/${documentId}/metadata`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export function getStandardItems({
  afterId,
  signal,
}: {
  afterId?: number;
  signal?: AbortSignal;
} = {}) {
  const params = new URLSearchParams({ limit: "50" });
  if (afterId !== undefined) params.set("after_id", String(afterId));
  return requestJson<StandardItemListResponse>(
    `/api/catalog/standard-items?${params.toString()}`,
    { signal },
  );
}

export function getPriceDraft(standardItemId: number, signal?: AbortSignal) {
  return requestJson<PriceDraft>(
    `/api/pricing/standard-items/${standardItemId}/draft`,
    { signal },
  );
}

export function getStandardPriceVersions({
  standardItemId,
  afterId,
  signal,
}: {
  standardItemId: number;
  afterId?: number;
  signal?: AbortSignal;
}) {
  const params = new URLSearchParams({ limit: "50" });
  if (afterId !== undefined) params.set("after_id", String(afterId));
  return requestJson<PriceHistory>(
    `/api/pricing/standard-items/${standardItemId}/versions?${params.toString()}`,
    { signal },
  );
}

export function approvePrice(
  standardItemId: number,
  body: {
    expected_fingerprint: string;
    expected_current_version_id: number | null;
    approved_by: string;
  },
) {
  return requestJson<PriceVersion>(
    `/api/pricing/standard-items/${standardItemId}/versions`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export function getAnalysisDocuments({
  afterId,
  signal,
}: {
  afterId?: number;
  signal?: AbortSignal;
} = {}) {
  const params = new URLSearchParams({ limit: "50" });
  if (afterId !== undefined) params.set("after_id", String(afterId));
  return requestJson<AnalysisDocumentList>(
    `/api/analysis/documents?${params.toString()}`,
    { signal },
  );
}

export function getDocumentAnalysis({
  documentId,
  matchStatus,
  assessment,
  afterId,
  signal,
}: {
  documentId: number;
  matchStatus?: AnalysisMatchStatus;
  assessment?: AnalysisAssessment;
  afterId?: number;
  signal?: AbortSignal;
}) {
  const params = new URLSearchParams({ limit: "50" });
  if (matchStatus) params.set("match_status", matchStatus);
  if (assessment) params.set("assessment", assessment);
  if (afterId !== undefined) params.set("after_id", String(afterId));
  return requestJson<DocumentAnalysis>(
    `/api/analysis/documents/${documentId}?${params.toString()}`,
    { signal },
  );
}
