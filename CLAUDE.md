# 협력사 견적 단가 AI 분석 시스템 — 작업 규칙

협력사 견적서(Excel/PDF)를 파싱해 품목별 표준단가 DB를 구축하고, 신규 견적서의 단가 적정성을 AI로 자동 검토하는 시스템.

**핵심 파이프라인**
```
견적서(xlsx/pdf) → parse_all.py → apply_rules.py → 표준단가DB_집계.json → build_db.py → 표준단가DB.xlsx
```

---

## 개발 환경

- Python 3.x, openpyxl, pdfplumber
- 실행: `python parse_all.py` → `python build_db.py`
- 플랫폼: Windows (경로 구분자 주의)

---

## 참조 문서

| 문서 | 내용 |
|------|------|
| [`docs/PARSING_RULES.md`](docs/PARSING_RULES.md) | 견적서 파싱 규칙 상세 명세 (파서 종류, 규칙 절차, 데이터 품질, 출력 파일) |
| [`docs/DESIGN.md`](docs/DESIGN.md) | 시스템 설계 문서 |
| [`docs/TODO.md`](docs/TODO.md) | 작업 목록 |
| [`.claude/CLAUDE.md`](.claude/CLAUDE.md) | AI 작업 행동 지침 |
