import { useQuery } from "@tanstack/react-query";
import type { CSSProperties, Ref } from "react";
import {
  getSourcePreview,
  type ReasonEvidenceObservation,
  type ReviewQueueItem,
} from "../api/client";
import { reasonLabel } from "./reasonLabels";

function formatWon(value: string | null | undefined) {
  if (!value) return "금액 정보 없음";
  const amount = Number(value.replaceAll(",", "").replace(/[^0-9.-]/g, ""));
  return Number.isFinite(amount)
    ? `${amount.toLocaleString("ko-KR", { maximumFractionDigits: 0 })}원`
    : value;
}

function formatPercent(value: string | null | undefined) {
  if (!value) return null;
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return `${number > 0 ? "+" : ""}${number.toLocaleString("ko-KR", {
    maximumFractionDigits: 1,
  })}%`;
}

function reasonSummary(item: ReviewQueueItem) {
  const evidence = item.reason_evidence;
  switch (item.reason_code) {
    case "UNIT_PRICE_MAD_OUTLIER":
    case "PRICE_OUTLIER": {
      const count = evidence?.observation_count;
      const median = formatWon(evidence?.median_unit_price);
      const variance = formatPercent(evidence?.variance_percent);
      if (count && evidence?.median_unit_price) {
        return `과거 유사 품목 ${count}건의 중앙 단가 ${median}보다 ${variance ?? "큰 폭으로"} 차이가 납니다.`;
      }
      return "과거 유사 품목과 비교했을 때 단가 차이가 커 확인이 필요합니다.";
    }
    case "AMOUNT_MISMATCH":
      return "수량 × 단가로 계산한 값과 견적서 금액이 일치하지 않습니다.";
    case "COLUMN_SHIFT_SUSPECTED":
      return "견적서의 열 위치가 어긋난 것으로 보여 품명·수량·단가를 확인해 주세요.";
    case "INVALID_AMOUNT":
      return "견적 금액을 숫자로 확인할 수 없어 원본 확인이 필요합니다.";
    case "INVALID_QUANTITY":
      return "수량을 숫자로 확인할 수 없어 원본 확인이 필요합니다.";
    case "NUMERIC_OUT_OF_RANGE":
      return "수량 또는 가격이 일반적인 입력 범위를 벗어나 원본 확인이 필요합니다.";
    case "MISSING_ITEM_NAME":
      return "품명이 비어 있어 표준 품목으로 분류할 수 없습니다.";
    default:
      return "자동 정제 과정에서 확인이 필요한 항목으로 분류되었습니다.";
  }
}

function sourceLocation(item: ReviewQueueItem) {
  const parts = [
    item.source.sheet,
    item.source.page ? `${item.source.page}쪽` : null,
    item.source.row ? `${item.source.row}행` : null,
    item.source.cells,
  ].filter(Boolean);
  return parts.join(" · ") || "위치 정보 없음";
}

