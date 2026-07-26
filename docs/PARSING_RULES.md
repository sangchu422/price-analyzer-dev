# 견적서 파싱·정제 규칙

현재 운영 기준은 `backend/app`의 로컬 FastAPI 파이프라인이다. 루트의
`parse_all.py`, `apply_rules.py`, `build_db.py`와 기존 Excel/JSON 산출물은
초기 시제품 참고자료이며 현재 표준 DB 구축에 사용하지 않는다.

## 처리 원칙

1. `preflight`에서 논리 문서, 지원 확장자, 보안해제 우선본과 경로 안전성을
   확인한다.
2. `ingest`는 원본 파일을 수정하지 않고 파일 SHA-256, 논리 문서, 물리 변형,
   시트·행·셀 또는 PDF 페이지와 파서 버전을 SQLite에 저장한다.
3. 보호 원본과 `_보안해제` 복사본은 하나의 논리 문서로 취급한다. 두 파일의
   증빙은 보존하되 읽을 수 있는 보안해제본만 파싱하여 중복 적재하지 않는다.
4. 품명 결측, 비정상 가격, 요약·인건비·경비 행, 열 밀림, 금액 불일치와
   이상치는 원본 행을 삭제하지 않고 append-only 정제 결정으로 기록한다.
5. 자동 표준 DB는 최신 `HISTORICAL_REFERENCE` 문서의 최신 `INCLUDED` 행만
   사용한다. 최신 역할이 `INCOMING_BID`인 문서는 절대 포함하지 않는다.
6. 정규화된 `품명 + 사양 + 단위` 완전 일치만 자동 그룹으로 확정한다.
   단위 충돌, 빈 품명, 퍼지·의미 유사 후보와 `REVIEW_REQUIRED`는 자동
   확정하지 않는다.
7. 근거 1건 그룹도 표준가격으로 사용하되 `근거 1건` 경고와 원본 증빙을
   표시한다.

## 실행

```powershell
cd backend
..\.venv\Scripts\python -m app.cli preflight --quote-root ..\견적서
..\.venv\Scripts\python -m app.cli ingest `
  --quote-root ..\견적서 `
  --database-file .local\price-analyzer.sqlite3
..\.venv\Scripts\python -m app.cli standard-db-build `
  --database-file .local\price-analyzer.sqlite3
```

`standard-db-build`는 동일 fingerprint와 규칙 버전의 성공 실행을 재사용한다.
결과는 SQLite와 `backend/.local/reports/`의 JSON 보고서로 확인한다. Excel
내보내기는 운영 흐름이 아니며, 기존 `표준단가DB.xlsx`는 입력·검증·비교·
증빙으로 사용하지 않는다.
