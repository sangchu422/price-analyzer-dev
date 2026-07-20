"""
PDF 견적서 파서 — 현대위아 통일양식 (단위장비별 세부시트 구조)

구조:
  1페이지: 갑지 (메타 정보)
  2-3페이지: 종합표 (스킵)
  4페이지~: "□ 전기부문: XXX (첨부)" 또는 "□ 단위공사명(전기제외): XXX (첨부)"
            각 페이지에 표 구조:
            구분 | 품명 | 규격 | 단위 | 수량 | 단가(원) | 금액(원) | 原MAKER
"""
import re
import pdfplumber

_SKIP_ITEM_KW = [
    '소계', '합계', '자재비', '소 계', '합 계',
    '설치/해체', '시운전 공사', '공사 관련',
    '일반관리', '이윤', '기계 소계', '전기 소계',
]
_SKIP_IF_STARTS = ['■', '□', '※', '●', '○']
_CATEGORY_KW = ['철자재', '구매품', '외주제작품', '자재', '비']

_UNIT_NAME_RE = re.compile(
    r'(?:단위공사명\(전기제외\)|전기부문)\s*[:：]\s*(.+?)\s*(?:\(첨부\)|$)',
    re.MULTILINE
)
_META_DATE_RE  = re.compile(r'견적일자\s+(\d{4}-\d{2}-\d{2})')
_META_NO_RE    = re.compile(r'견적번호\s+([\w\-/]+)')
_META_VENDOR_RE = re.compile(r'見\s*積\s*書\s+현대\s*위아\s+貴下\s+(.+)')
_META_TOTAL_RE  = re.compile(r'\\([\d,]+)\s*[）\)]')


def _clean(v):
    if v is None:
        return ""
    return str(v).strip().replace('\n', ' ')


def _to_int(s):
    try:
        return int(str(s).replace(',', '').strip())
    except Exception:
        return None


def _to_float(s):
    try:
        return float(str(s).replace(',', '').strip())
    except Exception:
        return None


def _should_skip_item(name: str) -> bool:
    if not name or len(name) < 2:
        return True
    if any(kw in name for kw in _SKIP_ITEM_KW):
        return True
    if name[0] in _SKIP_IF_STARTS:
        return True
    # 숫자만인 경우
    if re.match(r'^[\d,.\s]+$', name):
        return True
    return False


def extract_meta(pdf_path: str) -> dict:
    """1페이지에서 헤더 정보 추출"""
    meta = {"vendor": None, "quote_no": None, "quote_date": None, "total_amount": None}
    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[0].extract_text() or ""

    m = _META_VENDOR_RE.search(text)
    if m:
        meta["vendor"] = m.group(1).strip()

    m = _META_DATE_RE.search(text)
    if m:
        meta["quote_date"] = m.group(1)

    m = _META_NO_RE.search(text)
    if m:
        meta["quote_no"] = m.group(1).strip()

    m = _META_TOTAL_RE.search(text)
    if m:
        meta["total_amount"] = _to_int(m.group(1))

    return meta


def _parse_detail_table(table, unit_name: str, file_meta: dict) -> list:
    """세부 시트 테이블에서 라인 아이템 추출"""
    items = []
    header_found = False

    for row in table:
        if row is None or len(row) < 7:
            continue

        # 헤더 행 감지 (품 명 / 규격 / 단위 등)
        row_text = " ".join(_clean(c) for c in row)
        if "품 명" in row_text or "단가(원)" in row_text:
            header_found = True
            continue

        if not header_found:
            continue

        # 열 위치: 테이블이 9열 구조 (구분x2, 품명, 규격, 단위, 수량, 단가, 금액, 메이커)
        # 또는 단순 구조에 따라 유연하게 처리
        if len(row) >= 9:
            품명 = _clean(row[2])
            규격 = _clean(row[3])
            단위 = _clean(row[4])
            수량_raw = _clean(row[5])
            단가_raw = _clean(row[6])
            금액_raw = _clean(row[7])
            메이커 = _clean(row[8])
        elif len(row) >= 7:
            품명 = _clean(row[1])
            규격 = _clean(row[2])
            단위 = _clean(row[3])
            수량_raw = _clean(row[4])
            단가_raw = _clean(row[5])
            금액_raw = _clean(row[6])
            메이커 = _clean(row[7]) if len(row) > 7 else ""
        else:
            continue

        # 카테고리 텍스트가 품명에 섞인 경우 첫 줄만 사용
        if '\n' in 품명:
            품명 = 품명.split('\n')[0].strip()

        if _should_skip_item(품명):
            continue

        단가 = _to_int(단가_raw)
        금액 = _to_int(금액_raw)
        수량 = _to_float(수량_raw)

        if not 단가 or 단가 <= 0:
            continue
        if not 금액 or 금액 < 1000:
            continue

        items.append({
            "단위장비명": unit_name,
            "품명": 품명,
            "규격": 규격,
            "단위": 단위,
            "수량": 수량,
            "단가_원": 단가,
            "금액_원": 금액,
            "메이커": 메이커,
        })

    return items


def extract_items(pdf_path: str, file_meta: dict) -> list:
    """PDF 전체에서 라인 아이템 추출"""
    all_items = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""

            # 단위장비명 추출
            m = _UNIT_NAME_RE.search(text)
            if not m:
                continue
            unit_name = m.group(1).strip()

            # 테이블 추출
            tables = page.extract_tables()
            for table in tables:
                if not table:
                    continue
                # 헤더 있는 테이블만 처리 (품 명 컬럼 포함 여부 확인)
                table_text = " ".join(
                    _clean(c) for row in table[:3] for c in (row or [])
                )
                if "품 명" not in table_text and "단가" not in table_text:
                    continue
                items = _parse_detail_table(table, unit_name, file_meta)
                all_items.extend(items)

    return all_items
