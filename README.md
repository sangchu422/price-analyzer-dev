# Price Analyzer

과거 입찰 견적을 근거로 내부 표준 DB를 자동 구축하고, 웹에서 새 견적서를
업로드해 품목별 가격 적정성을 확인하는 로컬 애플리케이션이다.

## 빠른 실행

저장소 루트에서 다음 중 하나를 실행한다.

```powershell
.\scripts\start-local.ps1
```

또는 `앱실행.bat`를 실행한다. 백엔드는 `127.0.0.1:8000`, 프런트엔드는
`127.0.0.1:4173`에서 시작된다. 실행기는 다른 프로세스가 포트를 사용 중이면
그 프로세스를 종료하지 않고 오류로 중단한다.

단일 운영 로컬 DB 경로는 다음 하나뿐이다.

```text
backend\.local\standard-item-migration-v2.sqlite3
```

DB가 없으면 실행기가 Alembic으로 빈 스키마를 만든다. 빈 DB에는 과거 견적과
표준가격이 없으므로 아래 초기 적재·구축을 한 번 수행해야 한다.

## 과거 견적 적재와 표준 DB 구축

```powershell
$repo = (Resolve-Path '.').Path
$db = Join-Path $repo 'backend\.local\standard-item-migration-v2.sqlite3'
$quotes = Join-Path $repo '견적서'
$env:DATABASE_FILE = $db

Push-Location backend
try {
  ..\.venv\Scripts\python -m alembic upgrade head
  ..\.venv\Scripts\python -m app.cli ingest `
    --quote-root $quotes `
    --database-file $db
  ..\.venv\Scripts\python -m app.cli standard-db-build `
    --database-file $db
} finally {
  Pop-Location
}
```

`ingest`는 구성된 과거 견적 코퍼스를 `HISTORICAL_REFERENCE`로 등록한다.
표준 DB 구축은 최신 `INCLUDED` 행만 사용한다. `/analysis`로 접수한 신규
견적은 `INCOMING_BID`이므로 어떤 쓰기 경로에서도 표준 DB의 멤버나 가격
근거로 추가할 수 없다.

## 직접 개발 실행

```powershell
$repo = (Resolve-Path '.').Path
$db = Join-Path $repo 'backend\.local\standard-item-migration-v2.sqlite3'
$env:DATABASE_FILE = $db
$env:SUBMISSION_FOLDER = Join-Path $repo 'backend\.local\submissions'

Push-Location backend
try {
  ..\.venv\Scripts\python -m alembic upgrade head
  ..\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
} finally {
  Pop-Location
}
```

별도 터미널:

```powershell
Push-Location frontend
try {
  npm run dev -- --host 127.0.0.1 --port 4173 --strictPort
} finally {
  Pop-Location
}
```

## 레거시 자료

초기 Streamlit·Excel·JSON 프로토타입은
`archive/legacy-excel-prototype/`에 보관만 한다. 그 안의 Excel은 현재
제품의 입력, 결과 검증, 비교, 내보내기 자료가 아니다.
