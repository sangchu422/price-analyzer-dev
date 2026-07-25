import type { ReviewQueueItem } from "../api/client";
import { reasonLabel } from "./reasonLabels";

interface ReviewQueueProps {
  items: ReviewQueueItem[];
  availableReasons: string[];
  selectedId: number | null;
  search: string;
  reason: string;
  controlsLocked: boolean;
  resultsLocked: boolean;
  isSearching: boolean;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onSearchChange: (value: string) => void;
  onReasonChange: (value: string) => void;
  onSelect: (id: number) => void;
  onLoadMore: () => void;
}

export function ReviewQueue({
  items,
  availableReasons,
  selectedId,
  search,
  reason,
  controlsLocked,
  resultsLocked,
  isSearching,
  hasNextPage,
  isFetchingNextPage,
  onSearchChange,
  onReasonChange,
  onSelect,
  onLoadMore,
}: ReviewQueueProps) {
  return (
    <aside
      className="queue-panel"
      aria-label="검토 대기 목록"
      aria-busy={resultsLocked}
    >
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
            disabled={controlsLocked}
            onChange={(event) => onSearchChange(event.target.value)}
          />
        </label>
        <label>
          <span className="sr-only">검토 사유 필터</span>
          <select
            aria-label="검토 사유 필터"
            value={reason}
            disabled={controlsLocked}
            onChange={(event) => onReasonChange(event.target.value)}
          >
            <option value="">모든 사유</option>
            {availableReasons.map((value) => (
              <option key={value} value={value}>
                {reasonLabel(value)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="queue-heading">
        <span>검토 항목</span>
        {isSearching ? (
          <span className="queue-progress" role="status" aria-live="polite">
            검색 중…
          </span>
        ) : (
          <span>{items.length}건 표시</span>
        )}
      </div>
      <ol className="queue-list">
        {items.map((item, index) => {
          const name = item.normalized.item_name ?? item.raw.item_name ?? "품명 없음";
          return (
            <li key={item.raw_item_id} style={{ "--row-index": index } as React.CSSProperties}>
              <button
                type="button"
                className={item.raw_item_id === selectedId ? "queue-row is-selected" : "queue-row"}
                aria-pressed={item.raw_item_id === selectedId}
                disabled={resultsLocked}
                onClick={() => onSelect(item.raw_item_id)}
              >
                <span className="row-main">
                  <strong>{name}</strong>
                  <span>{item.normalized.spec ?? item.raw.spec ?? "사양 없음"}</span>
                </span>
                <span className="row-meta">
                  <span className="reason-mark">{reasonLabel(item.reason_code)}</span>
                  <span>{item.source.logical_name}</span>
                  <span>
                    {item.source.row
                      ? `${item.source.row}행`
                      : item.source.page
                        ? `${item.source.page}쪽`
                        : "위치 없음"}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
      {items.length === 0 && (
        <p className="queue-empty">목록에 표시할 항목이 없습니다.</p>
      )}
      {hasNextPage && (
        <button
          className="load-more"
          type="button"
          disabled={isFetchingNextPage || resultsLocked}
          onClick={onLoadMore}
        >
          {isFetchingNextPage ? "불러오는 중…" : "다음 항목 불러오기"}
        </button>
      )}
    </aside>
  );
}
