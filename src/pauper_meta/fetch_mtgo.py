"""Respectful HTTP fetch + raw cache for mtgo.com."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

from .paths import RAW_DIR, ensure_dirs

logger = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 PauperMetaTracker/0.1"
)
LISTING_URL = "https://www.mtgo.com/decklists?filter=Pauper"
MIN_DELAY_SEC = 1.5


def monthly_listing_url(year: int, month: int) -> str:
    return f"https://www.mtgo.com/decklists?filter=Pauper&month={month}&year={year}"


class MtgoFetcher:
    def __init__(
        self,
        raw_dir: Path | None = None,
        delay_sec: float = MIN_DELAY_SEC,
        session: requests.Session | None = None,
    ) -> None:
        ensure_dirs()
        self.raw_dir = raw_dir or RAW_DIR
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.delay_sec = max(0.0, delay_sec)
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", DEFAULT_UA)
        self.session.headers.setdefault("Accept", "text/html,application/xhtml+xml")
        self.session.headers.setdefault("Accept-Language", "en-US,en;q=0.9")
        self._last_fetch_at = 0.0

    def _throttle(self) -> None:
        if self.delay_sec <= 0:
            return
        elapsed = time.monotonic() - self._last_fetch_at
        wait = self.delay_sec - elapsed
        if wait > 0:
            time.sleep(wait)

    def get_text(self, url: str, timeout: float = 60.0) -> str:
        self._throttle()
        logger.info("GET %s", url)
        resp = self.session.get(url, timeout=timeout)
        self._last_fetch_at = time.monotonic()
        resp.raise_for_status()
        resp.encoding = resp.encoding or "utf-8"
        return resp.text

    def fetch_listing(self, use_cache: bool = False) -> tuple[str, Path]:
        cache_path = self.raw_dir / "listing-pauper.html"
        if use_cache and cache_path.exists():
            return cache_path.read_text(encoding="utf-8", errors="replace"), cache_path
        html = self.get_text(LISTING_URL)
        cache_path.write_text(html, encoding="utf-8")
        return html, cache_path

    def fetch_monthly_listing(self, year: int, month: int, use_cache: bool = True) -> tuple[str, Path]:
        cache_path = self.raw_dir / f"listing-pauper-{year:04d}-{month:02d}.html"
        if use_cache and cache_path.exists() and cache_path.stat().st_size > 10_000:
            return cache_path.read_text(encoding="utf-8", errors="replace"), cache_path
        html = self.get_text(monthly_listing_url(year, month))
        cache_path.write_text(html, encoding="utf-8")
        return html, cache_path

    def fetch_event(self, url: str, event_id: str, force: bool = False) -> tuple[str, Path]:
        # Prefer trailing slash (MTGO serves full embedded JSON that way).
        if not url.endswith("/"):
            url = url + "/"
        cache_path = self.raw_dir / f"event-{event_id}.html"
        json_cache = self.raw_dir / f"event-{event_id}.json"
        if not force and cache_path.exists() and cache_path.stat().st_size > 50_000:
            html = cache_path.read_text(encoding="utf-8", errors="replace")
            return html, cache_path
        html = self.get_text(url)
        cache_path.write_text(html, encoding="utf-8")
        # Also stash extracted JSON when present (best-effort).
        try:
            from .parse_mtgo import extract_decklists_data

            data = extract_decklists_data(html)
            import json

            json_cache.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 — cache is best-effort
            logger.debug("JSON cache skip for %s: %s", event_id, exc)
        return html, cache_path
