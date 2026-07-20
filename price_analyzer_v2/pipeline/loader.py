"""
파싱 결과를 PostgreSQL에 저장.
헤더 → quote_header, 아이템 → quote_item, 결과 → parse_log
"""
from pathlib import Path
import db
from pipeline.scorer import score_header, enrich_items


def save(conn, path: Path, file_hash: str, layout_group: str,
         meta: dict, items: list[dict]) -> int:
    """DB에 저장 후 삽입된 quote_header.id 반환."""
    enriched = enrich_items(items)
    meta.setdefault("vendor", None)

    header = {
        "file_path":    str(path),
        "file_hash":    file_hash,
        "file_name":    path.name,
        "vendor":       meta.get("vendor"),
        "quote_no":     meta.get("quote_no"),
        "quote_date":   meta.get("quote_date"),
        "project":      meta.get("project"),
        "total_amount": meta.get("total_amount"),
        "layout_group": layout_group,
        "item_count":   len(enriched),
        "confidence":   score_header(meta),
        "parse_ok":     True,
    }

    header_id = db.insert_header(conn, header)
    db.insert_items(conn, header_id, enriched)
    db.log_result(conn, str(path), "ok", item_count=len(enriched))
    conn.commit()
    return header_id


def save_error(conn, path: Path, message: str):
    db.log_result(conn, str(path), "error", message=message)
    conn.commit()


def save_skip(conn, path: Path, reason: str):
    db.log_result(conn, str(path), "skip", message=reason)
    conn.commit()
