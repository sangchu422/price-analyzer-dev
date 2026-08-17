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
  if (quality === "SUPPLIER_UNKNOWN") {
    return (
      <span className="evidence-badge is-unknown">
        {`가격 근거 ${count}건 · 제출사 확인 필요`}
      </span>
    );
  }
  if (quality === "NON_COMPARABLE") {
    return (
      <span className="evidence-badge is-unknown">
        규격·가격 범위 확인 필요
      </span>
    );
  }
  const suppliers = supplierCount ?? count;
  return (
    <span
      className={`evidence-badge ${
        quality === "SINGLE_OBSERVATION" ? "is-single" : "is-multiple"
      }`}
    >
      {`가격 근거 ${count}건 · 제출사 ${suppliers}곳`}
    </span>
  );
}
