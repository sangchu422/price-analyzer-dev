# TODO.md — 작업 로드맵

> 참조: DESIGN.md, PRD_견적가비교분석시스템_v2.md

---

## Phase 1 — 완료 (파싱·DB 기반)

- [x] PostgreSQL 스키마 설계 (`schema.sql`)
- [x] 파일 스캐너·해시 중복 제거 (`scanner.py`)
- [x] Excel 레이아웃 자동 감지 (`detector.py`)
- [x] 표준형 파서 (`parsers/standard.py`)
- [x] 조립기형 파서 (`parsers/assembly.py`)
- [x] DB 저장·파싱 로그 (`loader.py`)
- [x] 표준단가 마스터 동기화 (`sync_master.py`)
- [x] 이상가 탐지 뷰 (±15% 임계)
- [x] 파이프라인 엔트리포인트 (`run_pipeline.py`)

---

## Phase 2 — 진행 예정 (AI 검색 + MVP UI)

### 2-A. AI 유사도 검색 엔진 (F-205)

- [ ] sentence-transformers 한국어 모델 선정 (예: `jhgan/ko-sbert-sts`)
- [ ] 품목명·규격 임베딩 생성 파이프라인
- [ ] FAISS 인덱스 구축·저장
- [ ] 유사 품목 Top-N 검색 API
- [ ] `scorer.py` 연동 (신뢰도 점수 반영)

### 2-B. FastAPI 백엔드

- [ ] `/upload` — 견적서 파일 업로드·파싱 트리거
- [ ] `/items/{header_id}` — 파싱 결과 조회
- [ ] `/match/{item_id}` — AI 유사도 매핑 결과
- [ ] `/anomalies` — 이상가 품목 목록
- [ ] `/report/{header_id}` — Excel 리포트 생성

### 2-C. Streamlit MVP 대시보드

- [ ] 파일 업로드 UI (드래그앤드롭)
- [ ] 파싱 결과 테이블 (편집 가능)
- [ ] 표준단가 비교 뷰 (견적가 vs 표준단가 vs 차이율)
- [ ] 이상가 하이라이트 (빨간 강조)
- [ ] Excel 리포트 다운로드 버튼

### 2-D. PDF 파서 개선

- [ ] `parse_pdf.py` — 표로 구성된 PDF 대응 강화
- [ ] pdfplumber 테이블 추출 정확도 검증 (테스트 PDF 사용)

---

## Phase 3 — 고도화

### 3-A. 할인 룰 엔진 (F-311)

- [ ] 품목 분류별 할인율 룰 정의 (물품/설비/공사/용역)
- [ ] 신규 품목 추정가 산출 로직
- [ ] 룰 추가·수정 UI (관리자용)

### 3-B. 외부 시장가 조회 (F-312)

- [ ] 나라장터 Open API 연동
- [ ] 시장가 캐싱 (매일 갱신)
- [ ] 시장가 기반 적정가 추정

### 3-C. 리포트·대시보드 고도화

- [ ] 단가 추이 그래프 (품목별 월별) (F-404)
- [ ] 할인 시뮬레이터 (F-405)
- [ ] 다중 협력사 비교 (F-207, F-209)
- [ ] 절감 가능 금액 산출 (F-325)

### 3-D. React 정식 UI

- [ ] 디자인 시스템 확정
- [ ] FastAPI 연동
- [ ] 검토·승인 워크플로우 UI
- [ ] 승인 단가 → DB 자동 반영 (F-106)

---

## 즉시 할 수 있는 작업 (백로그)

| 우선순위 | 작업 | 예상 소요 |
|---------|------|-----------|
| P0 | `.env` 파일 설정·DB 연결 검증 | 30분 |
| P0 | `--dry-run`으로 테스트 견적서 파싱 검증 | 1시간 |
| P0 | `unknown_layout` 견적서 수동 분류 | 반나절 |
| P1 | scorer.py 로직 구현 (현재 빈 파일?) | 2시간 |
| P1 | PDF 파서 테스트 (2차 학습 PDF 2개) | 2시간 |
| P2 | sentence-transformers 모델 PoC | 반나절 |
| P2 | Streamlit 프로토타입 (파싱 결과 조회만) | 1일 |

---

*TODO.md — 최종 수정: 2026-07-23*
