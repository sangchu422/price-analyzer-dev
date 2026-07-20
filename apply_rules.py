"""
견적서_파싱규칙.md 에 정의된 규칙을 원본 아이템 리스트에 적용.

사용법:
    import apply_rules
    items = apply_rules.apply_all(items)
"""
import re


def apply_all(items: list) -> list:
    """모든 파싱 규칙을 순서대로 적용. 원본 리스트를 in-place 수정 후 반환."""
    stats = {}
    for it in items:
        for rule_fn in _RULES:
            changed = rule_fn(it)
            if changed:
                name = rule_fn.__name__
                stats[name] = stats.get(name, 0) + 1

    if stats:
        for rule_name, cnt in stats.items():
            print(f"  [규칙적용] {rule_name}: {cnt}건 수정")

    return items


# ──────────────────────────────────────────────────────────
# RULE-01: 단위 키워드 정규화
# ──────────────────────────────────────────────────────────
# 키워드 → 정규화된 단위값 (다른 표기를 하나로 통일할 때 값을 다르게 지정)
_UNIT_KEYWORDS = {
    '식':  '식',
    'EA':  'EA',
    'SET': 'SET',
    'M':   'M',
    'LOT': 'LOT',
    'AY':  'AY',
    'MD':  'M/D',
    'M/D': 'M/D',
}
_UNIT_COLS = ('규격', '품명', '메이커', '단위장비명')  # 단위 외에 잘못 들어올 수 있는 컬럼

def _rule_01_unit_keyword(it: dict) -> bool:
    """단위 키워드가 다른 컬럼에 단독으로 있으면 단위로 이동. 기존 단위값과 교환.
    단위 컬럼 자체의 표기도 정규화(예: MD → M/D)."""
    current_unit = it.get('단위', '').strip()
    canonical = _UNIT_KEYWORDS.get(current_unit)

    # 단위 컬럼 표기 정규화 (MD → M/D 등)
    if canonical and canonical != current_unit:
        it['단위'] = canonical
        return True

    # 이미 정규화된 단위 키워드이면 위치 이동 불필요
    if canonical:
        return False

    # 다른 컬럼에 단위 키워드가 있으면 단위로 이동
    for col in _UNIT_COLS:
        val = it.get(col, '').strip()
        if val in _UNIT_KEYWORDS:
            it[col] = current_unit  # 기존 단위값 → 해당 컬럼 (비어있으면 '')
            it['단위'] = _UNIT_KEYWORDS[val]
            return True

    return False


# ──────────────────────────────────────────────────────────
# RULE-02: 주요메이커 정규화
# ──────────────────────────────────────────────────────────
# 소문자 키 → 정규화된 메이커명 (대소문자 무관 매칭)
_MAKER_KEYWORDS = {
    'robostar': 'Robostar',
    'thk외':    'THK외',
    'omron':    'OMRON',
    'apex':     'APEX',
    'thk':      'THK',
    'unicon':   'Unicon',
    'keyence':  'Keyence',
    'advantech':'Advantech',
    'misumi':   'misumi',
    'festo':    'FESTO',
    '닛다모아':  '닛다모아',
    'smc':      'SMC',
}
_MAKER_COLS = ('규격', '품명', '단위장비명', '단위')  # 메이커 외에 잘못 들어올 수 있는 컬럼

def _rule_02_maker_keyword(it: dict) -> bool:
    """메이커 키워드가 다른 컬럼에 단독으로 있으면 메이커로 이동. 기존 메이커값과 교환."""
    current_maker = it.get('메이커', '').strip()

    for col in _MAKER_COLS:
        val = it.get(col, '').strip()
        canonical = _MAKER_KEYWORDS.get(val.lower())
        if canonical:
            it[col] = current_maker  # 기존 메이커값 → 해당 컬럼 (비어있으면 '')
            it['메이커'] = canonical
            return True

    return False


# ──────────────────────────────────────────────────────────
# RULE-03: 규격 키워드 — 단위 컬럼에 잘못 들어온 경우 규격으로 이동
# ──────────────────────────────────────────────────────────
_SPEC_KEYWORDS = {
    '19Ø(녹색)', '12Ø(흑색)', '16x12(녹색)', '12*8(적색)', '12*8(흑색)',
    '1G25(노랑)', '1G25(녹)', '1X2X0.64', '1X2X0.8',
    '4G 0.75', '4G 1', '25G 0.75', '15G 0.75', '12G 0.75', '4G 2.5',
    '7G 1', '3G 25', '1C*25SQ(G)', '1C*25SQ(BK)', '25G*0.75SQ', '7G*1.0SQ',
}
_SPEC_SOURCE_COLS = ('단위',)  # 규격이 잘못 들어올 수 있는 컬럼

