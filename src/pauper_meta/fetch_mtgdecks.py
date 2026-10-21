"""Fetch MTGDecks pages for Paper ingest (Playwright preferred; offline fixtures OK)."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .paths import RAW_PAPER_DIR, ensure_dirs

logger = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class CloudflareBlockedError(RuntimeError):
    """Raised when the response is a Cloudflare challenge interstitial."""


@dataclass
class FetchResult:
    html: str
    path: Path
    method: str  # playwright | offline | requests
    blocked: bool = False


def looks_like_cloudflare_challenge(html: str) -> bool:
    low = html.lower()
    markers = (
        "just a moment",
        "cf-browser-verification",
        "challenge-platform",
        "performing security verification",
        "cdn-cgi/challenge-platform",
        "attention required! | cloudflare",
    )
    return any(m in low for m in markers)


class MtgdecksFetcher:
    """Prefer Playwright; fall back to offline HTML. No CF bypass hacks."""

    def __init__(
        self,
        raw_dir: Path | None = None,
        *,
        timeout_ms: int = 60_000,
        wait_sec: float = 12.0,
    ) -> None:
        ensure_dirs()
        self.raw_dir = raw_dir or RAW_PAPER_DIR
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_ms = timeout_ms
        self.wait_sec = wait_sec

    def _cache_path(self, name: str) -> Path:
        safe = re_safe(name)
        return self.raw_dir / safe

    def load_offline(self, path: Path) -> FetchResult:
        html = path.read_text(encoding="utf-8", errors="replace")
        logger.info("offline HTML %s (%s bytes)", path, len(html))
        return FetchResult(html=html, path=path, method="offline", blocked=False)

    def fetch_playwright(self, url: str, cache_name: str) -> FetchResult:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError(
                "playwright not installed; pip install playwright && playwright install chromium"
            ) from e

        cache_path = self._cache_path(cache_name)
        logger.info("Playwright GET %s", url)
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                context = browser.new_context(
                    user_agent=DEFAULT_UA,
                    locale="en-US",
                    viewport={"width": 1280, "height": 800},
                )
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )
                page = context.new_page()
                resp = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                status = resp.status if resp else None
                # Give CF a short window; do not loop forever.
                deadline = time.monotonic() + max(0.0, self.wait_sec)
                while time.monotonic() < deadline:
                    title = (page.title() or "").lower()
                    if "moment" not in title and "cloudflare" not in title:
                        break
                    page.wait_for_timeout(1000)
                html = page.content()
            finally:
                browser.close()

        blocked = looks_like_cloudflare_challenge(html) or (status == 403)
        cache_path.write_text(html, encoding="utf-8")
        if blocked:
            logger.warning("Cloudflare challenge for %s (status=%s); cached interstitial", url, status)
            raise CloudflareBlockedError(f"Cloudflare blocked {url} (status={status})")
        logger.info("Playwright OK %s → %s (%s bytes)", url, cache_path, len(html))
        return FetchResult(html=html, path=cache_path, method="playwright", blocked=False)

    def fetch_url(self, url: str, cache_name: str, *, force_playwright: bool = True) -> FetchResult:
        if force_playwright:
            return self.fetch_playwright(url, cache_name)
        # Minimal requests attempt (expected to 403 behind CF) — useful for diagnostics.
        import requests

        cache_path = self._cache_path(cache_name)
        logger.info("requests GET %s", url)
        resp = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": DEFAULT_UA, "Accept": "text/html"},
        )
        html = resp.text
        cache_path.write_text(html, encoding="utf-8")
        blocked = looks_like_cloudflare_challenge(html) or resp.status_code == 403
        if blocked:
            raise CloudflareBlockedError(f"HTTP {resp.status_code} Cloudflare for {url}")
        return FetchResult(html=html, path=cache_path, method="requests", blocked=False)


def re_safe(name: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", name.strip())
    return s[:180] or "page.html"
