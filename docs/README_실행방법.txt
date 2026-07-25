===========================================================
  협력사 견적 단가 AI 비교분석 시스템 — 현대위아 구매본부
===========================================================

※ 이 문서의 첫 부분은 신규 로컬 A 구조 실행법입니다.
※ 아래 "기존 Streamlit 앱"은 마이그레이션 기간 참고용입니다.

■ 신규 로컬 A 구조 — 견적서 사전 점검/적재
─────────────────────────────
1. 백엔드 개발 환경 설치
   > cd backend
   > python -m venv .venv
   > .venv\Scripts\python -m pip install -e ".[dev]"

2. 파일 내용 읽기 전 사전 점검
   > .venv\Scripts\python -m app.cli preflight --quote-root ..\견적서

3. 로컬 SQLite 적재
   > .venv\Scripts\python -m app.cli ingest --quote-root ..\견적서 --database-file .local\price-analyzer.sqlite3 --report .local\corpus-run.json

4. 완전 일치 표준품목 초안 이관
   > .venv\Scripts\python -m app.cli catalog-seed --database-file .local\price-analyzer.sqlite3

   현재 INCLUDED 중 정규화된 품명·사양·단위가 완전히 같고 2건 이상인
   그룹만 자동 생성합니다. 단위 충돌, 퍼지 후보, 의미 유사 후보는 자동
   확정하지 않고 설비구매팀 검토 대상으로 남깁니다. 재실행해도 같은
   멤버십을 중복 생성하지 않습니다.

5. 표준단가 계산 초안 확인(승인하지 않음)
   > .venv\Scripts\python -m app.cli standard-price-drafts --database-file .local\price-analyzer.sqlite3

   이 명령은 읽기 전용이며 StandardPriceVersion을 만들지 않습니다.
   표준단가 버전은 웹 화면/API에서 사람 이름과 최신 fingerprint를
   확인해 명시적으로 승인해야 생성됩니다.

6. 개발용 mock 임베딩 인덱스(선택)
   > .venv\Scripts\python -m app.cli embedding-index --database-file .local\price-analyzer.sqlite3 --index-file .local\standard-items-mock.npz --mock

   local-mock-v1은 연결 시험용이며 실제 의미 임베딩 모델이 아닙니다.
   --mock 없이 실행하면 로컬 기본 설정은 DISABLED로 종료하고 네트워크를
   호출하거나 인덱스를 만들지 않습니다. 사내 hChat 샘플을 받은 뒤
   backend/app/embeddings/hchat.py의 _build_payload와 _parse_response만
   맞추고, 먼저 mock transport 계약 테스트를 실행하십시오.

   위 세 명령의 보고서는 backend/.local/reports 아래에 UTC 시각과
   run-id를 붙여 매번 새 파일로 보존되며, 출력의 report_file에서
   경로를 확인할 수 있습니다. --report를 명시하면 해당 고정 파일을
   교체합니다. DB·보고서·인덱스는 backend/.local 내부만 허용하고,
   원본 견적서·hard link·symlink·junction·디렉터리는 거부합니다.

7. 로컬 API/화면 실행
   > .venv\Scripts\python -m uvicorn app.main:app --reload

   별도 터미널:
   > cd frontend
   > npm install
   > npm run dev

사전 점검은 견적서 내용을 열지 않습니다. 적재 시 보호 원본/보안해제본
쌍은 둘 다 증빙 등록하되 보안해제본만 파싱합니다. 보안해제 짝이 없는
읽기 가능한 일반 원본은 지원 양식일 때 파싱합니다. .local 아래 DB와 실행
보고서는 Git에 포함하지 않습니다. 정제 검토 페이지와 판정대기 목록은
설비구매팀 검토용으로 계속 유지합니다. 현재는 로컬 전용이며 서버 설치,
hChat 사내망 연결, DeviceMart/Mouser 캐시 우선 시장가 DB와 원본 증빙
저장은 후속 단계입니다.

===========================================================
  기존 Streamlit 앱 (마이그레이션 기간 참고용)
===========================================================

■ 최초 설치 (최초 1회만)
─────────────────────────────
1. Python 3.10 이상 설치 확인
   > python --version

2. 패키지 설치
   > pip install -r requirements.txt

3. Playwright 브라우저 설치 (KPI 시장가 연동 필수)
   > playwright install chromium

─────────────────────────────
■ 앱 실행
─────────────────────────────
방법 1 (권장): 폴더 내 "앱실행.bat" 더블클릭

방법 2 (터미널):
   > cd "c:\...\260707 러닝랩 신규과제"
   > python -m streamlit run app.py --server.port 8501

실행 후 브라우저에서: http://localhost:8501

─────────────────────────────
■ KPI 계정 변경
─────────────────────────────
kpi_config.py 파일을 메모장으로 열어 ID/PW 수정
(현재 설정: ID=wia, PW=xhdrn)

─────────────────────────────
■ 주요 파일 설명
─────────────────────────────
app.py                  — 메인 앱 (Streamlit)
kpi_scraper.py          — 한국물가정보 연동 모듈
kpi_worker.py           — KPI 크롤러 격리 실행기
kpi_config.py           — KPI 계정 설정 (ID/PW)
build_db.py             — 표준단가DB Excel 재생성기
parse_all.py            — 견적서 파싱기 (11개 파일)
앱실행.bat              — 원클릭 실행기

표준단가DB.xlsx         — 표준단가 데이터베이스 (1,131건)
표준단가DB_집계.json    — 표준단가 DB 원본 (JSON)
표준단가DB_전체원본.json — 견적서 원본 데이터 전체 (JSON)
reviewed_db.json        — 검토 이력 저장소 (자동 생성/누적)

견적서/                 — 견적서 원본 파일들 (11개)
data/                   — 참고 자료
_이전작업물/            — 개발 과정 중간 산출물 (참고용)

─────────────────────────────
■ 표준단가DB 재구축 방법
─────────────────────────────
새 견적서를 "견적서/" 폴더에 추가한 후:
   > python parse_all.py     ← 파싱 및 JSON 재생성
   > python build_db.py      ← Excel DB 재생성

또는 앱 내 "🔄 캐시 초기화" 버튼 클릭 (build_db만 재실행)

─────────────────────────────
■ 문의
─────────────────────────────
현대위아 구매본부 (개발 담당: 러닝랩 신규과제팀)
===========================================================