def _rule_03_spec_keyword(it: dict) -> bool:
    """규격 키워드가 단위 컬럼에 있으면 규격으로 이동. 기존 규격값은 삭제."""
    for col in _SPEC_SOURCE_COLS:
        val = it.get(col, '').strip()
        if val in _SPEC_KEYWORDS:
            it['규격'] = val
            it[col] = ''
            return True
    return False


# ──────────────────────────────────────────────────────────
# RULE-04: 품명 유효성 정정 ("기계" 등)
# ──────────────────────────────────────────────────────────
_INVALID_품명 = {'기계'}  # 품명으로 쓰일 수 없는 값

def _rule_04_invalid_품명(it: dict) -> bool:
    """품명이 '기계' 등 유효하지 않은 값이면:
      1. 규격 → 품명으로 이동, 규격 비우기
      2. 규격이 비었고 메이커에 메이커가 아닌 값이 있으면 → 규격으로 이동"""
    if it.get('품명', '').strip() not in _INVALID_품명:  # noqa: E501
        return False

    spec = it.get('규격', '').strip()
    it['품명'] = spec
    it['규격'] = ''

    # 규격이 비었고 메이커에 비메이커 값이 있으면 → 규격으로
    maker = it.get('메이커', '').strip()
    if not spec and maker and maker.lower() not in _MAKER_KEYWORDS:
        it['규격'] = maker
        it['메이커'] = ''

    return True


# ──────────────────────────────────────────────────────────
# RULE-05: 수동 보정 (파싱 오류로 컬럼이 완전히 뒤바뀐 특정 항목)
# ──────────────────────────────────────────────────────────
# 형식: ({'match_col': 'match_val', ...}, {'set_col': 'set_val', ...})
# match 조건을 모두 만족하는 아이템에 set 값을 덮어씀
_MANUAL_CORRECTIONS = [
    (
        {'품명': "MBC ASS'Y", '메이커': 'OIL JET VISION UNIT'},
        {'품명': 'OIL JET VISION UNIT', '규격': "MBC ASS'Y", '메이커': ''},
    ),
    # 메이커에 'BED EQUIPMENT'가 있으면 규격으로 이동
    (
        {'메이커': 'BED EQUIPMENT'},
        {'규격': 'BED EQUIPMENT', '메이커': ''},
    ),
    # 서버 전송 프로그램은 단위=식
    (
        {'품명': '서버 전송 프로그램'},
        {'단위': '식'},
    ),
]

def _rule_05_manual_corrections(it: dict) -> bool:
    """수동 보정 목록(_MANUAL_CORRECTIONS)과 일치하는 항목을 직접 수정."""
    for match, patch in _MANUAL_CORRECTIONS:
        if all(it.get(col, '').strip() == val for col, val in match.items()):
            it.update(patch)
            return True
    return False


# ──────────────────────────────────────────────────────────
# RULE-06: 품명 = 규격 중복 제거
# ──────────────────────────────────────────────────────────
def _rule_06_품명_규격_중복(it: dict) -> bool:
    """품명과 규격이 동일한 값이면:
      - 단위가 모델번호(단위 키워드가 아닌 값)이면 → 단위→규격, 단위=EA
      - 단위가 정상값이거나 비었으면 → 규격만 비우기
    """
    name = it.get('품명', '').strip()
    spec = it.get('규격', '').strip()
    if not (name and spec and name == spec):
        return False

    unit_val = it.get('단위', '').strip()
    if unit_val and unit_val not in _UNIT_KEYWORDS:
        it['규격'] = unit_val
        it['단위'] = 'EA'
    else:
        it['규격'] = ''
    return True


# ──────────────────────────────────────────────────────────
# RULE-08: 카테고리 레이블 품명 → 3단 컬럼 시프트 + 단위=EA
# ──────────────────────────────────────────────────────────
_CATEGORY_LABELS = {'구매품', '(유공압품외)', 'DIVERTER', 'TURNING POSITION'}
_OP_LABEL_RE = re.compile(r'^\(OP[\w,\s]+\)$')

