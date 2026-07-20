"""
견적서 DB 구축 파이프라인 — 엔트리포인트

사용법:
  python run_pipeline.py               # 설정된 QUOTE_FOLDER 전체 스캔
  python run_pipeline.py --init-db     # DB 스키마 초기화 후 실행
  python run_pipeline.py --dry-run     # DB 저장 없이 파싱 결과만 출력
  python run_pipeline.py --status      # 현재 DB 통계 출력
  python run_pipeline.py --sync-master # 표준단가 마스터 동기화 (파싱 없이)
"""
import sys
import argparse
from pathlib import Path

import db
from config import QUOTE_FOLDER
from pipeline.scanner import scan_files, filter_new
from pipeline.reader import read_excel
from pipeline.detector import detect
from pipeline.parsers import standard, assembly
from pipeline.loader import save, save_error, save_skip
from pipeline.sync_master import sync_master

PARSERS = {
    "standard": (standard.parse_header, standard.parse_items),
    "assembly": (assembly.parse_header, assembly.parse_items),
}

# IRM 잠금 탐지 문자열
_IRM_MARKER = "IRM protected"


def run(folder: Path, conn, dry_run=False):
    all_files = scan_files(folder)
    print(f"\n전체 파일: {len(all_files)}개 (xlsx/xls)")

    new_files = filter_new(all_files, conn)
    print(f"신규 파일: {len(new_files)}개 (미처리)\n")

    ok = err = skip = 0

    for path, file_hash in new_files:
        label = path.name

        # 읽기 시도
        try:
            sheets = read_excel(path)
        except Exception as e:
            msg = str(e)
            # IRM 보안 파일 구분
            status = "irm_protected" if _IRM_MARKER in msg else "error"
            print(f"  [SKIP] {label} — {status}: {msg[:80]}")
            if not dry_run:
                save_skip(conn, path, f"{status}: {msg[:200]}")
            err += 1
            continue

        # IRM 감지 (내용 기반)
        first_rows = next(iter(sheets.values()), [])[:2]
        flat = " ".join(cell for row in first_rows for cell in row)
        if _IRM_MARKER in flat:
            print(f"  [SKIP] {label} — IRM 보안 잠금")
            if not dry_run:
                save_skip(conn, path, "irm_protected")
            skip += 1
            continue

        # 레이아웃 감지
        group = detect(sheets)
        if group == "unknown":
            print(f"  [UNKN] {label} — 레이아웃 미분류, 수동 검수 필요")
            if not dry_run:
                save_skip(conn, path, "unknown_layout")
            skip += 1
            continue

        # 파싱
        parse_header, parse_items = PARSERS[group]
        try:
            meta  = parse_header(sheets, file_name=path.name)
            items = parse_items(sheets)
        except Exception as e:
            print(f"  [ERR ] {label} — 파싱 오류: {e}")
            if not dry_run:
                save_error(conn, path, str(e))
            err += 1
            continue

        if dry_run:
            print(f"  [DRY ] {label} | {group} | {meta.get('vendor','?')} | {len(items)}행")
            for it in items[:3]:
                print(f"         {it['item_name']} / {it['unit_price']:,}원")
            ok += 1
            continue

        # DB 저장
        header_id = save(conn, path, file_hash, group, meta, items)
        print(f"  [ OK ] {label} | {group} | id={header_id} | {len(items)}행")
        ok += 1

    print(f"\n완료: 성공 {ok}건 / 오류 {err}건 / 스킵 {skip}건")


def print_status(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM quote_header WHERE parse_ok = TRUE")
        hdr = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM quote_item")
        itm = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM standard_price")
        std = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM standard_price_master")
        mst = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM standard_price_master WHERE manually_reviewed = TRUE")
        mst_rev = cur.fetchone()[0]
        cur.execute("SELECT status, COUNT(*) FROM parse_log GROUP BY status ORDER BY status")
        logs = cur.fetchall()
    print(f"\n── DB 현황 ──────────────────────────")
    print(f"  견적서 헤더:       {hdr:>6,}건")
    print(f"  라인 아이템:       {itm:>6,}건")
    print(f"  집계 품목(VIEW):   {std:>6,}개")
    print(f"  마스터 품목:       {mst:>6,}개  (수동검토완료: {mst_rev}개)")
    print(f"\n── 파싱 로그 ────────────────────────")
    for status, cnt in logs:
        print(f"  {status:<15}: {cnt:>5,}건")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="견적서 DB 파이프라인")
    parser.add_argument("--init-db",      action="store_true", help="스키마 초기화")
    parser.add_argument("--dry-run",      action="store_true", help="DB 저장 없이 파싱만")
    parser.add_argument("--status",       action="store_true", help="DB 통계 출력")
    parser.add_argument("--sync-master",  action="store_true", help="표준단가 마스터 동기화")
    parser.add_argument("--folder",       type=str, default=None, help="견적서 폴더 경로 지정")
    args = parser.parse_args()

    folder = Path(args.folder) if args.folder else QUOTE_FOLDER
    if not folder.exists():
        print(f"오류: 폴더가 존재하지 않습니다 — {folder}")
        sys.exit(1)

    conn = db.get_conn()

    if args.init_db:
        print("DB 스키마 초기화 중...")
        db.init_schema(conn)
        print("완료")

    if args.status:
        print_status(conn)
        conn.close()
        sys.exit(0)

    if args.sync_master:
        print("표준단가 마스터 동기화 중...")
        result = sync_master(conn)
        print(f"  신규 추가: {result['inserted']:,}건")
        print(f"  자동 갱신: {result['updated']:,}건")
        print(f"  보호(수동검토완료): {result['skipped']:,}건")
        conn.close()
        sys.exit(0)

    run(folder, conn, dry_run=args.dry_run)

    # 파싱 후 자동으로 마스터 동기화
    if not args.dry_run:
        print("\n표준단가 마스터 자동 동기화 중...")
        result = sync_master(conn)
        print(f"  신규: {result['inserted']}건 / 갱신: {result['updated']}건 / 보호: {result['skipped']}건")

    conn.close()
