import json, openpyxl
from pathlib import Path

folder = Path(__file__).parent / "견적서"

file_meta = {
    "5-1. 견적서_HVAC.xlsx":       {"공급사": "FTC", "공사명": "HVAC Assy Line SX3i", "견적번호": "FTC-2530", "견적일": "2025-09-29"},
    "5-2. 견적서_쿨링모듈.xlsx":    {"공급사": "FTC", "공사명": "C Module Assy Table Line SX3i", "견적번호": "FTC-2531", "견적일": "2025-09-29"},
    "5. 견적서(남경테크윈).xlsx":   {"공급사": "남경테크윈", "공사명": "전동기 설비 라인", "견적번호": "NKT25226-0902-C-08", "견적일": "2025-09-05"},
    "5. 견적서(화인) (1).xlsx":     {"공급사": "주식회사 화인", "공사명": "TMED-II 자동화라인(턴키)", "견적번호": "ES25-094M-C", "견적일": "2025-09-10"},
    "5. 견적서(화인).xlsx":         {"공급사": "주식회사 화인", "공사명": "TMED-II 함침라인(턴키)", "견적번호": "ES25-094M-L", "견적일": "2025-09-10"},
    "새 Microsoft Excel 워크시트 - 복사본 (2).xlsx": {"공급사": "자동차부품주식회사", "공사명": "메인조립기", "견적번호": "미상", "견적일": "2025-01-01"},
    "새 Microsoft Excel 워크시트 - 복사본 (3).xlsx": {"공급사": "자동차부품주식회사", "공사명": "헤드서브조립기", "견적번호": "미상", "견적일": "2025-01-01"},
    "새 Microsoft Excel 워크시트 - 복사본 (4).xlsx": {"공급사": "부남테크", "공사명": "KMX SVM 차종설치 시운전", "견적번호": "DM250313-001", "견적일": "2026-07-07"},
    "새 Microsoft Excel 워크시트 - 복사본 (5).xlsx": {"공급사": "주식회사 아성", "공사명": "호원 BJ1차종 로봇설비 티칭", "견적번호": "DM250429-001", "견적일": "2026-07-07"},
    "새 Microsoft Excel 워크시트 - 복사본.xlsx":     {"공급사": "협력사미상", "공사명": "CORE BUILDER 4종", "견적번호": "STEM2508-016/17/18/19", "견적일": "2025-09-22"},
    "새 Microsoft Excel 워크시트.xlsx":              {"공급사": "영산테크노", "공사명": "친수/바이오 코팅 라인", "견적번호": "YS-25-P-124", "견적일": "2025-10-20"},
}

SKIP_KW = [
    '소계', '합계', '이윤', '관리비', '노무비', '경비', 'M/D', '철거', '양산',
    '설계', '제작', '조립', '설치', '시운전', '도장', '포장', '운송', '안전',
    '순번', '원가', '직접비', '표준안전', '이벤트', '티칭', '품확', '추가공사',
    '대당단가', '항공료', '일반구매품', '외주제작품', '자재비 소계', 'GRAND TOTAL',
    '소 계', '합 계',
]

STANDARD_SH_KW = [
    '단위장비', '공사(라인)', 'HVAC조립', 'ASSY', 'ASS',
    '성형기', '압입기', '도포기', '포밍기', '조립기', '검사기',
    '이재', '컨베어', 'OTHER', 'VISION', '서브', 'I-Pin', 'U핀', 'I핀',
    '코어', '절연', '인서팅', '확관', '트위스팅', '하부', '납품',
    '친수', '바이오', '코팅',
]

ASSEMBLY_SKIP_SH = ['견적서', '상세 견적_메인조립기', '상세 견적_헤드서브조립기',
                    'Sheet1', '공통', 'PALLET']


def extract_nums_texts(row):
    nums, texts = [], []
    for c in row:
        if not c.strip() or c == 'None':
            continue
        try:
            nums.append(float(c.replace(',', '')))
        except ValueError:
            texts.append(c)
    return nums, texts


def parse_item_row(row, unit_name, min_amount=1000):
    nums, texts = extract_nums_texts(row)
    if len(nums) < 2 or len(texts) < 1:
        return None
    try:
        금액 = nums[-1]
        단가 = nums[-2]
        수량 = nums[-3] if len(nums) >= 3 else None
        if 단가 <= 0 or 금액 < min_amount:
            return None
        품명 = texts[0]
        규격 = texts[1] if len(texts) > 1 else ""
        단위 = texts[2] if len(texts) > 2 else ""
        메이커 = texts[-1] if len(texts) > 3 else ""
        if not 품명 or 품명.startswith('0') or len(품명) < 2:
            return None
        if any(k in 품명 for k in SKIP_KW):
            return None
        return {
            '단위장비명': unit_name, '품명': 품명, '규격': 규격,
            '단위': 단위, '수량': 수량, '단가_원': int(단가),
            '금액_원': int(금액), '메이커': 메이커,
        }
    except Exception:
        return None


