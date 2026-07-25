import type { Ref } from "react";
import type { DisplayValues, ReviewQueueItem } from "../api/client";
import { reasonLabel } from "./reasonLabels";

const valueFields: { key: keyof DisplayValues; label: string }[] = [
  { key: "item_name", label: "품명" },
  { key: "spec", label: "사양" },
  { key: "maker", label: "제조사" },
  { key: "unit", label: "단위" },
  { key: "quantity", label: "수량" },
  { key: "unit_price", label: "단가" },
  { key: "amount", label: "금액" },
];

function display(value: string | null) {
  return value ?? "—";
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
  return (
    <section className="inspector" aria-label="선택 항목 상세" key={item.raw_item_id}>
      <header className="inspector-header">
        <div>
          <p className="eyebrow">항목 #{item.raw_item_id}</p>
          <h1 ref={headingRef} tabIndex={-1}>
            {item.normalized.item_name ?? item.raw.item_name ?? "품명 없음"}
          </h1>
          <p>{item.normalized.spec ?? item.raw.spec ?? "사양 정보 없음"}</p>
        </div>
        <span className="status-tag">검토 필요</span>
      </header>

      <section className="reason-section" aria-labelledby="reason-title">
        <div>
          <p className="section-kicker">검토 사유</p>
          <h2 id="reason-title">{reasonLabel(item.reason_code)}</h2>
        </div>
        <p>{item.reason_detail ?? "상세 사유가 기록되지 않았습니다."}</p>
      </section>

      <section className="comparison" aria-labelledby="comparison-title">
        <div className="section-title">
          <p className="section-kicker">데이터 대조</p>
          <h2 id="comparison-title">원본과 정규화 값</h2>
        </div>
        <div className="comparison-head" aria-hidden="true">
          <span>필드</span>
          <span>원본</span>
          <span>정규화</span>
        </div>
        <dl>
          {valueFields.map(({ key, label }) => {
            const changed = item.raw[key] !== item.normalized[key];
            return (
              <div className={changed ? "comparison-row is-changed" : "comparison-row"} key={key}>
                <dt>{label}</dt>
                <dd>{display(item.raw[key])}</dd>
                <dd>{display(item.normalized[key])}</dd>
              </div>
            );
          })}
        </dl>
      </section>

      <section className="evidence" aria-labelledby="evidence-title">
        <div className="section-title">
          <p className="section-kicker">감사 증빙</p>
          <h2 id="evidence-title">원본 파일 및 파싱 이력</h2>
        </div>
        <dl className="evidence-list">
          <div>
            <dt>논리 문서</dt>
            <dd>{item.source.logical_name}</dd>
          </div>
          <div>
            <dt>실제 파일 경로</dt>
            <dd>{item.source.path}</dd>
          </div>
          <div>
            <dt>SHA-256</dt>
            <dd className="hash">{item.source.sha256}</dd>
          </div>
          <div>
            <dt>보안 / 선택 상태</dt>
            <dd>
              {item.source.security_state} ·{" "}
              {item.source.selected_for_parsing_at_ingest ? "파싱 원본으로 선택됨" : "대체 증빙"}
            </dd>
          </div>
          <div>
            <dt>원본 위치</dt>
            <dd>{sourceLocation(item)}</dd>
          </div>
          <div>
            <dt>파서</dt>
            <dd>{item.source.parser_name} {item.source.parser_version}</dd>
          </div>
          <div>
            <dt>파서 경고</dt>
            <dd>
              {item.source.parser_warnings.length
                ? item.source.parser_warnings.map((warning, index) => (
                    <code key={index}>{JSON.stringify(warning)}</code>
                  ))
                : "경고 없음"}
            </dd>
          </div>
        </dl>
      </section>
    </section>
  );
}
