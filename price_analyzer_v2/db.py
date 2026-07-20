import psycopg2
import psycopg2.extras
from config import DSN


def get_conn():
    return psycopg2.connect(DSN)


def init_schema(conn):
    """schema.sql 을 읽어 DB에 적용 (최초 1회)."""
    from pathlib import Path
    sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def already_processed(conn, file_hash: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM quote_header WHERE file_hash = %s LIMIT 1",
            (file_hash,)
        )
        return cur.fetchone() is not None


def insert_header(conn, header: dict) -> int:
    sql = """
        INSERT INTO quote_header
            (file_path, file_hash, file_name, vendor, quote_no, quote_date,
             project, total_amount, layout_group, item_count, confidence, parse_ok)
        VALUES
            (%(file_path)s, %(file_hash)s, %(file_name)s, %(vendor)s, %(quote_no)s,
             %(quote_date)s, %(project)s, %(total_amount)s, %(layout_group)s,
             %(item_count)s, %(confidence)s, %(parse_ok)s)
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, header)
        return cur.fetchone()[0]


def insert_items(conn, header_id: int, items: list[dict]):
    if not items:
        return
    sql = """
        INSERT INTO quote_item
            (header_id, unit_name, item_name, spec, unit, unit_norm,
             quantity, unit_price, amount, maker, category, confidence)
        VALUES
            (%(header_id)s, %(unit_name)s, %(item_name)s, %(spec)s, %(unit)s,
             %(unit_norm)s, %(quantity)s, %(unit_price)s, %(amount)s,
             %(maker)s, %(category)s, %(confidence)s)
    """
    for item in items:
        item["header_id"] = header_id
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, items, page_size=200)


def log_result(conn, file_path: str, status: str, message: str = "", item_count: int = 0):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO parse_log (file_path, status, message, item_count) VALUES (%s,%s,%s,%s)",
            (file_path, status, message, item_count)
        )