export function ItemInspector({
  item,
  headingRef,
}: {
  item: ReviewQueueItem;
  headingRef?: Ref<HTMLHeadingElement>;
}) {
  const preview = useQuery({
    queryKey: ["source-preview", item.source.variant_id, item.raw_item_id],
    queryFn: ({ signal }) =>
      getSourcePreview(item.source.variant_id, item.raw_item_id, signal),
    staleTime: Number.POSITIVE_INFINITY,
  });

  return (
    <section className="inspector" aria-label="선택 항목 상세" key={item.raw_item_id}>
      <header className="inspector-header">
        <div>
          <p className="eyebrow">품목 #{item.raw_item_id}</p>
          <h1 ref={headingRef} tabIndex={-1}>
            {item.normalized.item_name ?? item.raw.item_name ?? "품명 없음"}
          </h1>
          <p>{item.normalized.spec ?? item.raw.spec ?? missingSpecLabel(item.spec_source_status)}</p>
        </div>
        <span className="status-tag">검토 필요</span>
      </header>

      <section className="reason-section" aria-labelledby="reason-title">
        <div>
          <p className="section-kicker">검토 사유</p>
          <h2 id="reason-title">{reasonLabel(item.reason_code)}</h2>
        </div>
        <p>{reasonSummary(item)}</p>
      </section>

      {item.reason_evidence?.kind === "UNIT_PRICE_DISTRIBUTION" && (
        <PriceDistribution item={item} />
      )}

      <section className="evidence source-preview-section" aria-labelledby="evidence-title">
        <div className="section-title">
          <p className="section-kicker">원본 확인</p>
          <h2 id="evidence-title">견적서 해당 위치</h2>
        </div>
        <div className="source-preview-meta">
          <strong>{item.source.path.split(/[\\/]/).at(-1) ?? item.source.logical_name}</strong>
          <span>{sourceLocation(item)}</span>
          <a
            className="source-file-link"
            href={`/api/documents/variants/${item.source.variant_id}/file${item.source.page ? `#page=${item.source.page}` : ""}`}
            target="_blank"
            rel="noreferrer"
          >
            원본 전체 열기
          </a>
        </div>
        {preview.isPending && <p className="inline-state">원본 위치를 불러오는 중…</p>}
        {preview.isError && (
          <p className="inline-state">미리보기를 만들 수 없어 원본 파일 링크를 제공합니다.</p>
        )}
        {preview.data?.kind === "SPREADSHEET" && (
          <div className="source-grid-scroll" tabIndex={0} aria-label="원본 견적서 셀 미리보기">
            <table className="source-grid">
              <tbody>
                {preview.data.rows.map((row) => (
                  <tr key={row.row_number}>
                    <th scope="row">{row.row_number}</th>
                    {row.cells.map((cell) => (
                      <td
                        className={cell.highlighted ? "is-source-target" : undefined}
                        key={cell.coordinate}
                        title={cell.coordinate}
                      >
                        {cell.value ?? ""}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {preview.data?.kind === "PDF" && (
          <iframe
            className="source-pdf-frame"
            title={`${preview.data.file_name} ${preview.data.page ?? 1}쪽`}
            src={`${preview.data.file_url}#page=${preview.data.page ?? 1}&view=FitH`}
          />
        )}
      </section>
    </section>
  );
}

function missingSpecLabel(status: ReviewQueueItem["spec_source_status"]) {
  if (status === "SOURCE_BLANK") return "원문에 규격 없음";
  if (status === "PARSER_UNMAPPED") return "견적서에서 규격 위치 확인 필요";
  return "원본에서 규격을 확인하지 못함";
}

function PriceDistribution({ item }: { item: ReviewQueueItem }) {
  const evidence = item.reason_evidence;
  const observations = evidence?.observations ?? [];
  const values = observations
    .map((row) => Number(row.unit_price))
    .filter(Number.isFinite);
  const median = Number(evidence?.median_unit_price);
  if (!values.length || !Number.isFinite(median)) return null;
  const minimum = Math.min(...values, median);
  const maximum = Math.max(...values, median);
  const span = Math.max(1, maximum - minimum);
  const position = (value: number) => `${((value - minimum) / span) * 100}%`;

  return (
    <section className="price-distribution" aria-labelledby="distribution-title">
      <div className="section-title">
        <p className="section-kicker">가격 비교</p>
        <h2 id="distribution-title">유사 품목 단가 분포</h2>
      </div>
      <div className="distribution-summary">
        <span>현재 {formatWon(evidence?.current_unit_price)}</span>
        <strong>중앙값 {formatWon(evidence?.median_unit_price)}</strong>
        <span className="variance-callout">차이 {formatPercent(evidence?.variance_percent)}</span>
      </div>
      <div className="distribution-track" aria-hidden="true">
        <span
          className="distribution-median"
          style={{ "--point-position": position(median) } as CSSProperties}
        />
        {observations.map((row, index) => {
          const value = Number(row.unit_price);
          return (
            <span
              className={`distribution-point ${row.raw_item_id === item.raw_item_id ? "is-current" : ""}`}
              style={{ "--point-position": position(value) } as CSSProperties}
              key={`${row.raw_item_id}-${index}`}
            />
          );
        })}
      </div>
      <ul className="distribution-evidence-list">
        {observations.map((row) => (
          <ObservationRow currentId={item.raw_item_id} row={row} key={row.raw_item_id} />
        ))}
      </ul>
    </section>
  );
}

function ObservationRow({
  currentId,
  row,
}: {
  currentId: number;
  row: ReasonEvidenceObservation;
}) {
  const source = row.source;
  const location = [
    source?.sheet,
    source?.page ? `${source.page}쪽` : null,
    source?.row ? `${source.row}행` : null,
  ].filter(Boolean).join(" · ");
  return (
    <li className={row.raw_item_id === currentId ? "is-current" : undefined}>
      <span>{row.raw_item_id === currentId ? "현재 검토 품목" : `유사 품목 #${row.raw_item_id}`}</span>
      <strong>{formatWon(row.unit_price)}</strong>
      {source ? (
        <a
          href={`/api/documents/variants/${source.variant_id}/file${source.page ? `#page=${source.page}` : ""}`}
          target="_blank"
          rel="noreferrer"
          title={`${source.file_name}${location ? ` · ${location}` : ""}`}
        >
          원본 보기
        </a>
      ) : <span />}
    </li>
  );
}
