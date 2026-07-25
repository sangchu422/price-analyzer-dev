import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  createAndMatchStandardItem,
  getCatalogCandidates,
  getUnmatched,
  saveDocumentMetadata,
  submitMembership,
  type CatalogCandidate,
} from "../api/client";

type Notice = { kind: "success" | "error"; text: string };

export function GroupingReviewPage() {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [resolvedIds, setResolvedIds] = useState<Set<number>>(() => new Set());
  const [notice, setNotice] = useState<Notice | null>(null);
  const [staleLocked, setStaleLocked] = useState(false);
  const unmatched = useQuery({
    queryKey: ["catalog-unmatched"],
    queryFn: ({ signal }) => getUnmatched(signal),
  });
  const items = (unmatched.data?.items ?? []).filter(
    (item) => !resolvedIds.has(item.raw_item_id),
  );
  const selected = items.find((item) => item.raw_item_id === selectedId) ?? null;
  const detail = useQuery({
    queryKey: ["catalog-candidates", selectedId],
    queryFn: ({ signal }) => getCatalogCandidates(selectedId!, signal),
    enabled: selectedId !== null,
  });

  const handleError = (error: unknown) => {
    if (error instanceof ApiError && error.status === 409) {
      setStaleLocked(true);
      setNotice({
        kind: "error",
        text: "다른 검토자가 먼저 변경했습니다. 최신 데이터를 다시 확인해 주세요.",
      });
      void queryClient.invalidateQueries({ queryKey: ["catalog-unmatched"] });
      void queryClient.invalidateQueries({ queryKey: ["catalog-candidates"] });
      return;
    }
    setNotice({
      kind: "error",
      text:
        error instanceof Error
          ? `저장하지 못했습니다: ${error.message}`
          : "저장하지 못했습니다.",
    });
  };

  const membership = useMutation({
    mutationFn: ({
      candidate,
      actor,
      reason,
      status = "MATCHED",
      standardItemId = candidate?.standard_item_id ?? null,
      score = candidate?.final_score ?? null,
      method = candidate ? "MANUAL_CANDIDATE" : "MANUAL_NO_MATCH",
    }: {
      candidate?: CatalogCandidate;
      actor: string;
      reason: string;
      status?: "MATCHED" | "REJECTED";
      standardItemId?: number | null;
      score?: string | null;
      method?: string;
    }) =>
      submitMembership(selectedId!, {
        standard_item_id: standardItemId,
        status,
        expected_current_decision_id:
          detail.data?.current_membership_decision_id ??
          selected?.current_membership_decision_id ??
          null,
        candidate_score: score,
        method,
        evidence: candidate
          ? {
              candidate_standard_item_version_id:
                candidate.standard_item_version_id,
              matched_tokens: candidate.matched_tokens,
              scores: {
                name: candidate.name_score,
                spec: candidate.spec_score,
                token: candidate.token_score,
                embedding: candidate.embedding_score,
                final: candidate.final_score,
              },
              source: detail.data?.source,
            }
          : { source: detail.data?.source },
        decided_by: actor,
        reason_detail: reason,
      }),
    onSuccess: () => {
      setNotice({ kind: "success", text: "그룹핑 판정을 저장했습니다." });
      if (selectedId !== null) {
        setResolvedIds((current) => new Set(current).add(selectedId));
      }
      void queryClient.invalidateQueries({ queryKey: ["catalog-unmatched"] });
    },
    onError: handleError,
  });

  const metadata = useMutation({
    mutationFn: (body: {
      supplier: string;
      quoteDate: string;
      project: string;
      actor: string;
      reason: string;
    }) =>
      saveDocumentMetadata(detail.data!.source.document_id, {
        supplier_name: body.supplier.trim() || null,
        quote_date: body.quoteDate || null,
        project_name: body.project.trim() || null,
        expected_current_version_id:
          detail.data!.current_document_metadata?.id ?? null,
        decided_by: body.actor,
        reason_detail: body.reason,
      }),
    onSuccess: () => {
      setNotice({
        kind: "success",
        text: "문서 메타데이터를 저장했습니다.",
      });
      void queryClient.invalidateQueries({
        queryKey: ["catalog-candidates", selectedId],
      });
    },
    onError: handleError,
  });

  const createAndMatch = useMutation({
    mutationFn: (body: {
      name: string;
      spec: string;
      unit: string;
      actor: string;
      reason: string;
    }) =>
      createAndMatchStandardItem(selectedId!, {
        canonical_name: body.name,
        canonical_spec: body.spec.trim() || null,
        canonical_unit: body.unit.trim() || null,
        aliases: [],
        created_by: body.actor,
        reason_detail: body.reason,
        expected_current_decision_id:
          detail.data?.current_membership_decision_id ??
          selected?.current_membership_decision_id ??
          null,
      }),
    onSuccess: () => {
      setNotice({ kind: "success", text: "새 표준품목을 생성하고 확정했습니다." });
      if (selectedId !== null) {
        setResolvedIds((current) => new Set(current).add(selectedId));
      }
      void queryClient.invalidateQueries({ queryKey: ["catalog-unmatched"] });
      void queryClient.invalidateQueries({ queryKey: ["standard-items"] });
    },
    onError: handleError,
  });

  const busy =
    membership.isPending || metadata.isPending || createAndMatch.isPending;

  return (
    <main className="workspace-page grouping-page">
      <PageHeading
        kicker="Catalog review"
        title="품목 그룹핑"
        description="정제된 견적 행을 근거와 함께 확인하고 표준품목 소속을 사람이 확정합니다."
      />
      {notice && (
        <div
          className={`workspace-notice is-${notice.kind}`}
          role={notice.kind === "error" ? "alert" : "status"}
        >
          {notice.text}
        </div>
      )}
      <div className="split-workspace">
        <section className="work-list" aria-label="그룹핑 대기 목록">
          <header>
            <strong>미분류 품목</strong>
            <span>{items.length}건</span>
          </header>
          {unmatched.isPending && <p className="inline-state">불러오는 중…</p>}
          {unmatched.isError && (
            <p className="inline-state is-error">목록을 불러오지 못했습니다.</p>
          )}
          <ul>
            {items.map((item, index) => (
              <li key={item.raw_item_id} style={{ "--row-index": index } as React.CSSProperties}>
                <button
                  type="button"
                  className={selectedId === item.raw_item_id ? "work-row is-selected" : "work-row"}
                  onClick={() => {
                    setSelectedId(item.raw_item_id);
                    setNotice(null);
                    setStaleLocked(false);
                  }}
                  disabled={busy}
                >
                  <strong>{item.name ?? "품명 없음"}</strong>
                  <span>{item.spec ?? "사양 없음"}</span>
                  <small>{item.unit ?? "단위 없음"} · 원천행 #{item.raw_item_id}</small>
                </button>
              </li>
            ))}
          </ul>
          {!unmatched.isPending && items.length === 0 && (
            <p className="inline-state">그룹핑을 기다리는 품목이 없습니다.</p>
          )}
        </section>

        <section className="work-detail" aria-label="그룹핑 상세">
          {!selected && (
            <div className="empty-detail">
              <span>01</span>
              <p>왼쪽 목록에서 검토할 품목을 선택하세요.</p>
            </div>
          )}
          {selected && detail.isPending && (
            <div className="empty-detail"><p>후보와 근거를 불러오는 중…</p></div>
          )}
          {selected && detail.isError && (
            <div className="empty-detail is-error" role="alert">
              <p>후보 정보를 불러오지 못했습니다.</p>
              <button type="button" onClick={() => void detail.refetch()}>다시 시도</button>
            </div>
          )}
          {selected && detail.data && (
            <div className="detail-content">
              <header className="record-heading">
                <div>
                  <p className="section-kicker">원천행 #{selected.raw_item_id}</p>
                  <h1>{detail.data.normalized.name ?? "품명 없음"}</h1>
                  <p>{detail.data.normalized.spec ?? "사양 없음"} · {detail.data.normalized.unit ?? "단위 없음"}</p>
                </div>
                <span className="status-tag">
                  {detail.data.match_status === "CANDIDATE" ? "후보 있음" : "후보 없음"}
                </span>
              </header>

              <section className="provenance-strip" aria-label="원본 근거">
                <div><span>원본 파일</span><strong>{detail.data.source.path}</strong></div>
                <div><span>위치</span><strong>{sourcePosition(detail.data.source)}</strong></div>
                <div><span>정제 버전</span><strong>{detail.data.current_cleansing_decision.rule_version}</strong></div>
              </section>

              <section className="candidate-section">
                <div className="section-heading">
                  <div>
                    <p className="section-kicker">Deterministic candidates</p>
                    <h2>표준품목 후보</h2>
                  </div>
                  <span>자동 확정 없음</span>
                </div>
                {detail.data.candidates.length === 0 && (
                  <p className="inline-state">호환되는 기존 표준품목 후보가 없습니다.</p>
                )}
                {detail.data.candidates.map((candidate, index) => (
                  <CandidateRow
                    key={candidate.standard_item_id}
                    candidate={candidate}
                    rank={index + 1}
                    disabled={busy || staleLocked}
                    onConfirm={(actor, reason) =>
                      membership.mutate({ candidate, actor, reason })
                    }
                  />
                ))}
                <RejectForm
                  disabled={busy || staleLocked}
                  onReject={(actor, reason) =>
                    membership.mutate({
                      actor,
                      reason,
                      status: "REJECTED",
                      standardItemId: null,
                      score: null,
                    })
                  }
                />
              </section>

              <MetadataForm
                key={`metadata-${detail.data.current_document_metadata?.id ?? "new"}`}
                metadata={detail.data.current_document_metadata}
                disabled={busy || staleLocked}
                onSave={(body) => metadata.mutate(body)}
              />
              <NewItemForm
                key={`new-${selected.raw_item_id}`}
                defaults={detail.data.normalized}
                disabled={busy || staleLocked}
                onCreate={(body) => createAndMatch.mutate(body)}
              />
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

function CandidateRow({
  candidate,
  rank,
  disabled,
  onConfirm,
}: {
  candidate: CatalogCandidate;
  rank: number;
  disabled: boolean;
  onConfirm: (actor: string, reason: string) => void;
}) {
  const [actor, setActor] = useState("");
  const [reason, setReason] = useState("");
  return (
    <article className="candidate-row">
      <div className="candidate-rank">{String(rank).padStart(2, "0")}</div>
      <div className="candidate-main">
        <div className="candidate-title">
          <div>
            <strong>{candidate.canonical_name}</strong>
            <span>{candidate.canonical_spec ?? "사양 없음"} · {candidate.canonical_unit ?? "단위 없음"}</span>
          </div>
          <b>{formatScore(candidate.final_score)}</b>
        </div>
        <dl className="evidence-metrics">
          <div><dt>품명 유사도</dt><dd>{formatScore(candidate.name_score)}</dd></div>
          <div><dt>사양 유사도</dt><dd>{formatScore(candidate.spec_score)}</dd></div>
          <div><dt>단위 호환</dt><dd>{candidate.unit_compatible ? "통과" : "차단"}</dd></div>
          <div>
            <dt>임베딩</dt>
            <dd>{embeddingLabel(candidate.embedding_status)}</dd>
          </div>
        </dl>
        <p className="token-evidence">
          {candidate.matched_tokens.length
            ? `모델 토큰 ${candidate.matched_tokens.join(", ")}`
            : "일치한 모델 토큰 없음"}
        </p>
        <div className="inline-approval">
          <label>
            <span>검토자</span>
            <input aria-label="후보 검토자" value={actor} onChange={(event) => setActor(event.target.value)} />
          </label>
          <label>
            <span>판정 근거</span>
            <input aria-label="후보 판정 근거" value={reason} onChange={(event) => setReason(event.target.value)} />
          </label>
          <button
            type="button"
            disabled={disabled || !actor.trim() || !reason.trim()}
            onClick={() => onConfirm(actor.trim(), reason.trim())}
          >
            표준품목으로 확정
          </button>
        </div>
      </div>
    </article>
  );
}

function RejectForm({
  disabled,
  onReject,
}: {
  disabled: boolean;
  onReject: (actor: string, reason: string) => void;
}) {
  const [actor, setActor] = useState("");
  const [reason, setReason] = useState("");
  return (
    <details className="subtle-disclosure">
      <summary>적합한 후보 없음</summary>
      <div className="inline-approval">
        <label><span>거절 검토자</span><input value={actor} onChange={(event) => setActor(event.target.value)} /></label>
        <label><span>거절 근거</span><input value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        <button type="button" disabled={disabled || !actor.trim() || !reason.trim()} onClick={() => onReject(actor.trim(), reason.trim())}>
          후보 없음으로 판정
        </button>
      </div>
    </details>
  );
}

function MetadataForm({
  metadata,
  disabled,
  onSave,
}: {
  metadata: import("../api/client").DocumentMetadata | null;
  disabled: boolean;
  onSave: (body: { supplier: string; quoteDate: string; project: string; actor: string; reason: string }) => void;
}) {
  const [supplier, setSupplier] = useState(metadata?.supplier_name ?? "");
  const [quoteDate, setQuoteDate] = useState(metadata?.quote_date ?? "");
  const [project, setProject] = useState(metadata?.project_name ?? "");
  const [actor, setActor] = useState("");
  const [reason, setReason] = useState("");
  return (
    <section className="editor-section" aria-label="문서 메타데이터">
      <div className="section-heading">
        <div><p className="section-kicker">Source context</p><h2>문서 메타데이터</h2></div>
        <span>{metadata ? `v${metadata.version_number}` : "미등록"}</span>
      </div>
      <div className="form-grid">
        <label><span>공급사</span><input value={supplier} onChange={(event) => setSupplier(event.target.value)} /></label>
        <label><span>견적일</span><input type="date" value={quoteDate} onChange={(event) => setQuoteDate(event.target.value)} /></label>
        <label><span>프로젝트</span><input value={project} onChange={(event) => setProject(event.target.value)} /></label>
        <label><span>검토자</span><input aria-label="메타데이터 검토자" value={actor} onChange={(event) => setActor(event.target.value)} /></label>
        <label className="is-wide"><span>변경 근거</span><input value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        <button type="button" disabled={disabled || !actor.trim() || !reason.trim()} onClick={() => onSave({ supplier, quoteDate, project, actor: actor.trim(), reason: reason.trim() })}>
          메타데이터 저장
        </button>
      </div>
    </section>
  );
}

function NewItemForm({
  defaults,
  disabled,
  onCreate,
}: {
  defaults: { name: string | null; spec: string | null; unit: string | null };
  disabled: boolean;
  onCreate: (body: { name: string; spec: string; unit: string; actor: string; reason: string }) => void;
}) {
  const [name, setName] = useState(defaults.name ?? "");
  const [spec, setSpec] = useState(defaults.spec ?? "");
  const [unit, setUnit] = useState(defaults.unit ?? "");
  const [actor, setActor] = useState("");
  const [reason, setReason] = useState("");
  return (
    <section className="editor-section" aria-label="새 표준품목">
      <div className="section-heading">
        <div><p className="section-kicker">New catalog identity</p><h2>새 표준품목</h2></div>
        <span>생성 후 현재 행 확정</span>
      </div>
      <div className="form-grid">
        <label><span>표준 품명</span><input value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label><span>표준 사양</span><input value={spec} onChange={(event) => setSpec(event.target.value)} /></label>
        <label><span>표준 단위</span><input value={unit} onChange={(event) => setUnit(event.target.value)} /></label>
        <label><span>검토자</span><input aria-label="신규품목 검토자" value={actor} onChange={(event) => setActor(event.target.value)} /></label>
        <label className="is-wide"><span>판정 근거</span><input aria-label="신규품목 판정 근거" value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        <button type="button" disabled={disabled || !name.trim() || !actor.trim() || !reason.trim()} onClick={() => onCreate({ name: name.trim(), spec, unit, actor: actor.trim(), reason: reason.trim() })}>
          생성 후 확정
        </button>
      </div>
    </section>
  );
}

function PageHeading({ kicker, title, description }: { kicker: string; title: string; description: string }) {
  return (
    <header className="page-heading">
      <div><p className="section-kicker">{kicker}</p><h1>{title}</h1></div>
      <p>{description}</p>
    </header>
  );
}

function formatScore(value: string) {
  return `${Math.round(Number(value) * 100)}%`;
}

function embeddingLabel(status: CatalogCandidate["embedding_status"]) {
  return {
    DISABLED: "사용 안 함",
    UNAVAILABLE: "일시 사용 불가",
    AVAILABLE: "적용됨",
    MOCK_ONLY: "로컬 모의",
  }[status];
}

function sourcePosition(source: import("../api/client").SourceEvidence) {
  return [
    source.sheet,
    source.page ? `${source.page}쪽` : null,
    source.row ? `${source.row}행` : null,
  ].filter(Boolean).join(" · ") || "위치 정보 없음";
}
