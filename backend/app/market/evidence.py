from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import httpx

from app.market.adapters.base import CollectedProduct


@dataclass(frozen=True)
class SavedEvidence:
    raw_path: str
    raw_sha256: str
    image_path: str | None
    image_sha256: str | None
    screenshot_path: str | None
    screenshot_sha256: str | None


class EvidenceStore:
    def __init__(self, root: Path, *, timeout: float = 15.0) -> None:
        self.root = root.resolve()
        self.timeout = timeout

    def save(self, run_key: str, product: CollectedProduct) -> SavedEvidence:
        product_dir = self.root / run_key / _safe_name(product.source_product_id)
        product_dir.mkdir(parents=True, exist_ok=True)
        raw_path, raw_hash = self._write(
            product_dir / f"source{product.raw_extension}",
            product.raw_payload,
        )
        image_bytes = product.image_bytes
        image_extension = product.image_extension
        if image_bytes is None and product.image_url:
            try:
                response = httpx.get(
                    product.image_url,
                    timeout=self.timeout,
                    follow_redirects=True,
                )
                response.raise_for_status()
                image_bytes = response.content
                image_extension = _image_extension(
                    response.headers.get("content-type"),
                    product.image_url,
                )
            except httpx.HTTPError:
                image_bytes = None
        image_path = image_hash = None
        if image_bytes:
            image_path, image_hash = self._write(
                product_dir / f"product{image_extension or '.bin'}",
                image_bytes,
            )
        screenshot_path = screenshot_hash = None
        if product.screenshot_bytes:
            screenshot_path, screenshot_hash = self._write(
                product_dir / "page.png",
                product.screenshot_bytes,
            )
        return SavedEvidence(
            raw_path,
            raw_hash,
            image_path,
            image_hash,
            screenshot_path,
            screenshot_hash,
        )

    def resolve(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("evidence path escapes configured root")
        return candidate

    def _write(self, path: Path, content: bytes) -> tuple[str, str]:
        path.write_bytes(content)
        return path.relative_to(self.root).as_posix(), sha256(content).hexdigest()


def _safe_name(value: str) -> str:
    safe = "".join(character if character.isalnum() else "_" for character in value)
    return safe[:100] or "product"


def _image_extension(content_type: str | None, url: str) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    if content_type:
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type in mapping:
            return mapping[media_type]
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    return suffix if suffix in mapping.values() else ".bin"
