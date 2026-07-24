import os
from pathlib import Path
from dotenv import load_dotenv

_HERE = Path(__file__).parent          # price_analyzer_v2/
PROJECT_ROOT = _HERE.parent            # price_analyzer/

load_dotenv(_HERE / ".env")

# ── hChat 임베딩 API ──────────────────────────────────────────
H_CHAT_BASE_URL  = os.getenv("H_CHAT_BASE_URL", "https://internal-apigw-kr.hmg-corp.io/hchat-in/api/v3")
H_CHAT_API_KEY   = os.getenv("H_CHAT_API_KEY", "")
H_CHAT_PROJECT_ID = os.getenv("H_CHAT_PROJECT_ID", "")
EMBED_MODEL      = "text-embedding-3-large"

# ── 파일 경로 (상대경로는 프로젝트 루트 기준 해석) ─────────────
_quote_raw = os.getenv("QUOTE_FOLDER", "견적서")
QUOTE_FOLDER = Path(_quote_raw)
if not QUOTE_FOLDER.is_absolute():
    QUOTE_FOLDER = PROJECT_ROOT / QUOTE_FOLDER

EMBED_INDEX_PATH = PROJECT_ROOT / "price_analyzer_v2" / "embed_index.json"

# ── 비즈니스 임계값 ───────────────────────────────────────────
ANOMALY_THRESHOLD = float(os.getenv("ANOMALY_THRESHOLD", "0.15"))
MIN_CONFIDENCE    = float(os.getenv("MIN_CONFIDENCE", "0.70"))
MIN_AMOUNT        = 1_000
MIN_ITEM_NAME_LEN = 2

# ── PostgreSQL (레거시, Codex가 SQLite로 교체 예정) ───────────
DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", "5432"))
DB_NAME     = os.getenv("DB_NAME", "quote_db")
DB_USER     = os.getenv("DB_USER", "quote_app")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DSN = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASSWORD}"
