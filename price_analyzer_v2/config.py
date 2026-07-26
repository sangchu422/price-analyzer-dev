import os
from pathlib import Path

from dotenv import load_dotenv


_HERE = Path(__file__).parent
PROJECT_ROOT = _HERE.parent

load_dotenv(_HERE / ".env")

# hChat credentials and endpoints must be supplied by the intranet environment.
H_CHAT_BASE_URL = os.getenv("H_CHAT_BASE_URL", "")
H_CHAT_API_KEY = os.getenv("H_CHAT_API_KEY", "")
H_CHAT_PROJECT_ID = os.getenv("H_CHAT_PROJECT_ID", "")
EMBED_MODEL = os.getenv("H_CHAT_EMBED_MODEL", "text-embedding-3-large")

_quote_raw = os.getenv("QUOTE_FOLDER", "견적서")
QUOTE_FOLDER = Path(_quote_raw)
if not QUOTE_FOLDER.is_absolute():
    QUOTE_FOLDER = PROJECT_ROOT / QUOTE_FOLDER

EMBED_INDEX_PATH = PROJECT_ROOT / "price_analyzer_v2" / "embed_index.json"

ANOMALY_THRESHOLD = float(os.getenv("ANOMALY_THRESHOLD", "0.15"))
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.70"))
MIN_AMOUNT = 1_000
MIN_ITEM_NAME_LEN = 2

# Legacy PostgreSQL settings remain environment-only.
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "quote_db")
DB_USER = os.getenv("DB_USER", "quote_app")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DSN = (
    f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} "
    f"user={DB_USER} password={DB_PASSWORD}"
)
