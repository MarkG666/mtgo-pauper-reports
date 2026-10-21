"""Parse MTGO decklist listing HTML and embedded window.MTGO.decklists.data."""

from __future__ import annotations

import json
import logging
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

logger = logging.getLogger(__name__)


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


def _login_rank_map(rows: list[dict[str, Any]] | None, rank_key: str = "rank") -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows or []:
        login = str(row.get("loginid") or "")
        if not login:
            continue
        try:
            out[login] = int(row.get(rank_key))
        except (TypeError, ValueError):
            continue
    return out


def derive_bracket_top8_ranks(
    brackets: list[dict[str, Any]] | None,
    *,
    player_to_login: dict[str, str],
    swiss_by_login: dict[str, int],
) -> dict[str, int]:
    """Map loginid → finish 1–8 from Top8 SE bracket.

    Within the same elimination round (SF losers → 3–4, QF losers → 5–8),
    order by swiss rank ascending (better swiss = better finish), matching
    typical MTGO ``final_rank`` / MTGGoldfish ordering.
    """
    final_w: str | None = None
    final_l: str | None = None
    sf_losers: list[str] = []
    qf_losers: list[str] = []

    for rnd in brackets or []:
        try:
            idx = int(rnd.get("index"))
        except (TypeError, ValueError):
            continue
        for match in rnd.get("matches") or []:
            players = match.get("players") or []
            if len(players) < 2:
                continue
            a, b = players[0], players[1]
            if a.get("winner"):
                winner, loser = a, b
            elif b.get("winner"):
                winner, loser = b, a
            else:
                continue
            wp = str(winner.get("player") or "")
            lp = str(loser.get("player") or "")
            if not wp or not lp:
                continue
            if idx == 0:
                final_w, final_l = wp, lp
            elif idx == 1:
                sf_losers.append(lp)
            elif idx == 2:
                qf_losers.append(lp)

    def swiss_key(player: str) -> int:
        lid = player_to_login.get(player, "")
        return swiss_by_login.get(lid, 999)

    out: dict[str, int] = {}

    def set_rank(player: str | None, rank: int) -> None:
        if not player:
            return
        lid = player_to_login.get(player)
        if lid:
            out[lid] = rank

    set_rank(final_w, 1)
    set_rank(final_l, 2)
    for i, player in enumerate(sorted(sf_losers, key=swiss_key)):
        set_rank(player, 3 + i)
    for i, player in enumerate(sorted(qf_losers, key=swiss_key)):
        set_rank(player, 5 + i)
    return out


def resolve_finish_ranks(
    *,
    brackets: list[dict[str, Any]] | None,
    final_by_login: dict[str, int],
    swiss_by_login: dict[str, int],
    player_to_login: dict[str, str],
) -> dict[str, int]:
    """Challenge Top8: bracket results for 1–8; swiss (or final_rank) for 9+.

    When ``final_rank`` is missing at ingest time (common while ``inplayoffs``),
    falling back to swiss alone mis-labels the champion — so brackets win for 1–8.
    """
    bracket_top8 = derive_bracket_top8_ranks(
        brackets,
        player_to_login=player_to_login,
        swiss_by_login=swiss_by_login,
    )

    finish: dict[str, int] = {}
    if bracket_top8:
        finish.update(bracket_top8)
        top8_logins = set(bracket_top8)
        for login, swiss in swiss_by_login.items():
            if login in top8_logins:
                continue
            fr = final_by_login.get(login)
            # Prefer published final_rank for non-Top8 when it isn't a Top8 slot.
            if fr is not None and fr > 8:
                finish[login] = fr
            else:
                finish[login] = swiss
        for login, fr in final_by_login.items():
            if login not in finish and fr > 8:
                finish[login] = fr
        return finish

    if final_by_login:
        return dict(final_by_login)

    return dict(swiss_by_login)


def validate_finish_vs_bracket(parsed: ParsedEvent) -> list[str]:
    """Cheap in-memory check: finalist/champion must match finish_rank 1–2.

    Runs on already-parsed decks/matches — no extra HTTP.
    """
    if parsed.event_type != "challenge":
        return []
    finals = [m for m in parsed.matches if m.round_index == 0 and m.winner]
    if not finals:
        return []
    final = finals[0]
    champ = final.winner
    runner = final.player_b if final.player_a == champ else final.player_a
    by_player = {d.player: d for d in parsed.decks if d.player}
    warnings: list[str] = []
    c = by_player.get(champ)
    if c is None:
        warnings.append(f"bracket champion {champ!r} has no deck row")
    elif c.finish_rank != 1:
        warnings.append(
            f"bracket champion {champ!r} finish_rank={c.finish_rank} expected 1"
        )
    r = by_player.get(runner)
    if runner and r is None:
        warnings.append(f"bracket finalist {runner!r} has no deck row")
    elif runner and r is not None and r.finish_rank != 2:
        warnings.append(
            f"bracket finalist {runner!r} finish_rank={r.finish_rank} expected 2"
        )
    return warnings


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

    final_by_login = _login_rank_map(raw.get("final_rank"))
    swiss_by_login = _login_rank_map(raw.get("standings"))

    wl_by_login: dict[str, tuple[int, int]] = {}
    for row in raw.get("winloss") or []:
        login = str(row.get("loginid") or "")
        try:
            wl_by_login[login] = (int(row.get("wins") or 0), int(row.get("losses") or 0))
        except (TypeError, ValueError):
            continue

    player_to_login: dict[str, str] = {}
    deck_meta: list[tuple[str, str, str, int, list[ParsedCard]]] = []
    for idx, d in enumerate(raw.get("decklists") or []):
        deck_id = str(d.get("decktournamentid") or "").strip()
        if not deck_id:
            continue
        loginid = str(d.get("loginid") or "")
        player = str(d.get("player") or "")
        if player and loginid:
            player_to_login[player] = loginid
        cards = _card_entries(d.get("main_deck"), "main") + _card_entries(
            d.get("sideboard_deck"), "side"
        )
        deck_meta.append((deck_id, player, loginid, idx, cards))

    finish_by_login = resolve_finish_ranks(
        brackets=raw.get("brackets"),
        final_by_login=final_by_login,
        swiss_by_login=swiss_by_login,
        player_to_login=player_to_login,
    )

    decks: list[ParsedDeck] = []
    for deck_id, player, loginid, idx, cards in deck_meta:
        sw, sl = wl_by_login.get(loginid, (None, None))
        decks.append(
            ParsedDeck(
                deck_id=deck_id,
                player=player,
                loginid=loginid,
                finish_rank=finish_by_login.get(loginid),
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

    parsed = ParsedEvent(
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
    for msg in validate_finish_vs_bracket(parsed):
        logger.warning("event %s rank validation: %s", event_id, msg)
    return parsed
