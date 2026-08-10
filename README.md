# Price Analyzer

과거 입찰 견적을 근거로 내부 표준 DB를 자동 구축하고, 웹에서 새 견적서를
업로드해 품목별 가격 적정성을 확인하는 로컬 애플리케이션이다.

## 빠른 실행

저장소 루트에서 다음 중 하나를 실행한다.

```bat
scripts\start-local.bat
```

또는 `앱실행.bat`를 실행한다. 백엔드는 `127.0.0.1:8000`, 프런트엔드는
`127.0.0.1:4173`에서 시작된다. 실행기는 다른 프로세스가 포트를 사용 중이면
그 프로세스를 종료하지 않고 오류로 중단한다.

단일 운영 로컬 DB 경로는 다음 하나뿐이다.

```text
backend\.local\standard-item-migration-v2.sqlite3
```

DB가 없으면 실행기가 Alembic으로 빈 스키마를 만든다. 빈 DB에는 과거 견적과
표준가격이 없으므로 첫 실행 시 추적 중인 `견적서` 원본을 적재하고 표준 DB를
자동 구축한다. 데이터 구축만 먼저 실행하려면 다음 명령을 사용한다.

```bat
scripts\start-local.bat --initialize-only
```

새 PC에서 처음 pull한 경우에는 Python 3.12 가상환경과 프런트엔드 의존성을
먼저 설치해야 한다. DB 파일은 Git에 포함되지 않으며, 아래 초기화 명령이
추적 중인 1·2·3차 견적 원본으로 로컬 DB를 다시 만든다.

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e backend
call npm.cmd --prefix frontend install
scripts\start-local.bat --initialize-only
scripts\start-local.bat
```

상세 절차와 예상 건수는 `docs/HANDOFF_2026-07-24.md`의
`다른 PC에서 pull 후 최초 DB 구축`을 따른다.

## 과거 견적 적재와 표준 DB 구축

아래 명령은 자동 초기화 대신 각 단계를 직접 확인해야 할 때만 사용한다.

```bat
set "REPO_ROOT=%CD%"
set "DATABASE_FILE=%REPO_ROOT%\backend\.local\standard-item-migration-v2.sqlite3"

cd backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m app.cli ingest --quote-root "%REPO_ROOT%\견적서" --database-file "%DATABASE_FILE%"
..\.venv\Scripts\python.exe -m app.cli standard-db-build --database-file "%DATABASE_FILE%"
cd ..
```

`ingest`는 구성된 과거 견적 코퍼스를 `HISTORICAL_REFERENCE`로 등록한다.
표준 DB 구축은 최신 `INCLUDED` 행만 사용한다. `/analysis`로 접수한 신규
견적은 `INCOMING_BID`이므로 어떤 쓰기 경로에서도 표준 DB의 멤버나 가격
근거로 추가할 수 없다.

## 직접 개발 실행

```bat
set "REPO_ROOT=%CD%"
set "DATABASE_FILE=%REPO_ROOT%\backend\.local\standard-item-migration-v2.sqlite3"
set "SUBMISSION_FOLDER=%REPO_ROOT%\backend\.local\submissions"

cd backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

별도 터미널:

```bat
cd frontend
call npm.cmd run dev -- --host 127.0.0.1 --port 4173 --strictPort
```

## 레거시 자료

초기 Streamlit·Excel·JSON 프로토타입은
`archive/legacy-excel-prototype/`에 보관만 한다. 그 안의 Excel은 현재
제품의 입력, 결과 검증, 비교, 내보내기 자료가 아니다.
