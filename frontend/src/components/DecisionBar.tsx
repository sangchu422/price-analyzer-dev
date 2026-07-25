import { useState } from "react";
import type { ManualDecisionStatus, ReviewQueueItem } from "../api/client";

interface DecisionBarProps {
  item: ReviewQueueItem;
  isSaving: boolean;
  isDisabled: boolean;
  notice: { kind: "success" | "stale" | "error"; text: string } | null;
  onSubmit: (status: ManualDecisionStatus, actor: string, detail: string) => void;
}

export function DecisionBar({
  item,
  isSaving,
  isDisabled,
  notice,
  onSubmit,
}: DecisionBarProps) {
  const [actor, setActor] = useState("");
  const [detail, setDetail] = useState("");
  const [attempted, setAttempted] = useState(false);
  const actorError = attempted ? validateActor(actor) : null;
  const detailError = attempted ? validateDetail(detail) : null;
  const isAssertive = notice?.kind === "stale" || notice?.kind === "error";

  function submit(status: ManualDecisionStatus) {
    setAttempted(true);
    if (validateActor(actor) || validateDetail(detail)) return;
    onSubmit(status, actor.trim(), detail.trim());
  }

  return (
    <section className="decision-bar" aria-label="검토 판단" key={item.raw_item_id}>
      <div className="decision-fields">
        <label>
          <span>검토자</span>
          <input
            value={actor}
            aria-label="검토자"
            maxLength={100}
            aria-invalid={Boolean(actorError)}
            aria-describedby={actorError ? "actor-error actor-count" : "actor-count"}
            onChange={(event) => setActor(event.target.value)}
            placeholder="이름 또는 사번"
          />
          {actorError && <small id="actor-error">{actorError}</small>}
          <small className="character-count" id="actor-count">
            {actor.length.toLocaleString("ko-KR")} / 100
          </small>
        </label>
        <label className="reason-input">
          <span>판단 근거</span>
          <textarea
            value={detail}
            aria-label="판단 근거"
            maxLength={2000}
            aria-invalid={Boolean(detailError)}
            aria-describedby={detailError ? "detail-error detail-count" : "detail-count"}
            onChange={(event) => setDetail(event.target.value)}
            placeholder="원본과 판단 근거를 구체적으로 기록하세요."
            rows={2}
          />
          {detailError && <small id="detail-error">{detailError}</small>}
          <small className="character-count" id="detail-count">
            {detail.length.toLocaleString("ko-KR")} / 2,000
          </small>
        </label>
      </div>
      <div className="decision-actions">
        <div
          className="decision-notice"
          role={isAssertive ? "alert" : "status"}
          aria-live={isAssertive ? "assertive" : "polite"}
        >
          {isSaving && <span>판단을 저장하는 중입니다…</span>}
          {!isSaving && notice && (
            <span className={`notice-${notice.kind}`}>{notice.text}</span>
          )}
        </div>
        <button
          type="button"
          className="exclude-button"
          disabled={isSaving || isDisabled}
          onClick={() => submit("EXCLUDED")}
        >
          제외
        </button>
        <button
          type="button"
          className="include-button"
          disabled={isSaving || isDisabled}
          onClick={() => submit("INCLUDED")}
        >
          포함
        </button>
      </div>
    </section>
  );
}

function validateActor(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return "검토자를 입력해 주세요.";
  if (trimmed.toLocaleLowerCase("en-US") === "system") {
    return "SYSTEM은 자동 판단 전용 이름입니다.";
  }
  if (trimmed.length > 100) return "검토자는 100자 이내로 입력해 주세요.";
  return null;
}

function validateDetail(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return "판단 근거를 입력해 주세요.";
  if (trimmed.length > 2000) {
    return "판단 근거는 2,000자 이내로 입력해 주세요.";
  }
  return null;
}
