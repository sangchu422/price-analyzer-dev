# Price Analyzer 작업 기준

현재 제품은 React + FastAPI + SQLite로 구성된 로컬 웹 애플리케이션이다.

작업을 시작하면 먼저 `docs/HANDOFF_2026-07-24.md`의
`사내 Claude 첫 작업 체크리스트`를 읽고 현재 브랜치·DB·실행 상태를
확인한다. 실제 DB를 임의로 초기화하거나 archive 코드를 실행하지 않는다.

## 제품 흐름

1. 과거 견적 원본을 `HISTORICAL_REFERENCE`로 수집하고 정제한다.
2. 최신 `INCLUDED` 행만 품명·사양·단위 기준으로 그룹화해 내부 표준 DB를
   자동 구축한다.
3. 웹 `/analysis`에서 신규 견적을 업로드한다. 신규 문서는
   `INCOMING_BID`이며 표준 DB의 멤버나 가격 근거가 될 수 없다.
4. 표준 DB에 가격 근거가 있으면 신규 견적 단가를 비교한다. 매칭이나 가격
   근거가 없으면 검토 대기로 남긴다.

## 현재 실행 경로

- Backend: `backend/app`
- Frontend: `frontend/src`
- Alembic: `backend/alembic`
- 단일 로컬 DB:
  `backend/.local/standard-item-migration-v2.sqlite3`
- 실행기: `scripts\start-local.bat` 또는 `앱실행.bat`

사내 정책상 PowerShell과 `.ps1`은 사용하지 않는다. 모든 Windows 명령은
`cmd.exe`에서 실행한다.

루트의 과거 Streamlit·Excel 프로토타입은
`archive/legacy-excel-prototype/`에 역사 자료로만 보관한다. 그 코드를
실행하거나 Excel 산출물을 현재 제품의 입력·검증·비교에 사용하지 않는다.

## 개발 명령

```bat
set "DATABASE_FILE=%CD%\backend\.local\standard-item-migration-v2.sqlite3"

cd backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m pytest -q
cd ..

cd frontend
call npm.cmd test -- --run
call npm.cmd run lint
call npm.cmd run build
cd ..
```

서버 설치, hChat 실연결, DeviceMart·Mouser 시장가 수집은 후속 단계다.
