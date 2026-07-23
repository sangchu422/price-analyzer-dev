# DESIGN.md — 협력사 견적 단가 AI 분석 시스템

> 참조: PRD_견적가비교분석시스템_v2.md, BRD_견적가비교분석시스템_v2.md, 엠로 기획안 (이미지 1~3)

---

## 1. 시스템 철학

**"견적서를 올리면 AI가 표준단가·할인 룰·외부 시장가를 종합해 적정 가격을 자동 제안하고, 검토할수록 DB가 똑똑해지는 구매 인텔리전스"**

핵심 가치:
- **자동화**: 수작업 Excel 대조 → 자동 파싱·비교
- **학습**: 검토·승인 데이터 누적 → 표준단가 DB 정확도 향상
- **투명성**: 추정 근거·출처 명시, 사람이 최종 판단

---

## 2. 전체 아키텍처

```
┌──────────────────────────────────────────────────────────────┐
│                        INPUT LAYER                           │
│   협력사 견적서 (xlsx / xls / pdf)                            │
└─────────────────────────┬────────────────────────────────────┘
                          ▼
┌──────────────────────────────────────────────────────────────┐
│                     PARSING LAYER                            │
│                                                              │
│  scanner.py → reader.py → detector.py                       │
│       ↓              ↓          ↓                            │
│  파일 스캔       Excel 읽기   레이아웃 분류                    │
│  해시 중복제거                (standard / assembly / unknown) │
│                         ↓                                    │
│              parsers/standard.py                             │
│              parsers/assembly.py                             │
│              parse_pdf.py (PDF 전용)                         │
└─────────────────────────┬────────────────────────────────────┘
                          ▼
┌──────────────────────────────────────────────────────────────┐
│                     STORAGE LAYER (PostgreSQL)               │
│                                                              │
│  quote_header       quote_item          parse_log            │
│  (파일 메타)        (라인 아이템)        (처리 이력)           │
│                          ↓                                    │
│              standard_price (VIEW — 집계)                    │
│              standard_price_master (확정 마스터)              │
│              anomaly_items (VIEW — 이상가 탐지)               │
└─────────────────────────┬────────────────────────────────────┘
                          ▼
┌──────────────────────────────────────────────────────────────┐
│                    ANALYSIS LAYER (예정)                     │
│                                                              │
│  AI 유사도 검색 (sentence-transformers + FAISS)              │
│  할인 룰 엔진 (Python rule-based)                            │
│  외부 시장가 조회 (나라장터 Open API)                          │
│  이상가 탐지 (±15% 임계값)                                    │
└─────────────────────────┬────────────────────────────────────┘
                          ▼
┌──────────────────────────────────────────────────────────────┐
│                     OUTPUT LAYER (예정)                      │
│                                                              │
│  검토 리포트 (Excel 다운로드)                                  │
│  대시보드 (Streamlit MVP → React 정식)                        │
│  단가 추이 그래프 (월별)                                       │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. 데이터 모델

### 3-1. 견적서 파일 분류 (detector.py 기준)

| 레이아웃 | 판별 조건 | 파서 |
|----------|-----------|------|
| `assembly` | 숫자 패턴 시트 ≥2개 (`10`, `20-1`...) | `parsers/assembly.py` |
| `standard` | HVAC/ASSY/조립기 등 키워드 or 갑지 시트 | `parsers/standard.py` |
| `unknown` | 미분류 → parse_log 기록, 수동 검수 | — |

### 3-2. 핵심 비즈니스 룰

| 룰 | 값 | 파일 |
|----|-----|------|
| 이상가 임계값 | ±15% | `config.py` ANOMALY_THRESHOLD |
| 최소 신뢰도 | 0.70 | `config.py` MIN_CONFIDENCE |
| 최소 금액 | 1,000원 | `config.py` MIN_AMOUNT |
| 최소 품명 길이 | 2글자 | `config.py` MIN_ITEM_NAME_LEN |
| 표준단가 집계 기준 | 동일 품목 2건 이상 | `schema.sql` HAVING COUNT >= 2 |

### 3-3. 품목 분류 체계

```
물품 / 설비 / 공사 / 용역
└── 세부 카테고리 (향후 확장)
```

---

## 4. 파일 구조 (현재 구현)

```
price_analyzer/
├── price_analyzer_v2/          # 현재 활성 시스템 (PostgreSQL)
│   ├── run_pipeline.py         # 엔트리포인트
│   ├── config.py               # 환경 설정
│   ├── db.py                   # DB 연결·스키마
│   ├── schema.sql              # PostgreSQL DDL
│   ├── pipeline/
│   │   ├── scanner.py          # 파일 탐색·해시
│   │   ├── reader.py           # Excel 읽기
│   │   ├── detector.py         # 레이아웃 분류
│   │   ├── parsers/
│   │   │   ├── standard.py     # 표준형 파서
│   │   │   └── assembly.py     # 조립기형 파서
│   │   ├── scorer.py           # 신뢰도 점수
│   │   ├── loader.py           # DB 저장
│   │   └── sync_master.py      # 마스터 동기화
│   └── 견적서 - 테스트 학습용/
│
├── parse_all.py                # v1 레거시 (JSON 기반)
├── apply_rules.py              # v1 후처리 규칙
├── build_db.py                 # v1 Excel 산출
│
├── docs/                       # 기획·설계 문서
│   ├── PRD_견적가비교분석시스템_v2.md
│   ├── BRD_견적가비교분석시스템_v2.md
│   ├── DESIGN.md               # 이 파일
│   └── TODO.md                 # 작업 로드맵
│
├── claude-docs/                # Claude 시스템 참고 문서
│   └── INDEX.md
│
└── 견적서/                     # 학습용 원본 데이터
    ├── 1차 학습/
    └── 2차 학습/
