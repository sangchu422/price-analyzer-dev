"""
한국물가정보(kpi.or.kr) 로그인 + 가격 조회 모듈
Playwright 기반 — 카테고리 직접 접근 방식
"""
import re
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

try:
    from kpi_config import KPI_ID, KPI_PW
except ImportError:
    KPI_ID = "wia"
    KPI_PW = "xhdrn"

BASE_URL = "https://www.kpi.or.kr"
LOGIN_URL = f"{BASE_URL}/www/member/login.asp"

# ── 설비 구매 관련 카테고리 맵 ────────────────────────────────────────────────
# keyword→CATE_CD 매핑: 품명에 키워드가 포함되면 해당 카테고리 우선 검색
CATEGORY_MAP = {
    # 유·공압기기
    "cylinder|실린더|air|에어|pneumatic|공압|유압|hydraulic|solenoid|솔레노이드|valve|밸브|filter|필터|regulator|레귤레이터": [
        "10352111",  # 공압기기
        "10352112",  # 유압기기
        "10352103",  # 공기압축기
    ],
    # 기계요소
    "bearing|베어링|chain|체인|belt|벨트|pulley|풀리|coupling|커플링|spring|스프링|gear|기어|감속기|reducer|jack|잭": [
        "10357608", "10357609", "10357610",  # 베어링 1~3
        "10357628", "10357624",              # 체인
        "10357611",                          # 벨트
        "10357621",                          # 풀리
        "10357619",                          # 커플링
        "10357602", "10357604", "10357631",  # 감속기
        "10357613",                          # 스프링
        "10357612",                          # 스크류잭
    ],
    # 전기자재·모터·센서
    "motor|모터|servo|서보|encoder|엔코더|sensor|센서|inverter|인버터|panel|판넬|switch|스위치|relay|릴레이|cable|케이블|wire|전선": [
        "10451502",  # 전동기(모터)
        "10451503",  # 인버터
        "10451510",  # 차단기
        "10451511",  # 릴레이
        "10451201",  # 전선
    ],
    # 공구·측정기
    "tool|공구|drill|드릴|torque|토크|gauge|게이지|measure|계측|caliper|버니어|micrometer|마이크로미터": [
        "10358106",  # 에어공구
        "10358117",  # 전동공구
        "10358123",  # 측정공구
        "10358129", "10358130",  # 일반공구
        "10358118", "10358131",  # 절삭공구
    ],
    # 공작기계·자동화
    "robot|로봇|press|프레스|lathe|선반|cnc|자동화|automation|conveyor|컨베이어|컨베어": [
        "10355647",  # 자동화기기
        "10355625",  # 프레스이송용로봇
        "10355609", "10355646",  # 선반
        "10353618",  # 운반기계
    ],
    # 용접
    "welding|용접|electrode|용접봉": [
        "10356102", "10356106",  # 용접봉
        "10356103",              # 용접용품
    ],
    # 건설기계
    "crane|크레인|hoist|호이스트|forklift|지게차|winch|윈치": [
        "10354113", "10354107",  # 크레인
        "10354114",              # 호이스트
        "10354105",              # 지게차
        "10354104",              # 윈치
    ],
}

# 카테고리 전체 탐색 목록 (키워드 매칭 실패 시)
FALLBACK_CATE_CDS = [
    "10352111", "10352112",  # 공압/유압기기
    "10357608", "10357609", "10357610",  # 베어링
    "10357628",  # 체인
    "10357611",  # 벨트
    "10355647",  # 자동화기기
    "10358106",  # 에어공구
    "10358123",  # 측정공구
]

_pw_ctx = None
_browser = None
_page = None


def _start():
    global _pw_ctx, _browser, _page
    if _page is None:
        _pw_ctx = sync_playwright().start()
        _browser = _pw_ctx.chromium.launch(headless=True)
        _page = _browser.new_page()
        _page.set_extra_http_headers({"Accept-Language": "ko-KR,ko;q=0.9"})
    return _page


