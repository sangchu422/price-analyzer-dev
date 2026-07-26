import type { EvidenceQuality } from "../api/client";

export function EvidenceBadge({
  quality,
  count,
}: {
  quality: EvidenceQuality | null;
  count: number;
}) {
  if (quality === null) {
    return <span className="evidence-badge is-empty">가격 근거 없음</span>;
  }
  return (
    <span
      className={`evidence-badge ${
        quality === "SINGLE_OBSERVATION" ? "is-single" : "is-multiple"
      }`}
    >
      {quality === "SINGLE_OBSERVATION" ? "근거 1건" : `근거 ${count}건`}
    </span>
  );
}