def _rule_08_category_label_shift(it: dict) -> bool:
    """품명이 카테고리 레이블(구매품, (유공압품외), DIVERTER, TURNING POSITION, (OP패턴))이면:
      규격→품명, 단위→규격, 단위=EA
    """
    name = it.get('품명', '').strip()
    if name not in _CATEGORY_LABELS and not _OP_LABEL_RE.match(name):
        return False
    spec = it.get('규격', '').strip()
    if not spec:
        return False
    unit_val = it.get('단위', '').strip()
    it['품명'] = spec
    it['규격'] = unit_val if unit_val and unit_val not in _UNIT_KEYWORDS else ''
    it['단위'] = 'EA'
    return True


# ──────────────────────────────────────────────────────────
# RULE-09: 자재비+철자재 패턴 → 단위가 실제 품명
# ──────────────────────────────────────────────────────────
def _rule_09_자재비_철자재(it: dict) -> bool:
    """품명=자재비 AND 규격=철자재이면: 단위(실제품명) → 품명, 단위 비우기.
    이후 RULE-07이 단가 기준으로 단위를 채움(빈 경우 SET 이상)."""
    if it.get('품명', '').strip() != '자재비':
        return False
    if it.get('규격', '').strip() != '철자재':
        return False
    unit_val = it.get('단위', '').strip()
    if not unit_val or unit_val in _UNIT_KEYWORDS:
        return False
    it['품명'] = unit_val
    it['규격'] = '철자재'
    it['단위'] = ''
    return True


# ──────────────────────────────────────────────────────────
# RULE-10: (NSET) 형태 품명 → 컬럼 시프트 + 단위=SET
# ──────────────────────────────────────────────────────────
_NSET_RE = re.compile(r'^\(\d+SET\)$', re.IGNORECASE)

def _rule_10_nset_shift(it: dict) -> bool:
    """품명이 (N개SET) 형태이면: 규격→품명, 단위→규격, 단위=SET"""
    name = it.get('품명', '').strip()
    if not _NSET_RE.match(name):
        return False
    spec = it.get('규격', '').strip()
    if not spec:
        return False
    unit_val = it.get('단위', '').strip()
    it['품명'] = spec
    it['규격'] = unit_val if unit_val and unit_val not in _UNIT_KEYWORDS else ''
    it['단위'] = 'SET'
    return True


# ──────────────────────────────────────────────────────────
# RULE-07: 단위 공백 시 단가 기준 자동 추론
# ──────────────────────────────────────────────────────────
def _rule_07_unit_from_price(it: dict) -> bool:
    """단위가 비어있을 때 단가_원 기준으로 단위를 추론해 채움.
      단가 < 100만  → EA
      100만 ≤ 단가 < 1000만 → SET
      단가 ≥ 1000만 → 식
    """
    if it.get('단위', '').strip():
        return False
    단가 = it.get('단가_원', 0)
    if 단가 < 1_000_000:
        it['단위'] = 'EA'
    elif 단가 < 10_000_000:
        it['단위'] = 'SET'
    else:
        it['단위'] = '식'
    return True


# 적용할 규칙 함수 목록 (순서 중요: 컬럼 재배치 → 정규화 → 단위 추론 순)
_RULES = [
    _rule_01_unit_keyword,         # 단위 키워드 다른 컬럼에서 이동
    _rule_02_maker_keyword,        # 메이커 키워드 다른 컬럼에서 이동
    _rule_03_spec_keyword,         # 규격 키워드 단위 컬럼에서 이동
    _rule_08_category_label_shift, # 카테고리 레이블 3단 시프트
    _rule_09_자재비_철자재,         # 자재비+철자재 → 단위→품명
    _rule_10_nset_shift,           # (NSET) 3단 시프트
    _rule_04_invalid_품명,         # 유효하지 않은 품명 정정
    _rule_05_manual_corrections,   # 수동 보정 (서버전송프로그램 포함)
    _rule_06_품명_규격_중복,        # 품명=규격 중복 제거 (모델번호 처리 포함)
    _rule_07_unit_from_price,      # 단위 공백 시 단가 기준 추론 (마지막 실행)
]
