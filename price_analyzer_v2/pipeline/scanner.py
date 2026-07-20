"""
파일 탐색 · 해시 계산 · 처리 대상 필터링
"""
import hashlib
from pathlib import Path


SUPPORTED = {".xlsx", ".xls"}


def compute_hash(path: Path, chunk=65536) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk_data := f.read(chunk):
            h.update(chunk_data)
    return h.hexdigest()


def scan_files(folder: Path) -> list[Path]:
    """folder 하위의 모든 Excel 파일을 반환 (임시파일 ~$ 제외)."""
    files = []
    for ext in SUPPORTED:
        for p in folder.rglob(f"*{ext}"):
            if p.name.startswith("~$"):
                continue
            files.append(p)
    return sorted(files)


def filter_new(files: list[Path], conn) -> list[tuple[Path, str]]:
    """DB에 아직 없는 파일만 (path, hash) 튜플로 반환."""
    result = []
    for path in files:
        file_hash = compute_hash(path)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM quote_header WHERE file_hash = %s LIMIT 1",
                (file_hash,)
            )
            if cur.fetchone() is None:
                result.append((path, file_hash))
    return result
