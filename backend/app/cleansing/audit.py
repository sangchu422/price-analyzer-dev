"""Read-only operational audit for a clean-v2 database."""

from __future__ import annotations

import json

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.sqlite import configure_sqlite


def build_audit(session: Session) -> dict[str, object]:
    scalar = lambda sql: session.execute(text(sql)).scalar_one()
    rows = lambda sql: [dict(row._mapping) for row in session.execute(text(sql))]
    return {
        "integrity_check": scalar("PRAGMA integrity_check"),
        "foreign_key_violations": len(rows("PRAGMA foreign_key_check")),
        "parse_runs": rows(
            "SELECT parser_version, status, COUNT(*) AS count "
            "FROM source_parse_run GROUP BY parser_version, status ORDER BY parser_version, status"
        ),
        "reader_v1_latest_review_baseline": scalar(
            "WITH v1_raw AS (SELECT spo.raw_item_id FROM source_parse_output spo "
            "JOIN source_parse_run spr ON spr.id=spo.parse_run_id WHERE spr.parser_version='reader-v1'), "
            "ranked AS (SELECT cd.*, ROW_NUMBER() OVER(PARTITION BY raw_item_id ORDER BY id DESC) n "
            "FROM clean_decision cd WHERE rule_version != 'clean-v2') "
            "SELECT COUNT(*) FROM ranked JOIN v1_raw ON v1_raw.raw_item_id=ranked.raw_item_id "
            "WHERE n=1 AND status='REVIEW_REQUIRED'"
        ),
        "reassessment_runs": rows(
            "SELECT id, rule_version, status, counts_json "
            "FROM cleansing_reassessment_run ORDER BY id DESC LIMIT 5"
        ),
        "current_decisions": rows(
            "WITH successful AS ("
            " SELECT source_variant_id, MAX(id) run_id FROM source_parse_run WHERE status='SUCCEEDED' GROUP BY source_variant_id"
            "), current_raw AS ("
            " SELECT spo.raw_item_id FROM source_parse_output spo JOIN successful s ON s.run_id=spo.parse_run_id"
            "), latest AS ("
            " SELECT cd.*, ROW_NUMBER() OVER(PARTITION BY raw_item_id ORDER BY id DESC) n FROM clean_decision cd"
            ") SELECT status, reason_code, COUNT(*) count FROM latest JOIN current_raw cr ON cr.raw_item_id=latest.raw_item_id "
            "WHERE n=1 GROUP BY status, reason_code ORDER BY count DESC"
        ),
        "current_numeric_range": scalar(
            "WITH successful AS (SELECT source_variant_id, MAX(id) run_id FROM source_parse_run WHERE status='SUCCEEDED' GROUP BY source_variant_id), "
            "current_raw AS (SELECT spo.raw_item_id FROM source_parse_output spo JOIN successful s ON s.run_id=spo.parse_run_id), "
            "latest AS (SELECT cd.*, ROW_NUMBER() OVER(PARTITION BY raw_item_id ORDER BY id DESC) n FROM clean_decision cd) "
            "SELECT COUNT(*) FROM latest JOIN current_raw cr ON cr.raw_item_id=latest.raw_item_id WHERE n=1 AND reason_code='NUMERIC_OUT_OF_RANGE'"
        ),
        "numeric_range_details": rows(
            "WITH successful AS (SELECT source_variant_id, MAX(id) run_id FROM source_parse_run WHERE status='SUCCEEDED' GROUP BY source_variant_id), "
            "current_raw AS (SELECT spo.raw_item_id FROM source_parse_output spo JOIN successful s ON s.run_id=spo.parse_run_id), "
            "latest AS (SELECT cd.*, ROW_NUMBER() OVER(PARTITION BY raw_item_id ORDER BY id DESC) n FROM clean_decision cd) "
            "SELECT r.id, r.item_name_raw, r.quantity_raw, r.unit_price_raw, r.amount_raw, r.source_sheet, r.source_row "
            "FROM latest l JOIN current_raw cr ON cr.raw_item_id=l.raw_item_id JOIN raw_quote_item r ON r.id=l.raw_item_id "
            "WHERE n=1 AND reason_code='NUMERIC_OUT_OF_RANGE' ORDER BY r.id"
        ),
        "current_source_maker_recovery": scalar(
            "WITH successful AS (SELECT source_variant_id, MAX(id) run_id FROM source_parse_run WHERE status='SUCCEEDED' GROUP BY source_variant_id), "
            "current_raw AS (SELECT spo.raw_item_id FROM source_parse_output spo JOIN successful s ON s.run_id=spo.parse_run_id), "
            "latest AS (SELECT cd.*, ROW_NUMBER() OVER(PARTITION BY raw_item_id ORDER BY id DESC) n FROM clean_decision cd) "
            "SELECT COUNT(*) FROM latest JOIN current_raw cr ON cr.raw_item_id=latest.raw_item_id WHERE n=1 AND reason_code='SOURCE_MAKER_RECOVERY'"
        ),
        "outliers_without_evidence": scalar(
            "WITH latest AS (SELECT cd.*, ROW_NUMBER() OVER(PARTITION BY raw_item_id ORDER BY id DESC) n FROM clean_decision cd) "
            "SELECT COUNT(*) FROM latest WHERE n=1 AND reason_code='UNIT_PRICE_MAD_OUTLIER' "
            "AND (reason_evidence_json IS NULL OR reason_evidence_json='')"
        ),
    }


def main() -> int:
    engine = configure_sqlite(create_engine(f"sqlite:///{settings.database_path.as_posix()}"))
    try:
        with Session(engine) as session:
            print(json.dumps(build_audit(session), ensure_ascii=False, indent=2))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
