export function safeNextCursor<T extends { next_cursor: number | null }>(
  lastPage: T,
  allPages: T[],
  lastPageParam: number | undefined,
) {
  const next = lastPage.next_cursor;
  if (
    next === null ||
    next === lastPageParam ||
    allPages.slice(0, -1).some((page) => page.next_cursor === next)
  ) {
    return undefined;
  }
  return next;
}

export function uniqueByRawItemId<T extends { raw_item_id: number }>(items: T[]) {
  const seen = new Set<number>();
  return items.filter((item) => {
    if (seen.has(item.raw_item_id)) return false;
    seen.add(item.raw_item_id);
    return true;
  });
}
