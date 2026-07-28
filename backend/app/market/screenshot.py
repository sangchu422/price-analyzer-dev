from __future__ import annotations

from typing import Protocol


class PageScreenshotter(Protocol):
    def capture(self, url: str) -> bytes | None: ...


class PlaywrightScreenshotter:
    def capture(self, url: str) -> bytes | None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    return page.screenshot(type="png", full_page=False)
                finally:
                    browser.close()
        except Exception:
            return None
