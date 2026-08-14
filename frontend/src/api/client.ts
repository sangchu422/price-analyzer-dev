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
  reason_evidence: ReasonEvidence | null;
  extraction_confidence?: number | null;
  calculation_factors?: Array<{ label: string; coordinate: string; value: string }>;
  calculated_amount?: string | null;
  difference_amount?: string | null;
  difference_percent?: string | null;
  spec_source_status: "PRESENT" | "SOURCE_BLANK" | "PARSER_UNMAPPED" | "UNKNOWN";
  decision: Decision;
  source: SourceEvidence;
  document_group_count?: number;
}

export interface ReasonEvidenceObservation {
  raw_item_id: number;
  clean_decision_id: number;
  unit_price: string;
  source?: {
    document_id: number;
    logical_name: string;
    variant_id: number;
    file_name: string;
    sheet: string | null;
    page: number | null;
    row: number | null;
    cells: string | null;
  } | null;
}

export interface ReasonEvidence {
  kind: "UNIT_PRICE_DISTRIBUTION" | string;
  current_unit_price?: string | null;
  median_unit_price?: string | null;
  variance_percent?: string | null;
  observation_count?: number;
  observations?: ReasonEvidenceObservation[];
  original_quantity?: string | null;
  original_unit?: string | null;
  original_unit_price?: string | null;
  displayed_amount?: string | null;
  calculated_amount?: string | null;
  difference_amount?: string | null;
  difference_percent?: string | null;
  tolerance_amount?: string | null;
  comparison?: "CALCULATED_MINUS_DISPLAYED" | string;
  formula?: string | null;
  factors?: Array<{ label: string; coordinate: string; value: string }>;
  matches?: boolean;
}

export interface SourcePreviewRow {
  row_number: number;
  cells: Array<{
    coordinate: string;
    value: string | null;
    highlighted: boolean;
  }>;
}

export interface SourcePreview {
  kind: "SPREADSHEET" | "PDF" | "FILE";
  file_url: string;
  file_name: string;
  sheet: string | null;
  page: number | null;
  target_cells: string | null;
  header_rows?: SourcePreviewRow[];
  rows: SourcePreviewRow[];
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
  current_price_version_id: number | null;
  captured_price_version_id?: number | null;
  operational_status?:
    | "ACTIVE"
    | "REBUILD_REQUIRED"
    | "NO_ELIGIBLE_EVIDENCE";
  current_version: StandardItemVersion;
  member_count: number;
  observation_count: number | null;
  evidence_quality: EvidenceQuality | null;
  current_price: PriceStatistics | null;
  supplier_summary: string[];
  maker_summary: string[];
  quote_date_start: string | null;
  quote_date_end: string | null;
  quote_date_end_quality?: QuoteDateQuality | null;
  spec_source_status:
    | "PRESENT"
    | "SOURCE_BLANK"
    | "PARSER_UNMAPPED"
    | "MIXED_REVIEW_REQUIRED"
    | "MIXED_SOURCE_VALUES"
    | "UNKNOWN";
  provenance: StandardBuildProvenance | null;
}

export interface StandardItemListResponse {
  items: StandardItemSummary[];
  next_cursor: number | null;
  limit: number;
  latest_build: StandardBuildProvenance | null;
}

export interface SourceCoverageSummary {
  scanned_files: number;
  parsed_files: number;
  standard_price_files: number;
  unparsed_files: number;
  ocr_required_files: number;
  parser_required_files: number;
  recollection_required_files: number;
  recovered_copy_files: number;
  security_release_required_files: number;
  unsupported_files: number;
  raw_item_count: number;
  auto_confirmed_files: number;
  review_required_files: number;
  failed_files: number;
  candidate_count: number;
  accepted_candidate_count: number;
}

export type EvidenceQuality =
  | "SINGLE_OBSERVATION"
  | "MULTI_OBSERVATION";

export type QuoteDateQuality =
  | "CONFIRMED"
  | "REFERENCE_BACKFILL"
  | "FILE_DATE_INFERRED";

