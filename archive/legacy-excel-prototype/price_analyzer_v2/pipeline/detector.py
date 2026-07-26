"""
레이아웃 그룹 자동 감지

그룹           특징 시트명                                   파서
──────────────────────────────────────────────────────────────
standard       갑지 + 단위장비 키워드 시트                   parsers/standard.py
assembly       견적서(요약) + 숫자-번호 세부 시트            parsers/assembly.py
unknown        위 어느 것도 아님 → parse_log에 기록
"""

# 표준 레이아웃: 시트명에 이 키워드가 하나라도 있으면 standard
_STANDARD_SH_KW = [
    'HVAC', 'ASSY', 'ASS', '조립기', '압입기', '도포기',
    '포밍기', '검사기', '이재', '컨베어', 'OTHER', 'VISION',
    '서브', 'I-Pin', 'U핀', 'I핀', '코어', '절연', '인서팅',
    '확관', '트위스팅', '하부', '납품', '친수', '바이오', '코팅',
    '라인', 'LINE', 'COST', '단위장비',
]

# 조립기 레이아웃: 시트명이 숫자 패턴 (10-1, 20, 30 …)
import re
_NUMBERED_SHEET = re.compile(r"^\d{1,3}(-\d)?$")


def detect(sheets: dict[str, list]) -> str:
    names = list(sheets.keys())
    all_names_str = " ".join(names)

    # 1순위: 숫자 패턴 시트가 2개 이상 → assembly (standard보다 먼저 체크)
    numbered = sum(1 for n in names if _NUMBERED_SHEET.match(n.strip()))
    if numbered >= 2:
        return "assembly"

    # 2순위: '단 위별_' 패턴 시트가 있으면 assembly
    if any("단 위별" in n or "단위별" in n for n in names):
        return "assembly"

    # 3순위: 시트명에 표준 키워드가 있으면 standard
    if any(kw in all_names_str for kw in _STANDARD_SH_KW):
        return "standard"

    # 4순위: '갑지' 시트가 있으면 standard (현대위아 통일양식)
    if "갑지" in names or any("갑지" in n for n in names):
        return "standard"

    return "unknown"
