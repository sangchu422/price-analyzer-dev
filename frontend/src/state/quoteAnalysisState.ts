import { useState } from "react";

import type {
  AnalysisAssessment,
  MarketBatchLookupItem,
  MarketLookupResult,
  QuoteAnalysisRun,
  SubmissionResponse,
} from "../api/client";

export type WorkflowStage = "IDLE" | "PARSING" | "ANALYZING";
export type ResultFilter =
  | "ALL"
  | "MATCHED"
  | "MARKET"
  | "PENDING"
  | AnalysisAssessment;

export type MarketLookupProgressItem =
  | MarketBatchLookupItem
  | { status: "PENDING"; detail: string | null };

export type MarketLookupProgress = {
  state: "PENDING" | "COMPLETE" | "ERROR";
  total: number;
  completed: number;
  unavailable: number;
};

export function useQuoteAnalysisWorkflowState() {
  const [file, setFile] = useState<File | null>(null);
  const [submittedBy, setSubmittedBy] = useState("");
  const [reviewPercent, setReviewPercent] = useState(10);
  const [highPercent, setHighPercent] = useState(20);
  const [stage, setStage] = useState<WorkflowStage>("IDLE");
  const [submission, setSubmission] = useState<SubmissionResponse | null>(null);
  const [analysis, setAnalysis] = useState<QuoteAnalysisRun | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [resultFilter, setResultFilter] = useState<ResultFilter>("ALL");
  const [marketResults, setMarketResults] = useState<
    Record<number, MarketLookupResult>
  >({});
  const [marketLookupItems, setMarketLookupItems] = useState<
    Record<number, MarketLookupProgressItem>
  >({});
  const [marketLookupProgress, setMarketLookupProgress] =
    useState<MarketLookupProgress | null>(null);
  const [validationError, setValidationError] = useState("");

  return {
    file,
    setFile,
    submittedBy,
    setSubmittedBy,
    reviewPercent,
    setReviewPercent,
    highPercent,
    setHighPercent,
    stage,
    setStage,
    submission,
    setSubmission,
    analysis,
    setAnalysis,
    error,
    setError,
    resultFilter,
    setResultFilter,
    marketResults,
    setMarketResults,
    marketLookupItems,
    setMarketLookupItems,
    marketLookupProgress,
    setMarketLookupProgress,
    validationError,
    setValidationError,
  };
}

export type QuoteAnalysisWorkflowState = ReturnType<
  typeof useQuoteAnalysisWorkflowState
>;