export interface StandardBuildProvenance {
  build_run_id: number;
  status: "SUCCEEDED";
  built_at: string;
  rule_version: string;
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
  evidence: Record<string, unknown>;
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
  evidence_quality: EvidenceQuality;
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
  latest_build: StandardBuildProvenance | null;
}

export interface StandardEvidence {
  standard_item_id: number;
  standard_price_version_id: number;
  observation_count: number;
  evidence_quality: EvidenceQuality;
  provenance: StandardBuildProvenance | null;
  observations: Array<{
    raw_item_id: number;
    unit_price: string;
    supplier_name: string | null;
    maker: string | null;
    quote_date: string | null;
    quote_date_quality?: QuoteDateQuality | null;
    source: PriceSource & { cells: string | null };
  }>;
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
  spec_source_status: "PRESENT" | "SOURCE_BLANK" | "PARSER_UNMAPPED" | "UNKNOWN";
  unit: string | null;
  quantity: string | null;
  quote_unit_price: string | null;
  quote_amount: string | null;
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
  standard_observation_count: number | null;
  evidence_quality: EvidenceQuality | null;
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

export type MarketAssessment =
  | "LOW"
  | "WITHIN_RANGE"
  | "REVIEW"
  | "HIGH"
  | "REVIEW_REQUIRED";

export interface MarketProductResult {
  observation_id: number;
  source: "DEVICEMART" | "MOUSER";
  title: string;
  manufacturer: string | null;
  model_number: string | null;
  product_url: string;
  image_url: string | null;
  currency: string;
  applicable_unit_price: string;
  stock_quantity: number | null;
  stock_text: string | null;
  moq: number | null;
  vat_note: string | null;
  shipping_note: string | null;
  collected_at: string;
  expires_at: string;
  is_stale: boolean;
  tiers: Array<{
    minimum_quantity: number;
    unit_price: string;
    currency: string;
  }>;
  image_evidence_url: string | null;
  raw_evidence_url: string;
  screenshot_evidence_url: string | null;
  automatic_price_eligible?: boolean;
  automatic_price_exclusion_reasons?: string[];
}

export interface MarketLookupResult {
  raw_item_id: number;
  query: string;
  quote_unit_price: string | null;
  quantity: string | null;
  cache_state: "CACHE" | "LIVE" | "PARTIAL" | "UNAVAILABLE";
  assessment: MarketAssessment;
  minimum_price: string | null;
  median_price: string | null;
  maximum_price: string | null;
  variance_percent: string | null;
  products: MarketProductResult[];
  source_failures: Array<{
    source: "DEVICEMART" | "MOUSER";
    detail: string;
  }>;
  outcome?:
    | "CACHE_HIT"
    | "LIVE_HIT"
    | "REFERENCE_ONLY"
    | "NO_REFERENCE"
    | "SOURCE_UNAVAILABLE";
  automatic_price_product_count?: number;
}

export type MarketBatchLookupStatus =
  | "STANDARD_APPLIED"
  | "CACHE_HIT"
  | "LIVE_HIT"
  | "REFERENCE_ONLY"
  | "NO_REFERENCE"
  | "SOURCE_UNAVAILABLE"
  | "CLEANING_REQUIRED"
  | "EXCLUDED"
  | "NOT_FOUND";

export interface MarketBatchLookupItem {
  raw_item_id: number;
  status: MarketBatchLookupStatus;
  detail: string | null;
  result?: MarketLookupResult | null;
}

export interface MarketBatchLookupResponse {
  items: MarketBatchLookupItem[];
  completed: number;
  unavailable: number;
}

export interface DocumentAnalysis {
  document: {
    id: number;
    logical_name: string;
    display_name: string;
    purpose: "INCOMING_BID";
  };
  price_policy: {
    within_percent: string;
    high_low_percent: string;
    description: string;
  };
  lines: AnalysisLine[];
  next_cursor: number | null;
  limit: number;
}

export interface TargetPriceEvidence {
  raw_item_id: number;
  metadata_version_id: number;
  source_document_id: number;
  source_variant_id: number;
  source_logical_name: string;
  source_sheet: string | null;
  source_page: number | null;
  source_row: number | null;
  source_cells: string | null;
  quote_date: string;
  source_period?: string | null;
  original_unit_price: string;
  source_index_value?: string | null;
  target_index_value?: string | null;
  adjusted_unit_price: string;
  inflation?: {
    sync_run_id: number;
    latest_confirmed_year: string;
    annual_rates: Array<{ year: string; rate: string }>;
    factor: string;
    cumulative_percent: string;
  } | null;
}

export interface TargetPriceLine {
  raw_item_id: number;
  status:
    | "AVAILABLE"
    | "DATE_UNAVAILABLE"
    | "INDEX_UNAVAILABLE"
    | "RATE_GAP"
    | "MARKET_REFERENCE_REQUIRED"
    | "NOT_APPLICABLE";
  target_unit_price: string | null;
  target_amount: string | null;
  variance_amount: string | null;
  variance_percent: string | null;
  unit_variance_amount?: string | null;
  used_observation_count: number;
  excluded_observation_count: number;
  reason: string;
  evidence: TargetPriceEvidence[];
}

export interface QuoteAnalysisRun extends DocumentAnalysis {
  run_id: number;
  inflation_sync_run_id?: number | null;
  inflation_series_kind?: string | null;
  target_period: string | null;
  target_index_value: string | null;
  inflation_source_url?: string | null;
  inflation_source_last_changed?: string | null;
  quote_total_amount: string | null;
  target_total_amount: string | null;
  target_available_count: number;
  target_unavailable_count: number;
  target_lines: TargetPriceLine[];
}

export interface InflationSeries {
  available: boolean;
  sync_run_id: number | null;
  latest_period: string | null;
  latest_value: string | null;
  source_last_changed: string | null;
  point_count: number;
  org_id: string;
  table_id: string;
  item_id: string;
  classifier_code: string;
  unit: string;
  source_url: string;
}

export interface SubmissionResponse {
  document_id: number;
  sha256: string;
  purpose: "INCOMING_BID";
  parser_name: string;
  parser_version: string;
  status: "INGESTED" | "UNCHANGED";
  raw_item_count: number;
  included_count: number;
  excluded_count: number;
  review_required_count: number;
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
  const isFormData = init?.body instanceof FormData;
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body && !isFormData
        ? { "Content-Type": "application/json" }
        : {}),
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

export function getSourcePreview(
  variantId: number,
  rawItemId: number,
  signal?: AbortSignal,
) {
  return requestJson<SourcePreview>(
    `/api/documents/variants/${variantId}/preview?raw_item_id=${rawItemId}`,
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
  search,
  evidenceQuality,
  signal,
}: {
  afterId?: number;
  search?: string;
  evidenceQuality?: EvidenceQuality;
  signal?: AbortSignal;
} = {}) {
  const params = new URLSearchParams({ limit: "50" });
  if (afterId !== undefined) params.set("after_id", String(afterId));
  if (search) params.set("search", search);
  if (evidenceQuality) params.set("evidence_quality", evidenceQuality);
  return requestJson<StandardItemListResponse>(
    `/api/catalog/standard-items?${params.toString()}`,
    { signal },
  );
}

export function getSourceCoverageSummary(signal?: AbortSignal) {
  return requestJson<SourceCoverageSummary>(
    "/api/catalog/metadata-audit/summary",
    { signal },
  );
}

export function getStandardEvidence({
  standardItemId,
  priceVersionId,
  afterId,
  signal,
}: {
  standardItemId: number;
  priceVersionId: number;
  afterId?: number;
  signal?: AbortSignal;
}) {
  const params = new URLSearchParams({
    limit: "50",
    price_version_id: String(priceVersionId),
  });
  if (afterId !== undefined) params.set("after_id", String(afterId));
  return requestJson<StandardEvidence>(
    `/api/catalog/standard-items/${standardItemId}/evidence?${params.toString()}`,
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
  const params = new URLSearchParams({
    limit: "50",
    include_observations: "false",
  });
  if (afterId !== undefined) params.set("after_id", String(afterId));
  return requestJson<PriceHistory>(
    `/api/pricing/standard-items/${standardItemId}/versions?${params.toString()}`,
    { signal },
  );
}

export function getStandardPriceVersion({
  standardItemId,
  versionId,
  signal,
}: {
  standardItemId: number;
  versionId: number;
  signal?: AbortSignal;
}) {
  return requestJson<PriceVersion>(
    `/api/pricing/standard-items/${standardItemId}/versions/${versionId}`,
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
  limit = 50,
  signal,
}: {
  documentId: number;
  matchStatus?: AnalysisMatchStatus;
  assessment?: AnalysisAssessment;
  afterId?: number;
  limit?: number;
  signal?: AbortSignal;
}) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (matchStatus) params.set("match_status", matchStatus);
  if (assessment) params.set("assessment", assessment);
  if (afterId !== undefined) params.set("after_id", String(afterId));
  return requestJson<DocumentAnalysis>(
    `/api/analysis/documents/${documentId}?${params.toString()}`,
    { signal },
  );
}

export function submitIncomingBid(
  file: File,
  submittedBy: string,
  signal?: AbortSignal,
) {
  const body = new FormData();
  body.append("file", file);
  body.append("submitted_by", submittedBy);
  return requestJson<SubmissionResponse>("/api/submissions", {
    method: "POST",
    body,
    signal,
  });
}

export function lookupMarketPrice(
  rawItemId: number,
  analysisRunId: number,
  forceRefresh = false,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    analysis_run_id: String(analysisRunId),
    force_refresh: String(forceRefresh),
  });
  return requestJson<MarketLookupResult>(
    `/api/market/lookup/${rawItemId}?${params.toString()}`,
    { method: "POST", signal },
  );
}

