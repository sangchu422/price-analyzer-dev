"""
표준단가 마스터 동기화
quote_item 집계 → standard_price_master 테이블 업데이트

규칙:
  - 마스터에 없는 품목 → INSERT (새 item_id 발급)
  - 마스터에 있고 manually_reviewed=FALSE → UPDATE (최신 집계로 갱신)
  - 마스터에 있고 manually_reviewed=TRUE  → SKIP (수동 검토 완료 항목 보호)
"""
from psycopg2.extras import execute_values
from config import MIN_CONFIDENCE


_AGGREGATE_SQL = """
SELECT
    qi.item_name,
    COALESCE(qi.spec, '')                                         AS spec,
    COALESCE(qi.unit_norm, '')                                    AS unit,
    MAX(qi.category)                                              AS category,
    COUNT(*)                                                      AS data_count,
    MIN(qi.unit_price)                                            AS price_min,
    ROUND(AVG(qi.unit_price))::BIGINT                             AS price_avg,
    MAX(qi.unit_price)                                            AS price_max,
    MAX(qh.quote_date)                                            AS latest_quote_date,
    STRING_AGG(DISTINCT COALESCE(qh.vendor,''),
               ', ' ORDER BY COALESCE(qh.vendor,''))
        FILTER (WHERE qh.vendor IS NOT NULL AND qh.vendor != '') AS source_vendors,
    MODE() WITHIN GROUP (ORDER BY qi.maker)
        FILTER (WHERE qi.maker IS NOT NULL AND qi.maker != '')   AS main_maker
FROM  quote_item   qi
JOIN  quote_header qh ON qh.id = qi.header_id
WHERE qi.unit_price  > 0
  AND qi.confidence  >= %(min_conf)s
  AND qi.flagged     = FALSE
  AND qh.parse_ok    = TRUE
GROUP BY qi.item_name, COALESCE(qi.spec,''), COALESCE(qi.unit_norm,'')
HAVING COUNT(*) >= 2
ORDER BY qi.item_name, COALESCE(qi.spec,'')
"""


def _next_item_id(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(CAST(SUBSTRING(item_id FROM 4) AS INTEGER)) FROM standard_price_master WHERE item_id ~ '^SP-\\d+$'")
        row = cur.fetchone()
        next_num = (row[0] or 0) + 1
    return f"SP-{next_num:05d}"


def sync_master(conn) -> dict:
    """동기화 실행. {inserted, updated, skipped} 카운트 반환."""
    with conn.cursor() as cur:
        cur.execute(_AGGREGATE_SQL, {"min_conf": MIN_CONFIDENCE})
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]

    aggregated = [dict(zip(cols, r)) for r in rows]

    inserted = updated = skipped = 0

    with conn.cursor() as cur:
        for row in aggregated:
            key = (row["item_name"], row["spec"], row["unit"])

            cur.execute(
                "SELECT id, manually_reviewed FROM standard_price_master "
                "WHERE item_name=%s AND spec=%s AND unit=%s",
                key,
            )
            existing = cur.fetchone()

            if existing is None:
                # 새 품목 INSERT — created_at은 최초 1회만 기록
                item_id = _next_item_id(conn)
                cur.execute(
                    """
                    INSERT INTO standard_price_master
                        (item_id, category, item_name, spec, unit,
                         price_min, price_avg, price_max, data_count,
                         main_maker, latest_quote_date, source_vendors,
                         created_at, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW() AT TIME ZONE 'Asia/Seoul',NOW())
                    """,
                    (
                        item_id,
                        row["category"] or "",
                        row["item_name"],
                        row["spec"],
                        row["unit"],
                        row["price_min"],
                        row["price_avg"],
                        row["price_max"],
                        row["data_count"],
                        row["main_maker"] or "",
                        row["latest_quote_date"],
                        row["source_vendors"] or "",
                    ),
                )
                inserted += 1

            else:
                master_id, reviewed = existing
                if reviewed:
                    skipped += 1
                else:
                    cur.execute(
                        """
                        UPDATE standard_price_master SET
                            category          = %s,
                            price_min         = %s,
                            price_avg         = %s,
                            price_max         = %s,
                            data_count        = %s,
                            main_maker        = %s,
                            latest_quote_date = %s,
                            source_vendors    = %s,
                            updated_at        = NOW()
                        WHERE id = %s
                        """,
                        (
                            row["category"] or "",
                            row["price_min"],
                            row["price_avg"],
                            row["price_max"],
                            row["data_count"],
                            row["main_maker"] or "",
                            row["latest_quote_date"],
                            row["source_vendors"] or "",
                            master_id,
                        ),
                    )
                    updated += 1

    conn.commit()
    return {"inserted": inserted, "updated": updated, "skipped": skipped}
