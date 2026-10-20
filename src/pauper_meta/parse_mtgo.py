"""Parse MTGO decklist listing HTML and embedded window.MTGO.decklists.data."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

LISTING_BASE = "https://www.mtgo.com"
DECKLIST_HREF_RE = re.compile(r'href="(/decklist/[^"]+)"', re.I)
DATA_RE = re.compile(
    r"window\.MTGO\.decklists\.data\s*=\s*(\{.*?\});\s*window\.MTGO\.decklists\.type",
    re.S,
)

# Challenge + Preliminary only (League excluded intentionally).
CHALLENGE_RE = re.compile(r"pauper[- ]challenge", re.I)
PRELIM_RE = re.compile(r"pauper[- ]prelim", re.I)
LEAGUE_RE = re.compile(r"pauper[- ]league", re.I)

ROUND_NAMES = {
    0: "final",
    1: "semifinals",
    2: "quarterfinals",
    3: "eighth-finals",
    4: "16th-finals",
    5: "32nd-finals",
    6: "64th-finals",
}


@dataclass
class ListingEvent:
    slug: str
    url: str
    event_type: str  # challenge | preliminary


@dataclass
class ParsedCard:
    name: str
    qty: int
    board: str  # main | side
    colors: tuple[str, ...] = ()  # W/U/B/R/G letters
    card_type: str = ""


@dataclass
class ParsedDeck:
    deck_id: str
    player: str
    loginid: str
    finish_rank: int | None = None
    swiss_rank: int | None = None
    deck_index: int | None = None  # position in MTGO decklists array (for page anchors)
    swiss_wins: int | None = None
    swiss_losses: int | None = None
    cards: list[ParsedCard] = field(default_factory=list)


@dataclass
class ParsedMatch:
    round_index: int
    round_label: str
    player_a: str
    player_b: str
    wins_a: int
    wins_b: int
    winner: str


@dataclass
class ParsedEvent:
    event_id: str
    name: str
    start_time: str
    event_type: str
    player_count: int | None
    source_url: str
    decks: list[ParsedDeck] = field(default_factory=list)
    matches: list[ParsedMatch] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def classify_event_type(name_or_slug: str) -> str | None:
    text = name_or_slug or ""
    if LEAGUE_RE.search(text):
        return None
    if CHALLENGE_RE.search(text):
        return "challenge"
    if PRELIM_RE.search(text):
        return "preliminary"
    return None


def extract_listing_events(html: str, base_url: str = LISTING_BASE) -> list[ListingEvent]:
    seen: set[str] = set()
    out: list[ListingEvent] = []
    for m in DECKLIST_HREF_RE.finditer(html):
        path = m.group(1)
        slug = path.split("/decklist/", 1)[-1].strip("/")
        if not slug.lower().startswith("pauper-"):
            continue
        et = classify_event_type(slug)
        if not et:
            continue
        if slug in seen:
            continue
        seen.add(slug)
        # Detail pages need trailing slash for full embedded JSON.
        url = urljoin(base_url, f"/decklist/{slug}/")
        out.append(ListingEvent(slug=slug, url=url, event_type=et))
    return out


def extract_decklists_data(html: str) -> dict[str, Any]:
    m = DATA_RE.search(html)
    if not m:
        raise ValueError("window.MTGO.decklists.data not found in HTML")
    return json.loads(m.group(1))


def _player_count(raw: dict[str, Any]) -> int | None:
    pc = raw.get("player_count")
    if isinstance(pc, dict):
        for key in ("players", "queued_players"):
            if pc.get(key) not in (None, ""):
                try:
                    return int(pc[key])
                except (TypeError, ValueError):
                    pass
        return None
    if pc in (None, ""):
        return None
    try:
        return int(pc)
    except (TypeError, ValueError):
        return None


def _card_entries(items: list[dict[str, Any]] | None, board: str) -> list[ParsedCard]:
    from .colors import normalize_mtgo_colors

    out: list[ParsedCard] = []
    for item in items or []:
        attrs = item.get("card_attributes") or {}
        name = (attrs.get("card_name") or item.get("card_name") or "").strip()
        if not name:
            continue
        try:
            qty = int(item.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        letters = normalize_mtgo_colors(attrs.get("colors"))
        # Also accept singular "color" when colors list missing
        if not letters and attrs.get("color"):
            letters = normalize_mtgo_colors([attrs.get("color")])
        card_type = str(attrs.get("card_type") or "").strip()
        out.append(
            ParsedCard(
                name=name,
                qty=qty,
                board=board,
                colors=tuple(sorted(letters, key=lambda x: "WUBRG".index(x) if x in "WUBRG" else 9)),
                card_type=card_type,
            )
        )
    return out


def parse_event(raw: dict[str, Any], fallback_type: str | None = None, source_url: str = "") -> ParsedEvent:
    name = str(raw.get("description") or "").strip() or "Unknown Event"
    et = classify_event_type(name) or fallback_type
    if not et:
        raise ValueError(f"Unsupported event type for: {name}")

    event_id = str(raw.get("event_id") or "").strip()
    if not event_id:
        raise ValueError("event_id missing")

    # Prefer an actual /decklist/ URL (MTGO raw.url is sometimes a generic premier-play page).
    candidates = [source_url, str(raw.get("url") or "")]
    url = ""
    for cand in candidates:
        if cand and "/decklist/" in cand:
            url = cand
            break
    if not url:
        url = source_url or str(raw.get("url") or "")
    if url and not url.endswith("/"):
        url = url + "/"

    # ranks
    final_by_login: dict[str, int] = {}
    for row in raw.get("final_rank") or []:
        login = str(row.get("loginid") or "")
        try:
            final_by_login[login] = int(row.get("rank"))
        except (TypeError, ValueError):
            continue

    swiss_by_login: dict[str, int] = {}
    for row in raw.get("standings") or []:
        login = str(row.get("loginid") or "")
        try:
            swiss_by_login[login] = int(row.get("rank"))
        except (TypeError, ValueError):
            continue

    wl_by_login: dict[str, tuple[int, int]] = {}
    for row in raw.get("winloss") or []:
        login = str(row.get("loginid") or "")
        try:
            wl_by_login[login] = (int(row.get("wins") or 0), int(row.get("losses") or 0))
        except (TypeError, ValueError):
            continue

    decks: list[ParsedDeck] = []
    for idx, d in enumerate(raw.get("decklists") or []):
        deck_id = str(d.get("decktournamentid") or "").strip()
        if not deck_id:
            continue
        loginid = str(d.get("loginid") or "")
        cards = _card_entries(d.get("main_deck"), "main") + _card_entries(d.get("sideboard_deck"), "side")
        sw, sl = wl_by_login.get(loginid, (None, None))
        decks.append(
            ParsedDeck(
                deck_id=deck_id,
                player=str(d.get("player") or ""),
                loginid=loginid,
                finish_rank=final_by_login.get(loginid),
                swiss_rank=swiss_by_login.get(loginid),
                deck_index=idx,
                swiss_wins=sw,
                swiss_losses=sl,
                cards=cards,
            )
        )

    matches: list[ParsedMatch] = []
    for rnd in raw.get("brackets") or []:
        try:
            idx = int(rnd.get("index"))
        except (TypeError, ValueError):
            idx = -1
        label = ROUND_NAMES.get(idx, f"round-{idx}")
        for match in rnd.get("matches") or []:
            players = match.get("players") or []
            if len(players) < 2:
                continue
            a, b = players[0], players[1]
            winner = ""
            if a.get("winner"):
                winner = str(a.get("player") or "")
            elif b.get("winner"):
                winner = str(b.get("player") or "")
            matches.append(
                ParsedMatch(
                    round_index=idx,
                    round_label=label,
                    player_a=str(a.get("player") or ""),
                    player_b=str(b.get("player") or ""),
                    wins_a=int(a.get("wins") or 0),
                    wins_b=int(b.get("wins") or 0),
                    winner=winner,
                )
            )

    return ParsedEvent(
        event_id=event_id,
        name=name,
        start_time=str(raw.get("starttime") or ""),
        event_type=et,
        player_count=_player_count(raw),
        source_url=url,
        decks=decks,
        matches=matches,
        raw=raw,
    )
