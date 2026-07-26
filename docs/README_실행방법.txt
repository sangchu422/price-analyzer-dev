협력사 견적 단가 비교분석 시스템 — 로컬 실행
================================================

현재 운영 대상은 React + FastAPI + SQLite로 구성된 로컬 A 구조다.
루트의 기존 Streamlit 앱과 Excel 생성 스크립트는 초기 시제품 참고자료이며
현재 표준 DB 구축이나 신규 견적 분석에 사용하지 않는다.

1. 백엔드 설치
   > cd backend
   > ..\.venv\Scripts\python -m pip install -e ".[dev]"

2. 과거 견적서 사전 점검과 적재
   > ..\.venv\Scripts\python -m app.cli preflight --quote-root ..\견적서
   > ..\.venv\Scripts\python -m app.cli ingest --quote-root ..\견적서 --database-file .local\price-analyzer.sqlite3

3. 표준 DB 자동 구축
   > ..\.venv\Scripts\python -m app.cli standard-db-build --database-file .local\price-analyzer.sqlite3

   최신 HISTORICAL_REFERENCE 문서의 최신 INCLUDED 원본 행만 사용한다.
   정규화된 품명·사양·단위 완전 일치 그룹을 만들며 근거 1건도 포함한다.
   동일 입력 재실행은 기존 성공 실행을 재사용한다.

4. 백엔드 실행
   > set DATABASE_FILE=backend/.local/price-analyzer.sqlite3
   > ..\.venv\Scripts\python -m uvicorn app.main:app --reload

5. 프런트엔드 실행(별도 터미널)
   > cd frontend
   > npm install
   > npm run dev

브라우저에서 `/standard-prices`는 자동 구축된 내부 표준 DB를 읽기 전용으로
보여준다. `/analysis`에서 신규 견적서를 업로드하면 `INCOMING_BID`로 분리
적재하고, 현재 표준 DB와 완전 일치하는 품목의 가격 적정성을 분석한다.
분석은 표준 멤버십이나 가격을 생성하지 않는다. 판정대기와 유사 후보는
설비구매팀 검토 대상으로 남는다.

기존 표준단가DB.xlsx는 초창기 수작업 파일이므로 입력, 검증, 비교, 내보내기,
증빙으로 사용하지 않는다. Excel 내보내기 기능도 제공하지 않는다.

현재는 로컬 전용이다. 사내 서버 설치, hChat 임베딩 실연결,
DeviceMart·Mouser 시장가 DB와 누락 품목 실시간 조회는 후속 단계다.