```

---

## 5. 기술 스택

| 구분 | 현재 | 목표 (MVP+) |
|------|------|-------------|
| 파싱 | Python, openpyxl, pdfplumber | 동일 |
| DB | PostgreSQL + pgvector | 동일 |
| AI 검색 | — | sentence-transformers (한국어) + FAISS |
| 할인 룰 | — | Python rule-based |
| 외부 시장가 | — | 나라장터 Open API |
| 백엔드 | — | FastAPI |
| 프론트엔드 | — | Streamlit (MVP) → React |

---

## 6. 미구현 핵심 모듈 (로드맵)

```
Phase 1 (현재 완료)
  ✅ 견적서 Excel/PDF 파싱
  ✅ PostgreSQL DB 저장
  ✅ 표준단가 집계·마스터 동기화
  ✅ 이상가 탐지 뷰 (+15%)

Phase 2 (진행 예정)
  ⬜ AI 유사도 검색 (F-205)
  ⬜ FastAPI 백엔드
  ⬜ Streamlit 대시보드 (MVP UI)
  ⬜ Excel 리포트 출력 (F-403)

Phase 3
  ⬜ 할인 룰 엔진 (F-311)
  ⬜ 외부 시장가 조회 (F-312)
  ⬜ 단가 추이 그래프 (F-404)
  ⬜ 다중 협력사 비교 (F-207)
  ⬜ React 정식 UI