def login(user_id: str = KPI_ID, user_pw: str = KPI_PW) -> tuple:
    """로그인. (성공여부, 메시지) 반환"""
    try:
        pg = _start()
        pg.goto(LOGIN_URL, timeout=20000, wait_until="domcontentloaded")
        pg.fill("input[name='user_id']", user_id)
        pg.fill("input[name='user_pw']", user_pw)
        pg.click("input[type='submit'], button[type='submit'], #sendLogin")
        try:
            pg.wait_for_url(lambda url: "login.asp" not in url, timeout=8000)
            return True, "로그인 성공"
        except PWTimeout:
            return False, "아이디 또는 비밀번호를 확인하세요"
    except Exception as e:
        return False, f"오류: {e}"


def _parse_detail_page(html: str, keyword: str) -> list:
    """detail.asp 가격 테이블 파싱"""
    results = []
    soup = BeautifulSoup(html, "html.parser")

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header_cells = rows[0].find_all(["th", "td"])
        headers = [c.get_text(strip=True) for c in header_cells]
        if not any(k in " ".join(headers) for k in ["품명", "규격", "단위"]):
            continue

        for row in rows[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 3:
                continue

            name = cells[0]
            spec = cells[1] if len(cells) > 1 else ""
            unit = cells[2] if len(cells) > 2 else ""

            if not name or len(name) < 2:
                continue

            # 가격 컬럼 (3번 이후)
            prices = []
            for val in cells[3:]:
                cleaned = val.replace(",", "").replace("원", "").strip()
                if re.match(r"^\d{3,9}$", cleaned):
                    try:
                        prices.append(int(cleaned))
                    except Exception:
                        pass

            if not prices:
                continue

            results.append({
                "품명": name,
                "규격": spec,
                "단위": unit,
                "단가": round(sum(prices) / len(prices)),
                "단가_최저": min(prices),
                "단가_최고": max(prices),
                "기준일": "",
                "출처": "한국물가정보(kpi.or.kr)",
            })

    return results


def _keyword_to_cate_cds(keyword: str) -> list:
    """키워드에 맞는 카테고리 코드 리스트 반환"""
    kw_lower = keyword.lower()
    for pattern, cate_cds in CATEGORY_MAP.items():
        if any(k in kw_lower for k in pattern.split("|")):
            return cate_cds
    return FALLBACK_CATE_CDS


def search_price(keyword: str, max_results: int = 20) -> list:
    """
    품목명으로 시장가 검색.
    반환: [{'품명','규격','단위','단가','단가_최저','단가_최고','기준일','출처'}, ...]
    """
    pg = _page
    if pg is None:
        return []

    all_results = []
    visited = set()
    cate_cds = _keyword_to_cate_cds(keyword)
    kw_lower = keyword.lower()

    for cate_cd in cate_cds:
        if cate_cd in visited:
            continue
        visited.add(cate_cd)

        try:
            url = f"{BASE_URL}/www/price/detail.asp?CATE_CD={cate_cd}"
            pg.goto(url, timeout=15000, wait_until="domcontentloaded")
            pg.wait_for_timeout(800)
            items = _parse_detail_page(pg.content(), keyword)

            # 키워드 포함 항목 우선 수집
            matched = [it for it in items if kw_lower in it["품명"].lower()
                       or kw_lower in it["규격"].lower()]
            all_results.extend(matched if matched else items)

        except Exception:
            continue

        if len(all_results) >= max_results:
            break

    # 중복 제거 (품명+규격 기준)
    seen = set()
    unique = []
    for it in all_results:
        key = f"{it['품명']}|{it['규격']}"
        if key not in seen:
            seen.add(key)
            unique.append(it)

    return unique[:max_results]


def close():
    global _pw_ctx, _browser, _page
    if _browser:
        _browser.close()
    if _pw_ctx:
        _pw_ctx.stop()
    _pw_ctx = _browser = _page = None


# ── 단독 실행 테스트 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    print("=== 한국물가정보 로그인 ===")
    ok, msg = login()
    print(f"결과: {'성공' if ok else '실패'} / {msg}")

    if ok:
        kw = input("검색 품목명: ").strip() or "베어링"
        print(f"\n=== '{kw}' 검색 ===")
        items = search_price(kw, max_results=15)
        if items:
            for it in items:
                print(f"  {it['품명'][:30]:<30} | {it['규격'][:20]:<20} | {it['단가']:>10,}원 | {it['단위']}")
        else:
            print("검색 결과 없음")

    close()
