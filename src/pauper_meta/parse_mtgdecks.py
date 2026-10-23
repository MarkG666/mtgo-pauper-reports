"""Parse MTGDecks Pauper tournament listing + event HTML (Paper spike)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from typing import Iterable
from urllib.parse import urljoin

# Rank labels seen on MTGDecks event tables
_RANK_RE = re.compile(
    r"^(?P<label>1st|2nd|Top\s*4|Top\s*8|Top\s*16|Top\s*32|\d+(?:st|nd|rd|th))\b",
    re.I,
)
_PLAYERS_RE = re.compile(r"(?P<n>\d+)\s*Players?", re.I)
_EVENT_ID_RE = re.compile(r"tournament-(\d+)", re.I)
_DATE_META_RE = re.compile(
    r"Tournament\s*\|\s*(?P<date>\d{4}-\d{2}-\d{2})",
    re.I,
)
_OG_DATE_RE = re.compile(
    r"Tournament\s+(?P<date>\d{4}-\d{2}-\d{2})\.\s*Winner",
    re.I,
)
_BY_PLAYER_RE = re.compile(r"^\s*by\s+(.+?)\s*$", re.I)


@dataclass
class ListingEvent:
    event_id: str
    name: str
    path: str
    source_url: str
    player_count: int | None
    date_hint: str | None = None  # e.g. "06-Jun" from listing cell
    star_level: int | None = None


@dataclass
class PaperDeckRow:
    finish_rank: int
    place_label: str
    player: str
    archetype_label: str
    deck_name: str
    deck_path: str
    deck_url: str


@dataclass
class PaperEventParsed:
    event_id: str
    name: str
    player_count: int | None
    start_time: str | None  # YYYY-MM-DD when known
    source_url: str
    decks: list[PaperDeckRow] = field(default_factory=list)


def event_id_from_path(path_or_url: str) -> str | None:
    m = _EVENT_ID_RE.search(path_or_url)
    return m.group(1) if m else None


def absolute_mtgdecks_url(path: str, *, base_url: str = "https://mtgdecks.net") -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _strip_tags(html: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _iter_tr_blocks(html: str) -> Iterable[str]:
    for m in re.finditer(r"(?is)<tr\b[^>]*>(.*?)</tr>", html):
        yield m.group(0)


def _count_stars(tr_html: str) -> int:
    return len(re.findall(r"glyphicon-star", tr_html, flags=re.I))


def _first_tournament_href(tr_html: str) -> tuple[str, str] | None:
    """Return (path, anchor_text) for first tournament-* link in a row."""
    m = re.search(
        r'(?is)<a\b[^>]*href=["\']([^"\']*tournament-\d+[^"\']*)["\'][^>]*>(.*?)</a>',
        tr_html,
    )
    if not m:
        return None
    href = unescape(m.group(1).strip())
    # Drop accidental wayback prefixes if present
    href = re.sub(r"^https?://web\.archive\.org/web/\d+[a-z_]*/", "", href)
    if href.startswith("https://mtgdecks.net"):
        path = href[len("https://mtgdecks.net") :] or "/"
    elif href.startswith("http"):
        path = href
    else:
        path = href if href.startswith("/") else "/" + href
    name = _strip_tags(m.group(2))
    return path, name


def parse_listing_events(
    html: str,
    *,
    base_url: str = "https://mtgdecks.net",
    skip_mtgo_named: bool = True,
) -> list[ListingEvent]:
    """Parse Pauper tournament listing table rows."""
    out: list[ListingEvent] = []
    seen: set[str] = set()
    for tr in _iter_tr_blocks(html):
        link = _first_tournament_href(tr)
        if not link:
            continue
        path, name = link
        eid = event_id_from_path(path)
        if not eid or eid in seen:
            continue
        if skip_mtgo_named and re.search(r"\bMTGO\b", name, re.I):
            continue
        text = _strip_tags(tr)
        pm = _PLAYERS_RE.search(text)
        player_count: int | None = int(pm.group("n")) if pm else None
        # "?" Players → unknown
        if player_count is None and re.search(r"\?\s*Players?", text, re.I):
            player_count = None
        date_hint = None
        dm = re.search(r"\b(\d{1,2}-[A-Za-z]{3})\b", text)
        if dm:
            date_hint = dm.group(1)
        source_url = absolute_mtgdecks_url(path, base_url=base_url)
        seen.add(eid)
        out.append(
            ListingEvent(
                event_id=eid,
                name=name,
                path=path if path.startswith("/") else "/" + path.split("mtgdecks.net", 1)[-1].lstrip("/"),
                source_url=source_url,
                player_count=player_count,
                date_hint=date_hint,
                star_level=_count_stars(tr) or None,
            )
        )
    return out


def filter_large_events(
    events: list[ListingEvent],
    *,
    min_players: int = 50,
    skip_unknown_player_count: bool = True,
) -> list[ListingEvent]:
    kept: list[ListingEvent] = []
    for ev in events:
        if ev.player_count is None:
            if skip_unknown_player_count:
                continue
            continue
        if ev.player_count >= min_players:
            kept.append(ev)
    return kept


def _rank_from_label(label: str) -> int | None:
    cleaned = re.sub(r"\s+", "", label.strip())
    low = cleaned.lower()
    if low == "1st":
        return 1
    if low == "2nd":
        return 2
    if low.startswith("top"):
        try:
            n = int(re.sub(r"\D", "", low) or "0")
        except ValueError:
            return None
        # Top4 → ranks 3-4, Top8 → 5-8; assign band start; caller may refine by order
        return {4: 3, 8: 5, 16: 9, 32: 17}.get(n, n)
    m = re.match(r"(\d+)", low)
    return int(m.group(1)) if m else None


def _place_label_canonical(raw: str) -> str:
    s = re.sub(r"\s+", "", raw.strip())
    mapping = {
        "1st": "優勝",
        "2nd": "準優勝",
        "top4": "Top4",
        "top8": "Top8",
        "top16": "Top16",
        "top32": "Top32",
    }
    return mapping.get(s.lower(), raw.strip())


def parse_event_page(
    html: str,
    *,
    event_id: str,
    source_url: str,
    base_url: str = "https://mtgdecks.net",
    fallback_name: str | None = None,
    fallback_players: int | None = None,
    top_n: int = 8,
) -> PaperEventParsed:
    """Parse event detail page: name, player count, TopN deck rows."""
    name = fallback_name or "Untitled"
    title_m = re.search(r"(?is)<title>(.*?)</title>", html)
    if title_m:
        title = _strip_tags(title_m.group(1))
        # "Name | June 06 • MTG DECKS"
        name = title.split("|", 1)[0].strip() or name
    og_m = re.search(
        r'(?is)<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
        html,
    )
    if og_m:
        name = unescape(og_m.group(1)).strip() or name

    player_count = fallback_players
    for m in _PLAYERS_RE.finditer(html):
        # Prefer the header button-style count (first reasonable hit)
        n = int(m.group("n"))
        if 2 <= n <= 5000:
            player_count = n
            break

    start_time = None
    for rx in (_DATE_META_RE, _OG_DATE_RE):
        dm = rx.search(_strip_tags(html) if rx is _DATE_META_RE else html)
        if not dm:
            dm = rx.search(html)
        if dm:
            start_time = dm.group("date")
            break
    if not start_time:
        # meta description often has YYYY-MM-DD
        dm = re.search(r"(20\d{2}-\d{2}-\d{2})", html)
        if dm:
            start_time = dm.group(1)

    decks: list[PaperDeckRow] = []
    top4_seen = 0
    top8_seen = 0
    for tr in _iter_tr_blocks(html):
        # Rank cell
        rm = re.search(r"(?is)<td[^>]*>\s*(?:<strong>)?\s*([^<]+?)\s*(?:</strong>)?\s*</td>", tr)
        if not rm:
            continue
        rank_raw = _strip_tags(rm.group(1))
        if not _RANK_RE.match(rank_raw):
            continue
        finish = _rank_from_label(rank_raw)
        if finish is None:
            continue
        # Refine Top4/Top8 ordinals by encounter order
        low = re.sub(r"\s+", "", rank_raw).lower()
        if low == "top4":
            finish = 3 + top4_seen
            top4_seen += 1
        elif low == "top8":
            finish = 5 + top8_seen
            top8_seen += 1

        # Deck link + name
        am = re.search(
            r'(?is)<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>\s*(.*?)\s*</a>',
            tr,
        )
        if not am:
            continue
        deck_path = unescape(am.group(1).strip())
        deck_path = re.sub(r"^https?://web\.archive\.org/web/\d+[a-z_]*/", "", deck_path)
        if deck_path.startswith("https://mtgdecks.net"):
            deck_path = deck_path[len("https://mtgdecks.net") :]
        deck_name = _strip_tags(am.group(2))
        if "tournament-" in deck_path:
            # listing-style link; skip
            continue

        # Player: "by name"
        player = "Unknown"
        for sm in re.finditer(r"(?is)<span[^>]*text-capitalize[^>]*>(.*?)</span>", tr):
            ptxt = _strip_tags(sm.group(1))
            bm = _BY_PLAYER_RE.match(ptxt)
            if bm:
                player = bm.group(1).strip()
                break
        if player == "Unknown":
            bm = re.search(r"(?is)\bby\s+([A-Za-z0-9_ .\-']+)", _strip_tags(tr))
            if bm:
                player = bm.group(1).strip()

        # Archetype column: first .small that is not mana icons
        arch = ""
        for sm in re.finditer(r'(?is)<span[^>]*class=["\'][^"\']*small[^"\']*["\'][^>]*>(.*?)</span>', tr):
            candidate = _strip_tags(sm.group(1))
            if not candidate or candidate.lower().startswith("by "):
                continue
            if re.fullmatch(r"[\W\d_$]+", candidate):
                continue
            if "spiciness" in candidate.lower() or candidate.startswith("$"):
                continue
            arch = candidate
            break
        if not arch:
            arch = "Unknown"

        decks.append(
            PaperDeckRow(
                finish_rank=finish,
                place_label=_place_label_canonical(rank_raw),
                player=player,
                archetype_label=arch,
                deck_name=deck_name,
                deck_path=deck_path if deck_path.startswith("/") else "/" + deck_path.lstrip("/"),
                deck_url=absolute_mtgdecks_url(deck_path, base_url=base_url),
            )
        )
        if len(decks) >= top_n:
            break

    return PaperEventParsed(
        event_id=event_id,
        name=name,
        player_count=player_count,
        start_time=start_time,
        source_url=source_url,
        decks=decks,
    )
