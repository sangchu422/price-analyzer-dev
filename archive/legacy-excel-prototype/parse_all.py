import json, openpyxl
from pathlib import Path
import parse_pdf
import apply_rules

folder1 = Path(__file__).parent / "견적서" / "1차 학습"
folder2 = Path(__file__).parent / "견적서" / "2차 학습"

file_meta = {
    # ── 1차 학습 ──────────────────────────────────────────────────────────────
    "5-1. 견적서_HVAC.xlsx":       {"공급사": "FTC", "공사명": "HVAC Assy Line SX3i", "견적번호": "FTC-2530", "견적일": "2025-09-29", "데이터입력일자": "2026-07-08"},
    "5-2. 견적서_쿨링모듈.xlsx":    {"공급사": "FTC", "공사명": "C Module Assy Table Line SX3i", "견적번호": "FTC-2531", "견적일": "2025-09-29", "데이터입력일자": "2026-07-08"},
    "5. 견적서(남경테크윈).xlsx":   {"공급사": "남경테크윈", "공사명": "전동기 설비 라인", "견적번호": "NKT25226-0902-C-08", "견적일": "2025-09-05", "데이터입력일자": "2026-07-08"},
    "5. 견적서(화인) (1).xlsx":     {"공급사": "주식회사 화인", "공사명": "TMED-II 자동화라인(턴키)", "견적번호": "ES25-094M-C", "견적일": "2025-09-10", "데이터입력일자": "2026-07-08"},
    "5. 견적서(화인).xlsx":         {"공급사": "주식회사 화인", "공사명": "TMED-II 함침라인(턴키)", "견적번호": "ES25-094M-L", "견적일": "2025-09-10", "데이터입력일자": "2026-07-08"},
    "새 Microsoft Excel 워크시트 - 복사본 (2).xlsx": {"공급사": "자동차부품주식회사", "공사명": "메인조립기", "견적번호": "미상", "견적일": "2025-01-01", "데이터입력일자": "2026-07-08"},
    "새 Microsoft Excel 워크시트 - 복사본 (3).xlsx": {"공급사": "자동차부품주식회사", "공사명": "헤드서브조립기", "견적번호": "미상", "견적일": "2025-01-01", "데이터입력일자": "2026-07-08"},
    "새 Microsoft Excel 워크시트 - 복사본 (4).xlsx": {"공급사": "부남테크", "공사명": "KMX SVM 차종설치 시운전", "견적번호": "DM250313-001", "견적일": "2026-07-07", "데이터입력일자": "2026-07-08"},
    "새 Microsoft Excel 워크시트 - 복사본 (5).xlsx": {"공급사": "주식회사 아성", "공사명": "호원 BJ1차종 로봇설비 티칭", "견적번호": "DM250429-001", "견적일": "2026-07-07", "데이터입력일자": "2026-07-08"},
    "새 Microsoft Excel 워크시트 - 복사본.xlsx":     {"공급사": "협력사미상", "공사명": "CORE BUILDER 4종", "견적번호": "STEM2508-016/17/18/19", "견적일": "2025-09-22", "데이터입력일자": "2026-07-08"},
    "새 Microsoft Excel 워크시트.xlsx":              {"공급사": "영산테크노", "공사명": "친수/바이오 코팅 라인", "견적번호": "YS-25-P-124", "견적일": "2025-10-20", "데이터입력일자": "2026-07-08"},
    # PDF 견적서
    "4510307188 2022-10-28.pdf":  {"공급사": "주식회사 지이엠", "공사명": "현대위아 자동화라인", "견적번호": "GD1_220824-001", "견적일": "2022-08-24", "데이터입력일자": "2026-07-20"},
    # ── 2차 학습 (현대/기아차 설비협력업체 견적통일양식) ──────────────────────
    "2020년 견적서 7-1. 최종견적서_메인조립라인_보안해제.xlsx":       {"공급사": "주식회사 지이엠", "공사명": "메인조립라인", "견적번호": "GD1_191118-003", "견적일": "2019-11-18", "데이터입력일자": "2026-07-20"},
    "2020년 견적서 7-2. 최종견적서_OB 서브조립라인_보안해제.xlsx":    {"공급사": "주식회사 지이엠", "공사명": "OB 서브조립라인", "견적번호": "GD1_191118-001", "견적일": "2019-11-18", "데이터입력일자": "2026-07-20"},
    "2020년 견적서 7-3. 최종견적서_IB 서브조립라인_보안해제.xlsx":    {"공급사": "주식회사 지이엠", "공사명": "IB 서브조립라인", "견적번호": "GD1_191118-002", "견적일": "2019-11-18", "데이터입력일자": "2026-07-20"},
    "WIA-25-30 인도 푸네공장 CVJ 가공라인 자동화(260510).xlsx":        {"공급사": "주식회사 에스피시스템스", "공사명": "인도 푸네공장 CVJ 가공라인 자동화", "견적번호": "WIA-25-30", "견적일": "2026-05-10", "데이터입력일자": "2026-07-20"},
    "6. 견적서 CM862202505190005 등속조인트 고성능 효율시험기.xlsx":    {"공급사": "코리아고꾸사이", "공사명": "등속조인트 고성능 효율시험기", "견적번호": "CM862202505190005", "견적일": "2025-05-28", "데이터입력일자": "2026-07-20"},
    "7. 견적서 CM862202605150049 창원2공장 승용액슬 기어가공라인 12만 증량 대응 자동화 신규&개조(선삭,연삭,자동창고) 件.xlsx": {"공급사": "주식회사 디엠테크놀러지", "공사명": "창원2공장 승용액슬 기어가공라인 자동화 신규&개조", "견적번호": "DM260526-1", "견적일": "2026-05-26", "데이터입력일자": "2026-07-20"},
    "6. 견적서 CM862202605140120 창원1공장  LT.LW 냉각수 모듈 조립 3라인 기종추가.xlsx": {"공급사": "주식회사 지이엠", "공사명": "창원1공장 LT.LW 냉각수 모듈 조립 3라인 기종추가", "견적번호": "GD1_260521-001", "견적일": "2026-05-21", "데이터입력일자": "2026-07-20"},
    "7. 견적서 CM862202512080040 창원1공장 특수사업 포열 크롬도금 신작 件.xlsx": {"공급사": "협력사미상(WST)", "공사명": "포열 내경 크롬도금설비", "견적번호": "WST251219-001", "견적일": "2025-12-19", "데이터입력일자": "2026-07-20"},
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


# ── 현대/기아차 설비협력업체 견적통일양식 파서 ────────────────────────────────
# 데이터 시트 탐지: row0 col1에 '단위공사명' 또는 '전기부문' 포함
# 컬럼 레이아웃: col2=품명, col3=규격, col4=단위, col5=수량, col6=단가, col7=금액, col8=메이커
_WIA_MARKER_KW = ('단위공사명', '전기부문')

def parse_wia_format(sheets):
    items = []
    for sh_name, rows in sheets.items():
        if not rows:
            continue
        row0 = rows[0]
        marker = row0[1] if len(row0) > 1 else ''
        if not any(kw in marker for kw in _WIA_MARKER_KW):
            continue

        unit_name = (row0[2].strip() if len(row0) > 2 else '') or sh_name

        for row in rows[2:]:
            if len(row) < 7:
                continue
            품명 = row[2].lstrip('■□●○').strip()
            규격 = row[3].strip() if len(row) > 3 else ''
            단위 = row[4].strip() if len(row) > 4 else ''
            수량_raw = row[5].strip() if len(row) > 5 else ''
            단가_raw = row[6].strip() if len(row) > 6 else ''
            금액_raw = row[7].strip() if len(row) > 7 else ''
            메이커 = row[8].strip() if len(row) > 8 else ''

            if not 품명 or len(품명) < 2:
                continue
            if 품명[0] == '0' or 품명.replace(',', '').replace('.', '').isdigit():
                continue
            if any(k in 품명 for k in SKIP_KW):
                continue

            try:
                단가 = int(float(단가_raw.replace(',', '')))
            except (ValueError, AttributeError):
                continue
            try:
                금액 = int(float(금액_raw.replace(',', '')))
            except (ValueError, AttributeError):
                금액 = 0

            if 단가 <= 0 or 금액 < 1000:
                continue

            try:
                수량 = float(수량_raw.replace(',', '')) if 수량_raw else None
            except (ValueError, AttributeError):
                수량 = None

            items.append({
                '단위장비명': unit_name, '품명': 품명, '규격': 규격,
                '단위': 단위, '수량': 수량, '단가_원': 단가,
                '금액_원': 금액, '메이커': 메이커,
            })
    return items


ASSEMBLY_FILES = {
    "새 Microsoft Excel 워크시트 - 복사본 (2).xlsx",
    "새 Microsoft Excel 워크시트 - 복사본 (3).xlsx",
}
INSTALLATION_FILES = {
    "새 Microsoft Excel 워크시트 - 복사본 (4).xlsx",
    "새 Microsoft Excel 워크시트 - 복사본 (5).xlsx",
}
WIA_FORMAT_FILES = {
    "2020년 견적서 7-1. 최종견적서_메인조립라인_보안해제.xlsx",
    "2020년 견적서 7-2. 최종견적서_OB 서브조립라인_보안해제.xlsx",
    "2020년 견적서 7-3. 최종견적서_IB 서브조립라인_보안해제.xlsx",
    "WIA-25-30 인도 푸네공장 CVJ 가공라인 자동화(260510).xlsx",
    "6. 견적서 CM862202505190005 등속조인트 고성능 효율시험기.xlsx",
    "7. 견적서 CM862202605150049 창원2공장 승용액슬 기어가공라인 12만 증량 대응 자동화 신규&개조(선삭,연삭,자동창고) 件.xlsx",
    "6. 견적서 CM862202605140120 창원1공장  LT.LW 냉각수 모듈 조립 3라인 기종추가.xlsx",
    "7. 견적서 CM862202512080040 창원1공장 특수사업 포열 크롬도금 신작 件.xlsx",
}

all_items = []
item_id = 1
stats = {}

all_files = sorted(
    list(folder1.glob("*.xlsx")) + list(folder1.glob("*.pdf")) +
    list(folder2.glob("**/*.xlsx")) + list(folder2.glob("**/*.pdf"))
)

for fpath in all_files:
    fname = fpath.name
    if fname not in file_meta:
        continue
    meta = file_meta[fname]

    # PDF 처리
    if fpath.suffix.lower() == '.pdf':
        items = parse_pdf.extract_items(str(fpath), meta)
        for it in items:
            it.update({
                'ID': item_id, '공급사': meta['공급사'], '견적번호': meta['견적번호'],
                '견적일': meta['견적일'], '공사명': meta['공사명'],
                '품목분류': '설비', '출처파일': fname,
                '데이터입력일자': meta.get('데이터입력일자', ''),
            })
            all_items.append(it)
            item_id += 1
        stats[fname] = len(items)
        print(f"  {len(items):4d}개  {fname} [PDF]")
        continue

    # XLSX 처리
    sheets = read_xlsx(fpath)
    if not sheets:
        continue

    if fname in ASSEMBLY_FILES:
        items = parse_assembly(sheets)
    elif fname in INSTALLATION_FILES:
        items = parse_installation(sheets)
    elif fname in WIA_FORMAT_FILES:
        items = parse_wia_format(sheets)
    else:
        items = parse_standard(sheets)

    for it in items:
        it.update({
            'ID': item_id, '공급사': meta['공급사'], '견적번호': meta['견적번호'],
            '견적일': meta['견적일'], '공사명': meta['공사명'],
            '품목분류': '설비', '출처파일': fname,
            '데이터입력일자': meta.get('데이터입력일자', ''),
        })
        all_items.append(it)
        item_id += 1
    stats[fname] = len(items)
    print(f"  {len(items):4d}개  {fname}")

print(f"\n총 원본 레코드: {len(all_items)}개")

# 파싱 규칙 적용 (견적서_파싱규칙.md 참고)
all_items = apply_rules.apply_all(all_items)

# 기존 JSON에서 데이터입력일자 보존용 맵 로드
import datetime
_existing_dates = {}
_existing_json = Path(__file__).parent / "표준단가DB_집계.json"
if _existing_json.exists():
    with open(_existing_json, encoding='utf-8') as _f:
        for _rec in json.load(_f):
            _key = f"{_rec['품명']}||{_rec['규격']}||{_rec.get('단위','')}"
            _existing_dates[_key] = _rec.get('데이터입력일자', '')
_today = datetime.date.today().strftime('%Y-%m-%d')

# 중복 제거 후 표준단가 산출
from collections import defaultdict
groups = defaultdict(list)
for it in all_items:
    key = f"{it['품명']}||{it['규격']}||{it['단위']}"
    groups[key].append(it)

db_records = []
std_id = 1
for key, group in groups.items():
    prices = [g['단가_원'] for g in group if g['단가_원'] > 0]
    if not prices:
        continue
    rep = group[0]
    입력일자 = _existing_dates.get(key) or _today
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
        '데이터입력일자': 입력일자,
    })
    std_id += 1

print(f"표준단가 DB: {len(db_records)}개 품목 ({len(all_items)}건 원본)")

out_dir = Path(__file__).parent
with open(out_dir / "표준단가DB_전체원본.json", 'w', encoding='utf-8') as f:
    json.dump(all_items, f, ensure_ascii=False, indent=2)
with open(out_dir / "표준단가DB_집계.json", 'w', encoding='utf-8') as f:
    json.dump(db_records, f, ensure_ascii=False, indent=2)
print("JSON 저장 완료")
