import type { EvidenceQuality } from "../api/client";

export function EvidenceBadge({
  quality,
  count,
  supplierCount,
}: {
  quality: EvidenceQuality | null;
  count: number;
  supplierCount?: number | null;
}) {
  if (quality === null) {
    return <span className="evidence-badge is-empty">가격 근거 없음</span>;
  }
  const suppliers = supplierCount ?? count;
  return (
    <span
      className={`evidence-badge ${
        quality === "SINGLE_OBSERVATION" ? "is-single" : "is-multiple"
      }`}
    >
      {`근거 ${count}건 · 공급사 ${suppliers}곳`}
    </span>
  );
}
