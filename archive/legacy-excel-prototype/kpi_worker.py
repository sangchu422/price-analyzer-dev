"""
KPI 검색 워커 — subprocess로 호출됨 (스레드 격리)
사용: python kpi_worker.py <keyword> [max_results]
출력: JSON to stdout
"""
import sys, json, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

def main():
    if len(sys.argv) < 2:
        print(json.dumps([]))
        return

    keyword   = sys.argv[1]
    max_res   = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    try:
        from kpi_config import KPI_ID, KPI_PW
    except ImportError:
        KPI_ID, KPI_PW = "wia", "xhdrn"

    user_id   = sys.argv[3] if len(sys.argv) > 3 else KPI_ID
    user_pw   = sys.argv[4] if len(sys.argv) > 4 else KPI_PW

    try:
        from kpi_scraper import login, search_price, close
        ok, msg = login(user_id, user_pw)
        if not ok:
            print(json.dumps({"error": msg}))
            return

        results = search_price(keyword, max_results=max_res)
        print(json.dumps(results, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
    finally:
        try:
            close()
        except Exception:
            pass

if __name__ == "__main__":
    main()