def parse_standard(sheets):
    items = []
    for sh_name, rows in sheets.items():
        if not any(k in sh_name for k in STANDARD_SH_KW):
            continue
        unit_name = sh_name
        for row in rows[:4]:
            clean = [c for c in row if c.strip() and '단위공사명' not in c
                     and '(첨부)' not in c and c != '□']
            if clean and len(clean[0]) > 2 and not clean[0].startswith('□'):
                unit_name = clean[0]
                break
        for row in rows:
            it = parse_item_row(row, unit_name)
            if it:
                items.append(it)
    return items


def parse_assembly(sheets):
    items = []
    for sh_name, rows in sheets.items():
        if sh_name in ASSEMBLY_SKIP_SH:
            continue
        unit_name = sh_name
        for row in rows:
            clean = [c for c in row if c.strip() and c != 'None']
            if any(k in ' '.join(clean) for k in ['소계', 'GRAND TOTAL', '구분', '장치', '내 용']):
                continue
            it = parse_item_row(row, unit_name)
            if it:
                items.append(it)
    return items


def parse_installation(sheets):
    items = []
    for sh_name, rows in sheets.items():
        if '공사(라인)' not in sh_name:
            continue
        unit_name = sh_name
        for row in rows[:4]:
            clean = [c for c in row if c.strip() and '단위공사명' not in c
                     and '(첨부)' not in c and c != '□']
            if clean and len(clean[0]) > 2:
                unit_name = clean[0]
                break
        for row in rows:
            clean = [c for c in row if c.strip() and c != 'None']
            if any(k in ' '.join(clean) for k in SKIP_KW):
                continue
            it = parse_item_row(row, unit_name, min_amount=100)
            if it and len(it['품명']) >= 2:
                items.append(it)
    return items


def read_xlsx(fpath):
    sheets = {}
    try:
        wb = openpyxl.load_workbook(str(fpath), data_only=True)
        for sh_name in wb.sheetnames:
            ws = wb[sh_name]
            rows = []
            for row in ws.iter_rows(max_row=500, values_only=True):
                r = [str(v).strip() if v is not None else '' for v in row]
                if any(v for v in r):
                    rows.append(r)
            sheets[sh_name] = rows
    except Exception as e:
        print(f"  오류: {e}")
    return sheets


ASSEMBLY_FILES = {
    "새 Microsoft Excel 워크시트 - 복사본 (2).xlsx",
    "새 Microsoft Excel 워크시트 - 복사본 (3).xlsx",
}
INSTALLATION_FILES = {
    "새 Microsoft Excel 워크시트 - 복사본 (4).xlsx",
    "새 Microsoft Excel 워크시트 - 복사본 (5).xlsx",
}

all_items = []
item_id = 1
stats = {}

for fpath in sorted(folder.glob("*.xlsx")):
    fname = fpath.name
    if fname not in file_meta:
        continue
    meta = file_meta[fname]
    sheets = read_xlsx(fpath)
    if not sheets:
        continue

    if fname in ASSEMBLY_FILES:
        items = parse_assembly(sheets)
    elif fname in INSTALLATION_FILES:
        items = parse_installation(sheets)
    else:
        items = parse_standard(sheets)

    for it in items:
        it.update({
            'ID': item_id, '공급사': meta['공급사'], '견적번호': meta['견적번호'],
            '견적일': meta['견적일'], '공사명': meta['공사명'],
            '품목분류': '설비', '출처파일': fname,
        })
        all_items.append(it)
        item_id += 1
    stats[fname] = len(items)
    print(f"  {len(items):4d}개  {fname}")

print(f"\n총 원본 레코드: {len(all_items)}개")

# 중복 제거 후 표준단가 산출
from collections import defaultdict
groups = defaultdict(list)
for it in all_items:
    key = f"{it['품명']}||{it['규격']}"
    groups[key].append(it)

db_records = []
std_id = 1
for key, group in groups.items():
    prices = [g['단가_원'] for g in group if g['단가_원'] > 0]
    if not prices:
        continue
    rep = group[0]
    db_records.append({
        '표준품목ID': f"STD-{std_id:04d}",
        '품목분류': rep['품목분류'],
        '품명': rep['품명'],
        '규격': rep['규격'],
        '단위': rep['단위'],
        '단가_최저': min(prices),
        '단가_평균': round(sum(prices) / len(prices)),
        '단가_최고': max(prices),
        '데이터건수': len(prices),
        '주요메이커': rep['메이커'],
        '최근견적일': max(g['견적일'] for g in group),
        '출처': ', '.join(set(g['공급사'] for g in group)),
    })
    std_id += 1

print(f"표준단가 DB: {len(db_records)}개 품목 ({len(all_items)}건 원본)")

out_dir = Path(__file__).parent
with open(out_dir / "표준단가DB_전체원본.json", 'w', encoding='utf-8') as f:
    json.dump(all_items, f, ensure_ascii=False, indent=2)
with open(out_dir / "표준단가DB_집계.json", 'w', encoding='utf-8') as f:
    json.dump(db_records, f, ensure_ascii=False, indent=2)
print("JSON 저장 완료")