export function lookupMarketPriceBatch(
  rawItemIds: number[],
  analysisRunId: number,
  forceRefresh = false,
  signal?: AbortSignal,
) {
  return requestJson<MarketBatchLookupResponse>("/api/market/lookup-batch", {
    method: "POST",
    body: JSON.stringify({
      analysis_run_id: analysisRunId,
      raw_item_ids: rawItemIds,
      force_refresh: forceRefresh,
    }),
    signal,
  });
}

export async function getCompleteDocumentAnalysis(
  documentId: number,
  signal?: AbortSignal,
) {
  const lines: AnalysisLine[] = [];
  let afterId: number | undefined;
  let document: DocumentAnalysis["document"] | undefined;
  let pricePolicy: DocumentAnalysis["price_policy"] | undefined;
  do {
    const page = await getDocumentAnalysis({
      documentId,
      afterId,
      limit: 100,
      signal,
    });
    document = page.document;
    pricePolicy = page.price_policy;
    lines.push(...page.lines);
    afterId = page.next_cursor ?? undefined;
  } while (afterId !== undefined);
  return {
    document: document!,
    price_policy: pricePolicy!,
    lines,
    next_cursor: null,
    limit: 100,
  } satisfies DocumentAnalysis;
}

export function createQuoteAnalysisRun({
  documentId,
  createdBy,
  reviewPercent,
  highPercent,
  signal,
}: {
  documentId: number;
  createdBy: string;
  reviewPercent: number;
  highPercent: number;
  signal?: AbortSignal;
}) {
  return requestJson<QuoteAnalysisRun>(
    `/api/analysis/documents/${documentId}/runs`,
    {
      method: "POST",
      body: JSON.stringify({
        created_by: createdBy,
        review_percent: reviewPercent,
        high_percent: highPercent,
      }),
      signal,
    },
  );
}

export function getPpiSeries(signal?: AbortSignal) {
  return requestJson<InflationSeries>(
    "/api/analysis/inflation/series/ppi-all",
    { signal },
  );
}

export function syncPpiSeries(signal?: AbortSignal) {
  return requestJson<InflationSeries>(
    "/api/analysis/inflation/series/ppi-all/sync",
    { method: "POST", signal },
  );
}
