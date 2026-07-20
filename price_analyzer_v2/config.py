import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ── PostgreSQL ────────────────────────────────────────────────
DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", "5432"))
DB_NAME     = os.getenv("DB_NAME", "quote_db")
DB_USER     = os.getenv("DB_USER", "quote_app")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

DSN = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASSWORD}"

# ── 파일 경로 ─────────────────────────────────────────────────
QUOTE_FOLDER = Path(os.getenv(
    "QUOTE_FOLDER",
    r"D:\workroom\dev\price_analyzer_r1\견적서"
))

# ── 비즈니스 임계값 ───────────────────────────────────────────
ANOMALY_THRESHOLD = float(os.getenv("ANOMALY_THRESHOLD", "0.15"))
MIN_CONFIDENCE    = float(os.getenv("MIN_CONFIDENCE", "0.70"))
MIN_AMOUNT        = 1_000          # 1,000원 미만 라인은 무시
MIN_ITEM_NAME_LEN = 2              # 품명 최소 글자 수
