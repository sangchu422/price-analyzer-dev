"""
표준 레이아웃 파서 — 현대위아 통일양식
구조: 갑지(헤더 정보) + 단위장비 시트들(데이터 테이블)

갑지 시트에서 추출:
  - 공급사명, 견적번호, 견적일, 공사명, 총금액

단위장비 시트에서 추출:
  - 품명 | 규격 | 단위 | 수량 | 단가 | 금액 | (메이커)
"""
import re
from datetime import date

# 헤더 시트 — 데이터 추출 대상 아님
_SKIP_SHEETS = {'갑지', '견적서', '공통', 'PALLET', 'Sheet1', '차량별합계', '첨부'}
_SKIP_SHEET_KW = ['합계', '총괄', '요약', '첨부', '조건']

# 데이터 행 필터 — 이 단어가 품명에 있으면 스킵
_SKIP_ROW_KW = [
    '소계', '합계', '이윤', '관리비', '노무비', '경비', '철거',
    '설계', '제작', '조립', '설치', '시운전', '도장', '포장',
    '운송', '안전', '순번', '원가', '직접비', 'GRAND TOTAL',
    '소 계', '합 계', '대당단가', '항공료',
]


def parse_header(sheets: dict, file_name: str = "") -> dict:
    """갑지 시트에서 메타 정보 추출."""
    meta = {
        "vendor": None, "quote_no": None,
        "quote_date": None, "project": None, "total_amount": None,
    }
    target = sheets.get("갑지") or sheets.get("견적서")
    if not target:
        return meta

    for row in target:
        joined = " ".join(row)

        # 견적번호
        if any(kw in joined for kw in ["견적번호", "견적No", "견적NO"]):
            for i, cell in enumerate(row):
                if any(kw in cell for kw in ["견적번호", "견적No", "견적NO"]):
                    for j in range(i + 1, len(row)):
                        if row[j] and not _is_number(row[j]):
                            meta["quote_no"] = meta["quote_no"] or row[j].strip()
                            break

        # 견적일 / 견적일자 / 제출일
        if any(kw in joined for kw in ["견적일", "제출일", "작성일"]):
            for cell in row:
                d = _parse_date(cell)
                if d:
                    meta["quote_date"] = meta["quote_date"] or d

        # 공사명 — "공사명 : XXX" 형식(단일 셀) 또는 다음 셀
        if any(kw in joined for kw in ["공사명", "프로젝트명", "건명"]):
            for i, cell in enumerate(row):
                if any(kw in cell for kw in ["공사명", "프로젝트명", "건명"]):
                    # "공사명 : HVAC Assy Line SX3i" 형식 처리
                    if ":" in cell or "：" in cell:
                        parts = cell.replace("：", ":").split(":", 1)
                        if len(parts) == 2 and parts[1].strip():
                            meta["project"] = meta["project"] or parts[1].strip()
                            continue
                    # 다음 셀에서 추출
                    for j in range(i + 1, len(row)):
                        if row[j] and len(row[j]) > 2:
                            meta["project"] = meta["project"] or row[j].strip()
                            break

        # 총금액 — "견적금액: XXX원" 형식(단일 셀) 또는 숫자 셀
        if any(kw in joined for kw in ["견적금액", "총금액", "합계금액"]):
            nums = _extract_numbers(row)
            if nums and not meta["total_amount"]:
                meta["total_amount"] = int(max(nums))

    # 공급사 — 이 양식에는 갑지에 공급사명 셀이 없는 경우 多
    # 파일명에서 추출 시도: "5. 견적서(화인).xlsx" → "화인"
    if not meta["vendor"] and file_name:
        import re
        m = re.search(r"[（(]([^)）]{2,20})[)）]", file_name)
        if m:
            candidate = m.group(1)
            # 숫자만이거나 날짜 패턴이면 제외
            if not _is_number(candidate) and not re.match(r"\d{4}-\d{2}", candidate):
                meta["vendor"] = candidate

    return meta


