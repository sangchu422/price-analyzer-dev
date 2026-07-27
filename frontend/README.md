# Price Analyzer 프런트엔드

과거 견적 정제 결과를 검토하고 `INCLUDED` 또는 `EXCLUDED` 수동 판단을
추가하는 로컬 전용 화면입니다. 운영용 가짜 데이터나 hChat 호출은 포함하지
않습니다.

## 로컬 실행

백엔드를 먼저 `127.0.0.1:8000`에서 실행한 뒤:

```bat
cd frontend
call npm.cmd install
call npm.cmd run dev
```

Vite 개발 서버는 `/api` 요청을 기본적으로
`http://127.0.0.1:8000`에 전달합니다. 백엔드 주소가 다르면 실행 전에
`VITE_API_PROXY_TARGET`을 지정합니다.

```bat
set "VITE_API_PROXY_TARGET=http://127.0.0.1:9000"
call npm.cmd run dev
```

## 검증

```bat
call npm.cmd test -- --run
call npm.cmd run build
call npm.cmd run lint
```
