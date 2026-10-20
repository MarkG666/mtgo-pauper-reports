"""Best-effort MTGGoldfish deck/event links (no Cloudflare bypass)."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests

from .paths import DATA_DIR, ensure_dirs

logger = logging.getLogger(__name__)

GF_BASE = "https://www.mtggoldfish.com"
MIN_DELAY_SEC = 1.5
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# href="/deck/8001122">Archetype</a> ... href="/player/MisterTwin"
PAIR_RE = re.compile(
    r'href="/deck/(\d+)"[^>]*>([^<]+)</a>.*?href="/player/([^"]+)"',
    re.S | re.I,
)
# Fallback: title="Archetype - Player" on visual deck links
TITLE_DECK_RE = re.compile(
    r'title="[^"]+\s+-\s+([^"]+)"\s+href="/deck/visual/(\d+)',
    re.I,
)


def tournament_slug(event_name: str, event_date: str) -> str | None:
    """
    Build Goldfish tournament slug.
    e.g. "Pauper Challenge 32" + "2026-10-14" → "pauper-challenge-32-2026-10-14"
    """
    name = (event_name or "").strip().lower()
    date = (event_date or "").strip()[:10]
    if not name or len(date) < 10:
        return None
    # Keep alnum and spaces/hyphens
    cleaned = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    if not cleaned:
        return None
    return f"{cleaned}-{date}"


def tournament_url(event_name: str, event_date: str) -> str | None:
    slug = tournament_slug(event_name, event_date)
    if not slug:
        return None
    return f"{GF_BASE}/tournament/{slug}"


class GoldfishLinkResolver:
    """Cache tournament HTML and resolve player → deck URLs."""

    def __init__(self, cache_dir: Path | None = None, delay_sec: float = MIN_DELAY_SEC) -> None:
        ensure_dirs()
        self.cache_dir = cache_dir or (DATA_DIR / "raw" / "goldfish")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay_sec = max(0.0, delay_sec)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": _UA, "Accept": "text/html"})
        self._last_fetch = 0.0
        self._player_maps: dict[str, dict[str, str]] = {}  # tournament_url → player→deck_url

    def _throttle(self) -> None:
        wait = self.delay_sec - (time.monotonic() - self._last_fetch)
        if wait > 0:
            time.sleep(wait)

    def _cache_path(self, slug: str) -> Path:
        return self.cache_dir / f"tournament-{slug}.html"

    def fetch_tournament_html(self, url: str, slug: str, force: bool = False) -> str | None:
        path = self._cache_path(slug)
        if not force and path.exists() and path.stat().st_size > 5_000:
            return path.read_text(encoding="utf-8", errors="replace")
        try:
            self._throttle()
            logger.info("GET Goldfish %s", url)
            resp = self.session.get(url, timeout=45)
            self._last_fetch = time.monotonic()
            if resp.status_code != 200:
                logger.warning("Goldfish %s → HTTP %s", url, resp.status_code)
                return None
            text = resp.text
            path.write_text(text, encoding="utf-8")
            return text
        except requests.RequestException as exc:
            logger.warning("Goldfish fetch failed %s: %s", url, exc)
            return None

    @staticmethod
    def parse_player_decks(html: str) -> dict[str, str]:
        """Map player login → absolute /deck/N URL."""
        out: dict[str, str] = {}
        for deck_id, _arch, player in PAIR_RE.findall(html):
            player = player.strip()
            if player and player not in out:
                out[player] = f"{GF_BASE}/deck/{deck_id}"
        if not out:
            for player, deck_id in TITLE_DECK_RE.findall(html):
                player = player.strip()
                if player and player not in out:
                    out[player] = f"{GF_BASE}/deck/{deck_id}"
        return out

    def resolve_deck_link(
        self,
        *,
        event_name: str,
        event_date: str,
        player: str,
        mtgo_event_url: str | None = None,
    ) -> tuple[str | None, str]:
        """
        Returns (url, source) where source is:
          goldfish-deck | goldfish-tournament | mtgo-event | none
        """
        t_url = tournament_url(event_name, event_date)
        slug = tournament_slug(event_name, event_date)
        if t_url and slug:
            if t_url not in self._player_maps:
                html = self.fetch_tournament_html(t_url, slug)
                self._player_maps[t_url] = self.parse_player_decks(html) if html else {}
            deck = self._player_maps[t_url].get(player)
            if deck:
                return deck, "goldfish-deck"
            # Tournament page exists (or was cached empty) — still prefer tournament URL
            if self._cache_path(slug).exists() or self._player_maps[t_url] is not None:
                # If we got HTML with other decks, tournament page is valid
                if self._player_maps[t_url] or self._cache_path(slug).exists():
                    cache = self._cache_path(slug)
                    if cache.exists() and cache.stat().st_size > 5_000:
                        return t_url, "goldfish-tournament"
                # Try constructing tournament URL even without successful parse
                return t_url, "goldfish-tournament"

        if mtgo_event_url:
            base = mtgo_event_url if mtgo_event_url.endswith("/") else mtgo_event_url + "/"
            return f"{base}#decklistDecks", "mtgo-event"
        return None, "none"


# Module-level resolver for report generation (one fetch per tournament per process)
_RESOLVER: GoldfishLinkResolver | None = None


def get_resolver() -> GoldfishLinkResolver:
    global _RESOLVER
    if _RESOLVER is None:
        _RESOLVER = GoldfishLinkResolver()
    return _RESOLVER


def deck_search_fallback_url(player: str, event_name: str) -> str:
    """Last-resort Goldfish search URL (not a direct deck page)."""
    q = quote(f"{player} {event_name}")
    return f"{GF_BASE}/decks?deck_format=pauper&q={q}"