def parse_items(sheets: dict) -> list[dict]:
    items = []
    for sh_name, rows in sheets.items():
        if _is_skip_sheet(sh_name):
            continue
        unit_name = _extract_unit_name(sh_name, rows)
        header_row_idx = _find_header_row(rows)
        if header_row_idx is None:
            continue
        col_map = _map_columns(rows[header_row_idx])
        if not col_map.get("unit_price") and not col_map.get("amount"):
            continue

        for row in rows[header_row_idx + 1:]:
            item = _parse_row(row, col_map, unit_name)
            if item:
                items.append(item)
    return items


# ── 내부 헬퍼 ──────────────────────────────────────────────────

def _is_skip_sheet(name: str) -> bool:
    if name in _SKIP_SHEETS:
        return True
    return any(kw in name for kw in _SKIP_SHEET_KW)


def _extract_unit_name(sh_name: str, rows: list) -> str:
    """시트 상단 1~4행에서 단위장비명 추출, 없으면 시트명 사용."""
    for row in rows[:4]:
        clean = [c for c in row if c and len(c) > 2
                 and '단위공사명' not in c and c != '□']
        if clean:
            return clean[0]
    return sh_name


_HEADER_KW = {"품명", "규격", "수량", "단가", "금액", "단위", "품  명", "단 가", "금 액"}


def _find_header_row(rows: list) -> int | None:
    for i, row in enumerate(rows):
        hits = sum(1 for cell in row if any(kw in cell for kw in _HEADER_KW))
        if hits >= 3:
            return i
    return None


def _map_columns(header_row: list) -> dict:
    """헤더 행에서 컬럼 인덱스 매핑."""
    mapping = {}
    for i, cell in enumerate(header_row):
        c = cell.replace(" ", "")
        if "품명" in c:
            mapping["item_name"] = i
        elif "규격" in c:
            mapping["spec"] = i
        elif "단위" in c and "단가" not in c:
            mapping["unit"] = i
        elif "수량" in c:
            mapping["quantity"] = i
        elif "단가" in c:
            mapping["unit_price"] = i
        elif "금액" in c:
            mapping["amount"] = i
        elif any(kw in c for kw in ["메이커", "제조사", "브랜드", "MAKER"]):
            mapping["maker"] = i
    return mapping


def _parse_row(row: list, col_map: dict, unit_name: str) -> dict | None:
    def get(key):
        idx = col_map.get(key)
        return row[idx] if idx is not None and idx < len(row) else ""

    item_name = get("item_name")
    if not item_name or len(item_name) < 2:
        return None
    if any(kw in item_name for kw in _SKIP_ROW_KW):
        return None
    if item_name.startswith("0") or _is_number(item_name):
        return None

    unit_price = _to_int(get("unit_price"))
    amount     = _to_int(get("amount"))
    quantity   = _to_float(get("quantity"))

    if unit_price is None and amount is None:
        return None

    # 컬럼 매핑이 없으면 숫자 위치로 폴백
    if unit_price is None or amount is None:
        nums = _extract_numbers(row)
        if len(nums) >= 2:
            amount     = amount     or int(nums[-1])
            unit_price = unit_price or int(nums[-2])
            quantity   = quantity   or (nums[-3] if len(nums) >= 3 else None)

    if not unit_price or unit_price <= 0:
        return None

    return {
        "unit_name":  unit_name,
        "item_name":  item_name,
        "spec":       get("spec"),
        "unit":       get("unit"),
        "quantity":   quantity,
        "unit_price": unit_price,
        "amount":     amount,
        "maker":      get("maker"),
        "category":   "설비",
    }


# ── 유틸 ──────────────────────────────────────────────────────

def _parse_date(s: str):
    if not s:
        return None
    s = s.strip()
    # "2025-09-29 00:00:00" or "2025-09-29"
    m = re.search(r"(\d{4})[-./년](\d{1,2})[-./월](\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass
    return None


def _is_number(s: str) -> bool:
    try:
        float(s.replace(",", ""))
        return True
    except ValueError:
        return False


def _to_int(s: str):
    try:
        return int(float(s.replace(",", "")))
    except (ValueError, AttributeError):
        return None


def _to_float(s: str):
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _extract_numbers(row: list) -> list[float]:
    result = []
    for cell in row:
        try:
            result.append(float(cell.replace(",", "")))
        except (ValueError, AttributeError):
            pass
    return result