```

---

## 7. Claude 시스템 구조 — 개발 워크플로우

> 참조: `claude-docs/configuration/memory.md`, `claude-docs/plugins/skills.md`, `claude-docs/reference/hooks.md`, `claude-docs/agent-sdk/subagents.md`

---

### 7-1. Memory (CLAUDE.md / Auto Memory)

Claude Code는 세션마다 컨텍스트 창이 초기화되므로, 지식을 이월하려면 두 가지 메모리 시스템을 명시적으로 활용해야 한다. **CLAUDE.md** (직접 작성)와 **Auto Memory** (Claude가 자동 기록)가 그것이다. 이 둘은 상호 보완적 역할을 하며, 세션 시작 시 모두 자동 로드된다.

| 구분 | 위치 | 작성 주체 | 용도 |
|------|------|-----------|------|
| Project CLAUDE.md | `price_analyzer/.claude/CLAUDE.md` | 개발자 직접 작성 | 파이프라인 구조, 파서 분류 기준, DB 스키마 요약 등 팀 공유 규칙 |
| Local CLAUDE.md | `price_analyzer/CLAUDE.local.md` (gitignore) | 개발자 직접 작성 | 로컬 DB 접속 정보, 개인 테스트 데이터 경로 |
| User CLAUDE.md | `~/.claude/CLAUDE.md` | 개발자 직접 작성 | 전역 코딩 지침 (단순성, 외과적 수정 등) |
| Auto Memory | `~/.claude/projects/.../memory/MEMORY.md` | Claude 자동 기록 | 세션 중 발견한 패턴, 디버깅 인사이트 |

**Project CLAUDE.md에 넣어야 할 내용 예:**
- `detector.py`가 `assembly` / `standard` / `unknown`을 판별하는 조건 (숫자 패턴 시트 수, 키워드 목록)
- `config.py` 임계값 요약 (`ANOMALY_THRESHOLD=0.15`, `MIN_CONFIDENCE=0.70`)
- 파이프라인 진입점: `run_pipeline.py` → `scanner` → `reader` → `detector` → `parsers/*` → `loader`
- 테스트 데이터 위치: `견적서 - 테스트 학습용/` 내 업체별 파일명 패턴

Auto Memory는 Claude가 디버깅 중 발견한 사실(예: "특정 업체 시트명에 공백 포함 시 `openpyxl` KeyError 발생")을 토픽 파일로 자동 저장한다. MEMORY.md 인덱스는 세션 시작 시 최대 200줄 또는 25KB가 로드되므로, 세부 내용은 `debugging.md`, `parser-patterns.md` 같은 별도 파일로 분리해 관리한다.

**실천 규칙:** 동일한 오류 맥락이나 파서 예외 처리를 두 번째 설명해야 하는 상황이 생기면, 그 내용은 CLAUDE.md에 추가한다.

---

### 7-2. Hooks

Hooks는 Claude Code 라이프사이클 이벤트에서 쉘 명령어를 자동 실행하는 메커니즘이다. CLAUDE.md의 "지침"과 달리 Hook은 Claude의 판단과 무관하게 **하드웨어 수준에서 강제 실행**된다.

**현재 전역 활성화된 훅 (`~/.claude/settings.json`):**
- `SessionStart`: `caveman-activate.js` — 세션 시작 시 토큰 절약 모드 초기화
- `UserPromptSubmit`: `caveman-mode-tracker.js`, `skill-reminder.js` — 매 프롬프트마다 실행

**프로젝트 전용 훅 예시 (`price_analyzer/.claude/settings.json`):**

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Bash",
      "hooks": [{
        "type": "command",
        "command": "python3 run_pipeline.py --status 2>&1 | tail -5",
        "statusMessage": "parse_log 이상 건 확인 중..."
      }]
    }],
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "python3 report_anomaly.py",
        "statusMessage": "이상가 탐지 결과 출력..."
      }]
    }]
  }
}
```

| 이벤트 | 발동 시점 | price_analyzer 활용 예 |
|--------|-----------|----------------------|
| `SessionStart` | 세션 시작 시 1회 | PostgreSQL 연결 상태 확인 |
| `UserPromptSubmit` | 매 프롬프트 제출 전 | 현재 파이프라인 상태 요약 주입 |
| `PreToolUse` | 각 툴 호출 전 | 파이프라인 실행 전 DB 백업 경고 |
| `PostToolUse` | 각 툴 호출 후 | 파싱 완료 후 `parse_log` 이상 건 자동 집계 |
| `Stop` | Claude 응답 완료 후 | `anomaly_items` 뷰 쿼리 결과 출력 |

PreToolUse 훅은 `permissionDecision: "deny"`를 반환해 특정 도구 호출을 차단할 수도 있다. 예를 들어, 학습용 원본 데이터(`견적서/` 디렉터리)에 대한 Write 호출을 자동 차단하는 보호 훅 작성이 가능하다.

---

### 7-3. Skills

Skills는 SKILL.md 파일로 정의하는 재사용 가능한 지침 단위로, CLAUDE.md와 달리 **호출 시에만 컨텍스트에 로드**된다. `/skill-name`으로 직접 호출하거나, description이 현재 대화 맥락과 일치하면 Claude가 자동으로 호출한다.

**저장 위치와 적용 범위:**
- `~/.claude/skills/<name>/SKILL.md` — 개인 전역 (모든 프로젝트)
- `price_analyzer/.claude/skills/<name>/SKILL.md` — 이 프로젝트 전용

**현재 활성화된 전역 플러그인 스킬 매핑:**

| 스킬 | 자동 트리거 조건 | price_analyzer 적용 예 |
|------|----------------|----------------------|
| `superpowers:brainstorming` | 새 기능 구현 전 | 새 레이아웃 파서 추가 전 설계 검토 |
| `superpowers:test-driven-development` | 구현 코드 작성 전 | `assembly.py` 파서 함수 작성 전 실패 테스트 먼저 작성 |
| `superpowers:systematic-debugging` | 버그/예외 발생 시 | `scorer.py` 신뢰도 점수 이상 시 원인 추적 |
| `superpowers:verification-before-completion` | 완료 선언 전 | 파이프라인 실행 확인 후 PR 생성 |
| `karpathy-guidelines` | 코드 작성·리뷰 시 | 파서 함수 단순성 강제 |
| `caveman:cavecrew-investigator` | 코드 위치 탐색 | "`assembly.py`가 어디서 호출되나?" 탐색 |
| `caveman:cavecrew-builder` | 단일 함수 수정 | `standard.py` 내 특정 함수 1건 수정 |

**프로젝트 전용 스킬 예시 (`.claude/skills/parse-and-check/SKILL.md`):**

```markdown
---
description: 견적서 파이프라인 실행 후 이상가 탐지 결과 요약. "파싱 실행", "파이프라인 돌려줘" 등에 자동 트리거.
---

## 최근 parse_log 상태
!`python3 run_pipeline.py --status 2>&1 | tail -20`

위 결과를 바탕으로 이상가(±15% 초과) 품목 목록과 `unknown` 레이아웃 건수를 요약하라.
```

---

### 7-4. Subagents

Subagent는 메인 대화에서 `Agent` 도구로 생성하는 독립 에이전트로, 각자 별도의 신선한 컨텍스트 창을 가진다. 중간 도구 호출 결과는 메인 대화에 누적되지 않고, 최종 메시지만 부모에게 반환된다.

| 작업 | 서브에이전트 타입 |
|------|----------------|
| `standard.py` / `assembly.py` 파서 리팩토링 | `cavecrew-builder` |
| `unknown` 레이아웃 파일 탐색 | `cavecrew-investigator` |
| AI 유사도 엔진 PoC + FastAPI 동시 개발 | `superpowers:dispatching-parallel-agents` |
| Streamlit MVP 화면 검증 | Playwright MCP 연동 |

`.claude/agents/` 디렉터리에 마크다운으로 재사용 가능한 에이전트를 정의할 수도 있다. 서브에이전트는 부모의 대화 이력·스킬을 상속하지 않으므로, `Agent` 도구의 prompt에 파일 경로·오류 메시지·판단 맥락을 명시적으로 포함시켜야 한다.

---

### 7-5. MCP

| MCP 서버 | 제공 도구 | price_analyzer 활용 |
|----------|-----------|-------------------|
| `playwright@claude-plugins-official` | 브라우저 탐색, 스냅샷 | Streamlit MVP 파일 업로드 UI, 비교 테이블, Excel 다운로드 E2E 검증 |
| `context7@claude-plugins-official` | 최신 라이브러리 문서 조회 | `sentence-transformers`, `FAISS`, `FastAPI` 문서 실시간 참조 |

Phase 2 FastAPI 구현 시 `price_analyzer/.mcp.json`에 프로젝트 전용 MCP 서버 추가 가능 — PostgreSQL 쿼리 도구, 나라장터 API 호출 도구 등을 Claude에게 직접 노출.

---

### 7-6. 신규 파서 추가 시 전체 워크플로우

```
1. [Memory] CLAUDE.md → 기존 파서 분류 기준, 파이프라인 구조 로드
2. [Skill: brainstorming] 새 파서 설계 전 요구사항·엣지케이스 탐색
3. [Skill: test-driven-development] 테스트 파일 먼저 작성 (테스트 데이터: 견적서 - 테스트 학습용/)
4. [Subagent: cavecrew-investigator] 기존 파서 호출 구조 탐색 (메인 컨텍스트 보호)
5. [Subagent: cavecrew-builder] parsers/new_layout.py 단일 파일 구현
6. [Hook: PostToolUse] 파이프라인 실행 후 parse_log 자동 점검
7. [Skill: verification-before-completion] 완료 선언 전 검증 → PR 생성
8. [Auto Memory] 신규 레이아웃 패턴·예외 처리 사항 자동 기록
```

이 흐름을 `.claude/skills/add-parser/SKILL.md`로 캡슐화하면 `/add-parser` 한 줄로 전체 절차를 일관되게 실행 가능하다.

---

*DESIGN.md — 현대위아 구매본부 × price_analyzer 팀*
