"""
조립기 레이아웃 파서
구조: 견적서(요약) + 단 위별_XXX(롤업) + 숫자-번호 세부 시트

세부 시트 (10-1, 10-2, 30 …) 구조:
  행01: [공정명 타이틀]
  행02: [위치 구분, 설명, 수량]
  행03: [위치, 품 명, 규격, 단위, 단가(원), 금액(원)]
  행04+: 데이터 행
"""
import re
from pipeline.parsers.standard import (
    _to_int, _to_float, _extract_numbers, _parse_date, _is_number
)

_SKIP_SHEETS = {'견적서', 'Sheet1', '공통'}
_SKIP_SHEET_KW = ['단 위별', '단위별', '요약', '합계', '총괄']
_SKIP_ROW_KW = [
    '소계', '합계', '이윤', '관리비', '노무비', '경비', '합 계', '소 계',
    'GRAND TOTAL', '구분', '장치', '내 용', '내용',
]

_NUMBERED_SHEET = re.compile(r"^\d{1,3}(-\d)?$")
_HEADER_KW = {"품명", "품 명", "규격", "수량", "단가", "금액"}


def parse_header(sheets: dict, file_name: str = "") -> dict:
    meta = {
        "vendor": None, "quote_no": None,
        "quote_date": None, "project": None, "total_amount": None,
    }
    target = sheets.get("견적서") or sheets.get("Sheet1")
    if not target:
        return meta

    for row in target:
        joined = " ".join(row)
        if any(kw in joined for kw in ["견적번호", "견적No", "견적NO"]):
            for cell in row:
                if cell and cell not in ("견적번호", "견적No", "견적NO"):
                    meta["quote_no"] = meta["quote_no"] or cell.strip()
        if any(kw in joined for kw in ["견적일", "제출일"]):
            for cell in row:
                d = _parse_date(cell)
                if d:
                    meta["quote_date"] = meta["quote_date"] or d
        if any(kw in joined for kw in ["상호", "공급사", "회사명"]):
            for cell in row:
                if cell and len(cell) >= 3 and cell not in ("상호", "공급사", "회사명"):
                    meta["vendor"] = meta["vendor"] or cell.strip()
        if any(kw in joined for kw in ["공사명", "건명", "프로젝트"]):
            for cell in row:
                if cell and len(cell) > 4 and cell not in ("공사명", "건명", "프로젝트"):
                    meta["project"] = meta["project"] or cell.strip()

    return meta


def parse_items(sheets: dict) -> list[dict]:
    items = []
    for sh_name, rows in sheets.items():
        if not _is_detail_sheet(sh_name):
            continue
        unit_name = _sheet_title(sh_name, rows)
        header_idx = _find_header(rows)
        if header_idx is None:
            continue
        col_map = _map_cols(rows[header_idx])

        for row in rows[header_idx + 1:]:
            item = _parse_row(row, col_map, unit_name)
            if item:
                items.append(item)
    return items


def _is_detail_sheet(name: str) -> bool:
    if name in _SKIP_SHEETS:
        return False
    if any(kw in name for kw in _SKIP_SHEET_KW):
        return False
    return True


def _sheet_title(sh_name: str, rows: list) -> str:
    if rows:
        first = [c for c in rows[0] if c and len(c) > 2]
        if first:
            return first[0]
    return sh_name


def _find_header(rows: list) -> int | None:
    for i, row in enumerate(rows):
        hits = sum(1 for cell in row if any(kw in cell for kw in _HEADER_KW))
        if hits >= 2:
            return i
    return None


def _map_cols(header_row: list) -> dict:
    mapping = {}
    for i, cell in enumerate(header_row):
        c = cell.replace(" ", "")
        if "품명" in c or "장치" in c or "내용" in c:
            if "item_name" not in mapping:
                mapping["item_name"] = i
        elif "규격" in c:
            mapping["spec"] = i
        elif "단위" in c and "단가" not in c:
            mapping["unit"] = i
        elif "수량" in c:
            mapping["quantity"] = i
        elif "단가" in c:
            mapping["unit_price"] = i
        elif "금액" in c or "견적" in c:
            if "amount" not in mapping:
                mapping["amount"] = i

    # 폴백: item_name이 없으면 단가 앞의 첫 번째 텍스트 컬럼 사용
    if "item_name" not in mapping and "unit_price" in mapping:
        price_col = mapping["unit_price"]
        for i in range(price_col - 1, -1, -1):
            if header_row[i] and not any(
                kw in header_row[i] for kw in ["단위", "수량", "구분", "번호"]
            ):
                mapping["item_name"] = i
                break

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
    if _is_number(item_name):
        return None

    unit_price = _to_int(get("unit_price"))
    amount     = _to_int(get("amount"))
    quantity   = _to_float(get("quantity"))

    if unit_price is None or unit_price <= 0:
        nums = _extract_numbers(row)
        if len(nums) >= 2:
            amount     = int(nums[-1])
            unit_price = int(nums[-2])
            quantity   = nums[-3] if len(nums) >= 3 else None

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
        "maker":      "",
        "category":   "설비",
    }
