import type { ReviewQueueItem } from "../api/client";
import { reasonLabel } from "./reasonLabels";

interface ReviewQueueProps {
  items: ReviewQueueItem[];
  selectedId: number | null;
  search: string;
  reason: string;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onSearchChange: (value: string) => void;
  onReasonChange: (value: string) => void;
  onSelect: (id: number) => void;
  onLoadMore: () => void;
}

export function ReviewQueue({
  items,
  selectedId,
  search,
  reason,
  hasNextPage,
  isFetchingNextPage,
  onSearchChange,
  onReasonChange,
  onSelect,
  onLoadMore,
}: ReviewQueueProps) {
  const reasons = [...new Set(items.map((item) => item.reason_code))];
  const needle = search.trim().toLocaleLowerCase("ko");
  const visibleItems = items.filter((item) => {
    const searchable = [
      item.raw.item_name,
      item.normalized.item_name,
      item.source.logical_name,
      item.source.path,
    ]
      .filter(Boolean)
      .join(" ")
      .toLocaleLowerCase("ko");
    return (!needle || searchable.includes(needle)) && (!reason || item.reason_code === reason);
  });

  return (
    <aside className="queue-panel" aria-label="검토 대기 목록">
      <div className="queue-tools">
        <label className="search-field">
          <span className="sr-only">품목 또는 파일 검색</span>
          <svg aria-hidden="true" viewBox="0 0 24 24">
            <circle cx="11" cy="11" r="6" />
            <path d="m16 16 4 4" />
          </svg>
          <input
            type="search"
            aria-label="품목 또는 파일 검색"
            placeholder="품목 또는 파일 검색"
            value={search}
            onChange={(event) => onSearchChange(event.target.value)}
          />
        </label>
        <label>
          <span className="sr-only">검토 사유 필터</span>
          <select
            aria-label="검토 사유 필터"
            value={reason}
            onChange={(event) => onReasonChange(event.target.value)}
          >
            <option value="">모든 사유</option>
            {reasons.map((value) => (
              <option key={value} value={value}>
                {reasonLabel(value)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="queue-heading">
        <span>검토 항목</span>
        <span>{visibleItems.length}건 표시</span>
      </div>
      <ol className="queue-list">
        {visibleItems.map((item, index) => {
          const name = item.normalized.item_name ?? item.raw.item_name ?? "품명 없음";
          return (
            <li key={item.raw_item_id} style={{ "--row-index": index } as React.CSSProperties}>
              <button
                type="button"
                className={item.raw_item_id === selectedId ? "queue-row is-selected" : "queue-row"}
                aria-pressed={item.raw_item_id === selectedId}
                onClick={() => onSelect(item.raw_item_id)}
              >
                <span className="row-main">
                  <strong>{name}</strong>
                  <span>{item.normalized.spec ?? item.raw.spec ?? "사양 없음"}</span>
                </span>
                <span className="row-meta">
                  <span className="reason-mark">{reasonLabel(item.reason_code)}</span>
                  <span>{item.source.logical_name}</span>
                  <span>{item.source.row ? `${item.source.row}행` : item.source.page ? `${item.source.page}쪽` : "위치 없음"}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
      {visibleItems.length === 0 && search && (
        <p className="queue-empty">검색 조건에 맞는 항목이 없습니다.</p>
      )}
      {hasNextPage && (
        <button
          className="load-more"
          type="button"
          disabled={isFetchingNextPage}
          onClick={onLoadMore}
        >
          {isFetchingNextPage ? "불러오는 중…" : "다음 항목 불러오기"}
        </button>
      )}
    </aside>
  );
}
