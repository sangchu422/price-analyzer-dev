import { useState } from "react";
import type { ManualDecisionStatus, ReviewQueueItem } from "../api/client";

interface DecisionBarProps {
  item: ReviewQueueItem;
  isSaving: boolean;
  notice: { kind: "success" | "stale" | "error"; text: string } | null;
  onSubmit: (status: ManualDecisionStatus, actor: string, detail: string) => void;
}

export function DecisionBar({ item, isSaving, notice, onSubmit }: DecisionBarProps) {
  const [actor, setActor] = useState("");
  const [detail, setDetail] = useState("");
  const [attempted, setAttempted] = useState(false);
  const actorError = attempted && !actor.trim();
  const detailError = attempted && !detail.trim();

  function submit(status: ManualDecisionStatus) {
    setAttempted(true);
    if (!actor.trim() || !detail.trim()) return;
    onSubmit(status, actor.trim(), detail.trim());
  }

  return (
    <section className="decision-bar" aria-label="검토 판단" key={item.raw_item_id}>
      <div className="decision-fields">
        <label>
          <span>검토자</span>
          <input
            value={actor}
            aria-invalid={actorError}
            aria-describedby={actorError ? "actor-error" : undefined}
            onChange={(event) => setActor(event.target.value)}
            placeholder="이름 또는 사번"
          />
          {actorError && <small id="actor-error">검토자를 입력해 주세요.</small>}
        </label>
        <label className="reason-input">
          <span>판단 근거</span>
          <textarea
            value={detail}
            aria-invalid={detailError}
            aria-describedby={detailError ? "detail-error" : undefined}
            onChange={(event) => setDetail(event.target.value)}
            placeholder="원본과 판단 근거를 구체적으로 기록하세요."
            rows={2}
          />
          {detailError && <small id="detail-error">판단 근거를 입력해 주세요.</small>}
        </label>
      </div>
      <div className="decision-actions">
        <div className="decision-notice" aria-live="polite">
          {isSaving && <span>판단을 저장하는 중입니다…</span>}
          {!isSaving && notice && <span className={`notice-${notice.kind}`}>{notice.text}</span>}
        </div>
        <button type="button" className="exclude-button" disabled={isSaving} onClick={() => submit("EXCLUDED")}>
          제외
        </button>
        <button type="button" className="include-button" disabled={isSaving} onClick={() => submit("INCLUDED")}>
          포함
        </button>
      </div>
    </section>
  );
}
