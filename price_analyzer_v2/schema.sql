-- ============================================================
-- 협력사 견적 단가 분석 시스템 v2 — PostgreSQL 스키마
-- ============================================================

-- DB 생성 (psql 에서 실행: \c postgres 후 아래 명령 실행)
-- CREATE DATABASE quote_db ENCODING 'UTF8' LC_COLLATE 'Korean_Korea.949' TEMPLATE template0;
-- CREATE USER quote_app WITH PASSWORD 'your_password';
-- GRANT ALL PRIVILEGES ON DATABASE quote_db TO quote_app;
-- \c quote_db
-- GRANT ALL ON SCHEMA public TO quote_app;

-- ─────────────────────────────────────────────
-- 1. 견적서 헤더 (Excel 파일 1개 = 1행)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS quote_header (
    id              SERIAL PRIMARY KEY,
    file_path       TEXT NOT NULL UNIQUE,       -- 절대 경로
    file_hash       CHAR(64),                   -- SHA-256 (중복 방지)
    file_name       TEXT,                       -- 파일명
    vendor          TEXT,                       -- 공급사명
    quote_no        TEXT,                       -- 견적번호
    quote_date      DATE,                       -- 견적일
    project         TEXT,                       -- 공사/프로젝트명
    total_amount    BIGINT,                     -- 총 견적금액 (원)
    layout_group    TEXT,                       -- 레이아웃 그룹: standard / assembly / unknown
    item_count      INTEGER DEFAULT 0,          -- 추출된 라인 수
    confidence      NUMERIC(3,2) DEFAULT 1.00,  -- 파싱 신뢰도 (0.00~1.00)
    parse_ok        BOOLEAN DEFAULT TRUE,
    reviewed        BOOLEAN DEFAULT FALSE,      -- 담당자 검수 완료 여부
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- 2. 견적서 라인 아이템 (핵심 데이터)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS quote_item (
    id              SERIAL PRIMARY KEY,
    header_id       INTEGER NOT NULL REFERENCES quote_header(id) ON DELETE CASCADE,
    unit_name       TEXT,                       -- 단위장비명 (시트명 또는 공정명)
    item_name       TEXT NOT NULL,              -- 품명 (원본 그대로 보존)
    spec            TEXT,                       -- 규격
    unit            TEXT,                       -- 단위 (원본)
    unit_norm       TEXT,                       -- 정규화 단위 (EA/SET/M/KG...)
    quantity        NUMERIC(12,3),              -- 수량
    unit_price      BIGINT,                     -- 단가 (원)
    amount          BIGINT,                     -- 금액 (원)
    maker           TEXT,                       -- 메이커/브랜드
    category        TEXT DEFAULT '설비',        -- 물품/설비/공사/용역
    confidence      NUMERIC(3,2) DEFAULT 1.00,  -- 행 단위 신뢰도
    flagged         BOOLEAN DEFAULT FALSE,      -- 이상가 플래그
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- 3. 파싱 로그 (오류 추적 · 감사 이력)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS parse_log (
    id          SERIAL PRIMARY KEY,
    file_path   TEXT,
    status      TEXT,               -- ok / error / skip / irm_protected
    message     TEXT,
    item_count  INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- 인덱스
-- ─────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_item_name     ON quote_item (item_name);
CREATE INDEX IF NOT EXISTS idx_item_header   ON quote_item (header_id);
CREATE INDEX IF NOT EXISTS idx_item_flagged  ON quote_item (flagged) WHERE flagged = TRUE;
CREATE INDEX IF NOT EXISTS idx_header_vendor ON quote_header (vendor);
CREATE INDEX IF NOT EXISTS idx_header_date   ON quote_header (quote_date);
CREATE INDEX IF NOT EXISTS idx_header_layout ON quote_header (layout_group);

-- ─────────────────────────────────────────────
-- 4. 표준단가 마스터 (수동 관리 가능한 확정 테이블)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS standard_price_master (
    id                SERIAL          PRIMARY KEY,
    item_id           TEXT            UNIQUE NOT NULL,         -- 표준품목ID: SP-00001
    category          TEXT            DEFAULT '',              -- 품목분류
    item_name         TEXT            NOT NULL,                -- 품명
    spec              TEXT            DEFAULT '',              -- 규격
    unit              TEXT            DEFAULT '',              -- 단위
    price_min         BIGINT,                                  -- 단가_최저(원)
    price_avg         BIGINT,                                  -- 단가_평균(원)
    price_max         BIGINT,                                  -- 단가_최고(원)
    data_count        INTEGER         DEFAULT 0,               -- 데이터건수
    main_maker        TEXT            DEFAULT '',              -- 주요메이커
    latest_quote_date DATE,                                    -- 최근견적일
    source_vendors    TEXT            DEFAULT '',              -- 출처공급사 (콤마 구분)
    manually_reviewed BOOLEAN         DEFAULT FALSE,           -- 수동검토완료 (TRUE면 자동동기화 덮어쓰기 안 함)
    review_note       TEXT            DEFAULT '',              -- 검토메모
    created_at        TIMESTAMPTZ     DEFAULT NOW(),           -- 마스터 최초 등록일 (변경 안 함)
    updated_at        TIMESTAMPTZ     DEFAULT NOW(),           -- 마지막 동기화 갱신일
    UNIQUE (item_name, spec, unit)                            -- 비즈니스 키
);

CREATE INDEX IF NOT EXISTS idx_master_item_name ON standard_price_master (item_name);
CREATE INDEX IF NOT EXISTS idx_master_reviewed  ON standard_price_master (manually_reviewed);

-- ─────────────────────────────────────────────
-- 표준단가 집계 뷰 (quote_item → 실시간 집계, 마스터 동기화 원본)
-- ─────────────────────────────────────────────
CREATE OR REPLACE VIEW standard_price AS
SELECT
    qi.item_name,
    qi.spec,
    qi.unit_norm                                              AS unit,
    MAX(qi.category)                                          AS category,
    COUNT(*)                                                  AS sample_count,
    MIN(qi.unit_price)                                        AS price_min,
    ROUND(AVG(qi.unit_price))                                 AS price_avg,
    MAX(qi.unit_price)                                        AS price_max,
    MAX(qh.quote_date)                                        AS latest_quote_date,
    STRING_AGG(DISTINCT COALESCE(qh.vendor,''), ', '
               ORDER BY COALESCE(qh.vendor,''))               AS source_vendors
FROM  quote_item  qi
JOIN  quote_header qh ON qh.id = qi.header_id
WHERE qi.unit_price > 0
  AND qi.confidence  >= 0.70
  AND qi.flagged     = FALSE
  AND qh.parse_ok    = TRUE
GROUP BY qi.item_name, qi.spec, qi.unit_norm
HAVING COUNT(*) >= 2;

-- ─────────────────────────────────────────────
-- 이상가 탐지 뷰 (마스터 단가 기준 +15% 초과 건)
-- ─────────────────────────────────────────────
CREATE OR REPLACE VIEW anomaly_items AS
SELECT
    qi.id,
    qh.vendor,
    qh.quote_no,
    qh.quote_date,
    qh.project,
    qi.item_name,
    qi.spec,
    qi.unit_price                                             AS submitted_price,
    spm.price_avg                                             AS std_price_avg,
    ROUND((qi.unit_price - spm.price_avg) * 100.0
          / NULLIF(spm.price_avg, 0), 1)                      AS deviation_pct
FROM  quote_item   qi
JOIN  quote_header qh  ON qh.id  = qi.header_id
JOIN  standard_price_master spm
      ON  spm.item_name = qi.item_name
      AND spm.spec      = qi.spec
WHERE qi.unit_price > spm.price_avg * 1.15
  AND qh.parse_ok = TRUE
ORDER BY deviation_pct DESC;
