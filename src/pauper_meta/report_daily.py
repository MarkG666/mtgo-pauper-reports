"""Daily Japanese Markdown report — structured data first (Discord-ready)."""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import json

from .colors import color_identity_from_cards, display_archetype
from .distinctive import (
    DistinctiveFlag,
    build_historical_prototypes,
    load_distinctive_config,
    select_distinctive,
)
from .goldfish_links import get_resolver
from .parse_mtgo import mtgo_player_deck_url
from .paths import CONFIG_DIR, REPORTS_DIR, ensure_dirs

JST = ZoneInfo("Asia/Tokyo")

DEFAULT_REPORT_CONFIG = {
    "other_share_threshold": 0.02,
}


def load_report_config(path: Path | None = None) -> dict:
    ensure_dirs()
    cfg_path = path or (CONFIG_DIR / "report.json")
    out = dict(DEFAULT_REPORT_CONFIG)
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            if "other_share_threshold" in data:
                out["other_share_threshold"] = float(data["other_share_threshold"])
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return out


@dataclass
class ArchCount:
    archetype: str
    count: int
    share: float
    # Match W-L: swiss standings + Top8 single-elim bracket (not game W-L)
    match_wins: int = 0
    match_losses: int = 0
    winrate: float | None = None  # W / (W+L), None if no matches
    top8_count: int = 0  # finish_rank <= 8 within cohort
    share_delta: float | None = None  # week share − prior week share (raw)


@dataclass
class Top8Entry:
    place_label: str
    finish_rank: int
    player: str
    archetype: str
    eliminated_by: str = "—"
    # Per-deck MTGO deep link (event URL + #deck_<player>); None if unavailable
    deck_url: str | None = None


@dataclass
class BracketMatchLine:
    round_label: str
    player_a: str
    arch_a: str
    player_b: str
    arch_b: str
    wins_a: int
    wins_b: int
    winner: str


@dataclass
class EventSummary:
    event_id: str
    name: str
    event_type: str
    start_time: str
    player_count: int | None
    source_url: str
    deck_count: int
    top32: list[ArchCount] = field(default_factory=list)
    top8_bracket: list[Top8Entry] = field(default_factory=list)
    bracket_matches: list[BracketMatchLine] = field(default_factory=list)


@dataclass
class TrendDelta:
    archetype: str
    current: int
    previous: int
    delta: int


@dataclass
class DailyReportData:
    report_date: str
    generated_at: str
    events: list[EventSummary]
    meta_overview: list[ArchCount]
    month_dist: list[ArchCount]  # ~30d Top32 aggregate (Other-bucketed)
    week_dist: list[ArchCount]  # last 7d Top32 aggregate (Other-bucketed + 前週差)
    distinctive: list[DistinctiveFlag]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def today_jst(now: datetime | None = None) -> date:
    now = now or datetime.now(tz=JST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc).astimezone(JST)
    else:
        now = now.astimezone(JST)
    return now.date()


def _parse_event_start_utc(start_time: str | None) -> datetime | None:
    """Parse MTGO ``start_time`` as UTC (DB stores naive UTC wall clock)."""
    if not start_time:
        return None
    text = start_time.strip()
    if not text:
        return None
    # Common shapes: ``2026-10-22 19:00:00.0``, ``2026-10-22T19:00:00``, with/without Z
    text = text.replace("Z", "").replace("z", "")
    if "T" not in text and " " in text:
        text = text.replace(" ", "T", 1)
    # Drop fractional seconds that fromisoformat may not like uniformly
    if "." in text:
        text = text.split(".", 1)[0]
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text[:10]).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _parse_event_date(start_time: str | None) -> date | None:
    """JST calendar date of event start — the report day for this event.

    MTGO ``start_time`` is UTC. A Friday 04:00 JST Challenge is stored as
    Thursday 19:00 UTC; report day must be Friday (JST), not Thursday (UTC).
    """
    dt = _parse_event_start_utc(start_time)
    if dt is None:
        return None
    return dt.astimezone(JST).date()


def _place_label(rank: int) -> str:
    if rank == 1:
        return "優勝"
    if rank == 2:
        return "準優勝"
    if rank <= 4:
        return "Top4"
    if rank <= 8:
        return "Top8"
    return f"#{rank}"


def _deck_display(conn: sqlite3.Connection, deck_row: sqlite3.Row) -> str:
    arch = deck_row["archetype_final"] or "Unknown"
    try:
        cid = deck_row["color_identity"]
    except (IndexError, KeyError):
        cid = None
    if not cid:
        cards = conn.execute(
            "SELECT board, colors, card_type FROM cards WHERE deck_id = ?",
            (deck_row["id"],),
        ).fetchall()
        cid = color_identity_from_cards(
            [
                {
                    "board": r["board"],
                    "colors": (r["colors"] or "").split(",") if r["colors"] else [],
                    "card_type": r["card_type"],
                }
                for r in cards
            ]
        )
    return display_archetype(arch, cid)


def _player_arch_map(conn: sqlite3.Connection, event_id: str) -> dict[str, str]:
    return {
        (r["player"] or ""): _deck_display(conn, r)
        for r in conn.execute("SELECT * FROM decks WHERE event_id = ?", (event_id,)).fetchall()
    }


def _top32_deck_rows(conn: sqlite3.Connection, event_id: str) -> list[sqlite3.Row]:
    """Top32 meta cohort for one event.

    Use ``COALESCE(finish_rank, swiss_rank) <= 32``. An early ``finish_rank IS NOT
    NULL AND finish_rank <= 32`` query wrongly stopped at Top8 whenever only SE
    places 1–8 were filled (9–32 still NULL) — the swiss fallback never ran.
    """
    rows = conn.execute(
        """
        SELECT * FROM decks
        WHERE event_id = ?
          AND COALESCE(finish_rank, swiss_rank) IS NOT NULL
          AND COALESCE(finish_rank, swiss_rank) <= 32
        ORDER BY COALESCE(finish_rank, swiss_rank) ASC, player
        """,
        (event_id,),
    ).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT * FROM decks WHERE event_id = ? ORDER BY player",
            (event_id,),
        ).fetchall()
    return list(rows)


def _bracket_match_wl_by_player(
    conn: sqlite3.Connection, event_id: str
) -> dict[str, tuple[int, int]]:
    """Top8 single-elim: match wins/losses per player (winner column)."""
    wins: Counter[str] = Counter()
    losses: Counter[str] = Counter()
    for m in conn.execute(
        "SELECT player_a, player_b, winner FROM matches WHERE event_id = ?",
        (event_id,),
    ).fetchall():
        a, b, w = m["player_a"] or "", m["player_b"] or "", m["winner"] or ""
        if not w or not a or not b:
            continue
        if w == a:
            wins[a] += 1
            losses[b] += 1
        elif w == b:
            wins[b] += 1
            losses[a] += 1
    players = set(wins) | set(losses)
    return {p: (wins[p], losses[p]) for p in players}


def _bracket_top8_by_player(conn: sqlite3.Connection, event_id: str) -> dict[str, int]:
    """Derive player → finish 1–8 from stored matches + swiss (no HTTP)."""
    decks = conn.execute(
        "SELECT player, loginid, swiss_rank FROM decks WHERE event_id = ?",
        (event_id,),
    ).fetchall()
    player_to_login = {
        (r["player"] or ""): (r["loginid"] or "") for r in decks if r["player"]
    }
    swiss_by_login = {
        (r["loginid"] or ""): int(r["swiss_rank"])
        for r in decks
        if r["loginid"] and r["swiss_rank"] is not None
    }
    # Rebuild minimal bracket payload from matches table
    by_round: dict[int, list[dict]] = defaultdict(list)
    for m in conn.execute(
        """
        SELECT round_index, player_a, player_b, wins_a, wins_b, winner
        FROM matches WHERE event_id = ?
        """,
        (event_id,),
    ).fetchall():
        idx = int(m["round_index"] if m["round_index"] is not None else -1)
        a, b = m["player_a"] or "", m["player_b"] or ""
        w = m["winner"] or ""
        by_round[idx].append(
            {
                "players": [
                    {
                        "player": a,
                        "wins": int(m["wins_a"] or 0),
                        "winner": bool(w and w == a),
                    },
                    {
                        "player": b,
                        "wins": int(m["wins_b"] or 0),
                        "winner": bool(w and w == b),
                    },
                ]
            }
        )
    if not by_round:
        return {}
    from .parse_mtgo import derive_bracket_top8_ranks

    brackets = [{"index": idx, "matches": matches} for idx, matches in by_round.items()]
    by_login = derive_bracket_top8_ranks(
        brackets,
        player_to_login=player_to_login,
        swiss_by_login=swiss_by_login,
    )
    login_to_player = {lid: p for p, lid in player_to_login.items() if lid}
    return {
        login_to_player[lid]: rank
        for lid, rank in by_login.items()
        if lid in login_to_player
    }


def backfill_finish_ranks_from_brackets(
    conn: sqlite3.Connection, event_id: str
) -> int:
    """If Challenge has bracket matches but null finish_rank, fill from brackets.

    Returns number of decks updated. Cheap — uses rows already in SQLite.
    """
    has_matches = conn.execute(
        "SELECT 1 FROM matches WHERE event_id = ? LIMIT 1", (event_id,)
    ).fetchone()
    if not has_matches:
        return 0
    null_fr = conn.execute(
        """
        SELECT COUNT(*) AS c FROM decks
        WHERE event_id = ? AND finish_rank IS NULL
        """,
        (event_id,),
    ).fetchone()["c"]
    if not null_fr:
        return 0

    by_player = _bracket_top8_by_player(conn, event_id)
    if not by_player:
        return 0

    updated = 0
    for row in conn.execute(
        "SELECT id, player, swiss_rank, finish_rank FROM decks WHERE event_id = ?",
        (event_id,),
    ).fetchall():
        player = row["player"] or ""
        if player in by_player:
            new_rank = by_player[player]
        elif (
            row["finish_rank"] is None
            and row["swiss_rank"] is not None
            and int(row["swiss_rank"]) > 8
        ):
            # Places 9+: swiss standings (never use swiss 1–8 as finish without bracket)
            new_rank = int(row["swiss_rank"])
        else:
            continue
        if row["finish_rank"] == new_rank:
            continue
        conn.execute(
            "UPDATE decks SET finish_rank = ? WHERE id = ?",
            (new_rank, row["id"]),
        )
        updated += 1
    return updated


def _winrate(wins: int, losses: int) -> float | None:
    total = wins + losses
    if total <= 0:
        return None
    return round(wins / total, 4)


def _arch_strength_from_decks(
    conn: sqlite3.Connection, decks: list[sqlite3.Row]
) -> list[ArchCount]:
    """Aggregate Top32 cohort: count/share + match W-L (swiss + SE) + winrate."""
    by_arch: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for d in decks:
        by_arch[_deck_display(conn, d)].append(d)

    bracket_cache: dict[str, dict[str, tuple[int, int]]] = {}
    for d in decks:
        eid = d["event_id"]
        if eid not in bracket_cache:
            bracket_cache[eid] = _bracket_match_wl_by_player(conn, eid)

    total = sum(len(v) for v in by_arch.values()) or 1
    out: list[ArchCount] = []
    for arch, group in by_arch.items():
        mw = 0
        ml = 0
        top8 = 0
        for g in group:
            mw += int(g["swiss_wins"] or 0)
            ml += int(g["swiss_losses"] or 0)
            bw, bl = bracket_cache.get(g["event_id"], {}).get(g["player"] or "", (0, 0))
            mw += bw
            ml += bl
            fr = g["finish_rank"]
            if fr is not None and int(fr) <= 8:
                top8 += 1
            elif fr is None:
                # Legacy rows: count bracket participants when finish_rank was never set
                if (g["player"] or "") in bracket_cache.get(g["event_id"], {}):
                    top8 += 1
        out.append(
            ArchCount(
                archetype=arch,
                count=len(group),
                share=round(len(group) / total, 4),
                match_wins=mw,
                match_losses=ml,
                winrate=_winrate(mw, ml),
                top8_count=top8,
            )
        )
    out.sort(key=lambda a: (-a.count, -(a.winrate or -1), a.archetype))
    return out


def _merge_arch_counts(parts: list[list[ArchCount]]) -> list[ArchCount]:
    agg: dict[str, dict] = {}
    for lst in parts:
        for a in lst:
            slot = agg.setdefault(
                a.archetype, {"count": 0, "mw": 0, "ml": 0, "top8": 0}
            )
            slot["count"] += a.count
            slot["mw"] += a.match_wins
            slot["ml"] += a.match_losses
            slot["top8"] += a.top8_count
    total = sum(v["count"] for v in agg.values()) or 1
    out: list[ArchCount] = []
    for arch, v in agg.items():
        out.append(
            ArchCount(
                archetype=arch,
                count=v["count"],
                share=round(v["count"] / total, 4),
                match_wins=v["mw"],
                match_losses=v["ml"],
                winrate=_winrate(v["mw"], v["ml"]),
                top8_count=v["top8"],
            )
        )
    out.sort(key=lambda a: (-a.count, -(a.winrate or -1), a.archetype))
    return out


def _bucket_other(rows: list[ArchCount], threshold: float) -> list[ArchCount]:
    """Merge archetypes with share <= threshold into a single Other row."""
    if not rows:
        return []
    keep: list[ArchCount] = []
    other_n = other_mw = other_ml = other_t8 = 0
    for a in rows:
        if a.archetype == "Other" or a.share <= threshold:
            other_n += a.count
            other_mw += a.match_wins
            other_ml += a.match_losses
            other_t8 += a.top8_count
        else:
            keep.append(a)
    total = sum(a.count for a in keep) + other_n or 1
    out: list[ArchCount] = [
        ArchCount(
            archetype=a.archetype,
            count=a.count,
            share=round(a.count / total, 4),
            match_wins=a.match_wins,
            match_losses=a.match_losses,
            winrate=_winrate(a.match_wins, a.match_losses),
            top8_count=a.top8_count,
        )
        for a in keep
    ]
    if other_n:
        out.append(
            ArchCount(
                archetype="Other",
                count=other_n,
                share=round(other_n / total, 4),
                match_wins=other_mw,
                match_losses=other_ml,
                winrate=_winrate(other_mw, other_ml),
                top8_count=other_t8,
            )
        )
    out.sort(
        key=lambda a: (
            a.archetype == "Other",
            -a.count,
            -(a.winrate or -1),
            a.archetype,
        )
    )
    return out


def _with_share_deltas(
    current: list[ArchCount],
    previous_raw: list[ArchCount],
    *,
    threshold: float,
) -> list[ArchCount]:
    """Attach share_delta: named arches use prior raw share; Other uses prior bucketed Other."""
    prev_raw = {a.archetype: a.share for a in previous_raw}
    prev_bucketed = _bucket_other(previous_raw, threshold)
    prev_other = next((a.share for a in prev_bucketed if a.archetype == "Other"), None)
    out: list[ArchCount] = []
    for a in current:
        if a.archetype == "Other":
            delta = None if prev_other is None else round(a.share - prev_other, 4)
        elif a.archetype in prev_raw:
            delta = round(a.share - prev_raw[a.archetype], 4)
        else:
            delta = None
        out.append(
            ArchCount(
                archetype=a.archetype,
                count=a.count,
                share=a.share,
                match_wins=a.match_wins,
                match_losses=a.match_losses,
                winrate=a.winrate,
                top8_count=a.top8_count,
                share_delta=delta,
            )
        )
    return out


def _fmt_share_delta(delta: float | None) -> str:
    if delta is None:
        return "—"
    pct = int(round(delta * 100))
    if pct == 0:
        return "-"
    return f"+{pct}%" if pct > 0 else f"{pct}%"


def _eliminators(conn: sqlite3.Connection, event_id: str) -> dict[str, str]:
    arch = _player_arch_map(conn, event_id)
    out: dict[str, str] = {}
    for m in conn.execute(
        "SELECT player_a, player_b, winner FROM matches WHERE event_id = ? ORDER BY round_index ASC",
        (event_id,),
    ).fetchall():
        a, b, w = m["player_a"], m["player_b"], m["winner"]
        if not w:
            continue
        loser = b if w == a else a if w == b else None
        if not loser:
            continue
        out[loser] = f"{w}（{arch.get(w, '?')}）"
    return out


def _bracket_matches(conn: sqlite3.Connection, event_id: str) -> list[BracketMatchLine]:
    arch = _player_arch_map(conn, event_id)
    rows = conn.execute(
        """
        SELECT round_index, round_label, player_a, player_b, wins_a, wins_b, winner
        FROM matches WHERE event_id = ?
        ORDER BY round_index DESC, id
        """,
        (event_id,),
    ).fetchall()
    return [
        BracketMatchLine(
            round_label=m["round_label"] or f"round-{m['round_index']}",
            player_a=m["player_a"] or "",
            arch_a=arch.get(m["player_a"] or "", "?"),
            player_b=m["player_b"] or "",
            arch_b=arch.get(m["player_b"] or "", "?"),
            wins_a=int(m["wins_a"] or 0),
            wins_b=int(m["wins_b"] or 0),
            winner=m["winner"] or "",
        )
        for m in rows
    ]


def _top8_bracket(conn: sqlite3.Connection, event_id: str) -> list[Top8Entry]:
    elim = _eliminators(conn, event_id)
    ev_row = conn.execute(
        "SELECT source_url FROM events WHERE id = ?",
        (event_id,),
    ).fetchone()
    event_url = (ev_row["source_url"] if ev_row else None) or ""
    rows = conn.execute(
        """
        SELECT * FROM decks
        WHERE event_id = ? AND finish_rank IS NOT NULL AND finish_rank <= 8
        ORDER BY finish_rank ASC, player
        """,
        (event_id,),
    ).fetchall()
    rank_by_player: dict[str, int] = {}
    if not rows:
        # Do NOT use swiss Top8 as stand-ins for 優勝/準優勝 — that disagreed with
        # MTGO brackets / Goldfish when final_rank was missing at ingest.
        rank_by_player = _bracket_top8_by_player(conn, event_id)
        if rank_by_player:
            placeholders = ",".join("?" * len(rank_by_player))
            rows = conn.execute(
                f"""
                SELECT * FROM decks
                WHERE event_id = ? AND player IN ({placeholders})
                """,
                (event_id, *rank_by_player.keys()),
            ).fetchall()
            rows = sorted(
                rows,
                key=lambda r: (rank_by_player.get(r["player"] or "", 99), r["player"] or ""),
            )
    out: list[Top8Entry] = []
    for r in rows:
        player = r["player"] or ""
        if rank_by_player:
            rank = rank_by_player[player]
        else:
            rank = int(r["finish_rank"])
        out.append(
            Top8Entry(
                place_label=_place_label(rank),
                finish_rank=rank,
                player=player,
                archetype=_deck_display(conn, r),
                eliminated_by="—" if rank == 1 else elim.get(player, "—"),
                deck_url=mtgo_player_deck_url(event_url, player),
            )
        )
    return out


def _events_on_date(conn: sqlite3.Connection, d: date) -> list[sqlite3.Row]:
    """Events whose **JST** start date equals ``d``.

    DB ``start_time`` is UTC text, so we pull a ±1 UTC-day window and filter
    by ``_parse_event_date`` (JST).
    """
    prev = (d - timedelta(days=1)).isoformat()
    cur = d.isoformat()
    nxt = (d + timedelta(days=1)).isoformat()
    rows = conn.execute(
        """
        SELECT * FROM events
        WHERE start_time LIKE ? OR start_time LIKE ? OR start_time LIKE ?
        ORDER BY start_time, name
        """,
        (prev + "%", cur + "%", nxt + "%"),
    ).fetchall()
    return [r for r in rows if _parse_event_date(r["start_time"]) == d]


def _events_for_report(conn: sqlite3.Connection, report_date: date) -> list[sqlite3.Row]:
    """Events that belong on ``report_date`` (JST). No carry-forward fallback.

    Empty days stay empty so Discord/catch-up do not invent a duplicate of
    another day's Challenge.
    """
    return list(_events_on_date(conn, report_date))


def _trend(current: Counter[str], previous: Counter[str]) -> list[TrendDelta]:
    """Count deltas; omit rows with zero change. Empty previous → empty list."""
    if not previous and not current:
        return []
    out: list[TrendDelta] = []
    for name in set(current) | set(previous):
        c, p = current.get(name, 0), previous.get(name, 0)
        if c == p:
            continue
        out.append(TrendDelta(name, c, p, c - p))
    out.sort(key=lambda t: (-abs(t.delta), t.archetype))
    return out


def _top32_strength_window(
    conn: sqlite3.Connection, start: date, end: date
) -> list[ArchCount]:
    """Aggregate Top32-per-event decks across events with **JST** start date in [start, end].

    DB ``start_time`` is UTC text; pad ±1 UTC day then filter via ``_parse_event_date``.
    """
    prev = (start - timedelta(days=1)).isoformat()
    nxt = (end + timedelta(days=1)).isoformat()
    rows = conn.execute(
        """
        SELECT id, start_time FROM events
        WHERE substr(start_time, 1, 10) BETWEEN ? AND ?
        ORDER BY start_time
        """,
        (prev, nxt),
    ).fetchall()
    event_ids = [
        r["id"]
        for r in rows
        if (d := _parse_event_date(r["start_time"])) is not None and start <= d <= end
    ]
    if not event_ids:
        return []
    parts = [
        _arch_strength_from_decks(conn, _top32_deck_rows(conn, eid)) for eid in event_ids
    ]
    return _merge_arch_counts(parts)


def _arch_counts_counter(rows: list[ArchCount]) -> Counter[str]:
    return Counter({a.archetype: a.count for a in rows})


def meta_top32_checksum(
    events: list[EventSummary],
    meta: list[ArchCount],
) -> list[str]:
    """Lightweight 検算 notes: Top8 column vs brackets; Top32 size vs field.

    Returns note strings (warnings prefixed with ``検算警告``). Safe to append
    to report ``## 注記``; also useful for tests / CI logging.
    """
    notes: list[str] = []
    bracket_n = sum(len(ev.top8_bracket) for ev in events)
    top8_col = sum(a.top8_count for a in meta)
    meta_total = sum(a.count for a in meta)

    if bracket_n != top8_col:
        notes.append(
            f"検算警告: メタ Top8 列の合計 ({top8_col}) が "
            f"Top8 ブラケット掲載数 ({bracket_n}) と一致しません。"
        )
    else:
        notes.append(
            f"検算: メタ Top8 列合計 = ブラケット掲載 {bracket_n} 件（一致）。"
        )

    # Per-event: cohort size should be ≤ published decks and ≤ 32
    for ev in events:
        cohort = sum(a.count for a in ev.top32)
        if cohort > ev.deck_count:
            notes.append(
                f"検算警告: event {ev.event_id} Top32 合算 {cohort} > "
                f"公開デッキ {ev.deck_count}。"
            )
        if cohort > 32:
            notes.append(
                f"検算警告: event {ev.event_id} Top32 合算 {cohort} > 32。"
            )
        # Challenge with full publish: expect min(32, deck_count) when ranks exist
        if ev.deck_count >= 16 and cohort == 8 and ev.top8_bracket:
            notes.append(
                f"検算警告: event {ev.event_id} は公開デッキ {ev.deck_count} だが "
                f"メタ cohort が 8（Top8 のみカウントの疑い）。"
            )

    field_cap = sum(min(32, ev.deck_count) for ev in events) if events else 0
    if events and meta_total > field_cap:
        notes.append(
            f"検算警告: メタ Top32 合計 {meta_total} が "
            f"当日フィールド上限 {field_cap}（各イベント min(32, 公開デッキ) 合算）を超えています。"
        )
    elif events:
        notes.append(
            f"検算: メタ Top32 合計 {meta_total} "
            f"（上限 {field_cap} = 各イベント min(32, 公開デッキ) 合算）。"
        )
    return notes


def log_meta_checksum(notes: list[str], *, logger: Any | None = None) -> None:
    """Emit checksum notes to the given logger (default: module-style print via logging)."""
    import logging

    log = logger or logging.getLogger(__name__)
    for n in notes:
        if n.startswith("検算警告"):
            log.warning("%s", n)
        else:
            log.info("%s", n)


def _load_deck_payloads(conn: sqlite3.Connection, event_ids: list[str]) -> list[dict]:
    if not event_ids:
        return []
    qmarks = ",".join("?" * len(event_ids))
    events = {
        r["id"]: r
        for r in conn.execute(f"SELECT * FROM events WHERE id IN ({qmarks})", event_ids).fetchall()
    }
    decks = conn.execute(
        f"SELECT * FROM decks WHERE event_id IN ({qmarks})",
        event_ids,
    ).fetchall()
    resolver = get_resolver()
    out: list[dict] = []
    for d in decks:
        cards = conn.execute(
            "SELECT name, qty, board FROM cards WHERE deck_id = ?",
            (d["id"],),
        ).fetchall()
        main_cards = [(c["name"], int(c["qty"])) for c in cards if c["board"] == "main"]
        side_cards = [(c["name"], int(c["qty"])) for c in cards if c["board"] == "side"]
        # None would mean "unknown / missing ingest"; empty list means known empty.
        # We always have side rows when MTGO published sideboards (typical).
        has_any_card_rows = bool(cards)
        ev = events[d["event_id"]]
        event_date = (ev["start_time"] or "")[:10]
        url, source = resolver.resolve_deck_link(
            event_name=ev["name"] or "",
            event_date=event_date,
            player=d["player"] or "",
            mtgo_event_url=ev["source_url"],
        )
        out.append(
            {
                "deck_id": d["id"],
                "player": d["player"],
                "event_id": d["event_id"],
                "event_name": ev["name"],
                "archetype": _deck_display(conn, d),
                "finish_rank": d["finish_rank"],
                "main_cards": main_cards,
                "side_cards": side_cards if has_any_card_rows else None,
                "deck_url": url,
                "link_source": source,
            }
        )
    return out


def build_report_data(
    conn: sqlite3.Connection,
    report_date: date | None = None,
    now: datetime | None = None,
) -> DailyReportData:
    rd = report_date or today_jst(now)
    notes: list[str] = []
    events_rows = _events_for_report(conn, rd)
    if not events_rows:
        notes.append("対象日の Challenge / Preliminary は見つかりませんでした（新規なし）。")
    else:
        first_date = _parse_event_date(events_rows[0]["start_time"])
        if first_date and first_date != rd:
            notes.append(
                f"レポート日 {rd.isoformat()} に開始のイベントが無いため、"
                f"直近の {first_date.isoformat()} 開催分を掲載。"
            )

    from .overrides import recompute_top8_dist_for_event

    event_summaries: list[EventSummary] = []
    for ev in events_rows:
        n_fix = backfill_finish_ranks_from_brackets(conn, ev["id"])
        if n_fix:
            notes.append(
                f"event {ev['id']}: finish_rank をブラケットから {n_fix} 件補完"
            )
        recompute_top8_dist_for_event(conn, ev["id"])
        deck_count = conn.execute(
            "SELECT COUNT(*) AS c FROM decks WHERE event_id = ?",
            (ev["id"],),
        ).fetchone()["c"]
        top32_decks = _top32_deck_rows(conn, ev["id"])
        top32 = _arch_strength_from_decks(conn, top32_decks)
        event_summaries.append(
            EventSummary(
                event_id=ev["id"],
                name=ev["name"],
                event_type=ev["event_type"],
                start_time=ev["start_time"] or "",
                player_count=ev["player_count"],
                source_url=ev["source_url"] or "",
                deck_count=int(deck_count),
                top32=top32,
                top8_bracket=_top8_bracket(conn, ev["id"]),
                bracket_matches=_bracket_matches(conn, ev["id"]),
            )
        )

    event_ids = [e.event_id for e in event_summaries]
    meta = _merge_arch_counts([e.top32 for e in event_summaries])
    checksum_notes = meta_top32_checksum(event_summaries, meta)
    log_meta_checksum(checksum_notes)
    notes.extend(checksum_notes)

    # Window end = report event date when present, else report date (JST calendar)
    as_of = rd
    if events_rows:
        ed = _parse_event_date(events_rows[0]["start_time"])
        if ed:
            as_of = ed

    month_start = as_of - timedelta(days=29)  # 30 days inclusive
    week_start = as_of - timedelta(days=6)  # 7 days inclusive
    prev_week_end = week_start - timedelta(days=1)
    prev_week_start = prev_week_end - timedelta(days=6)

    report_cfg = load_report_config()
    other_thr = float(report_cfg["other_share_threshold"])

    month_raw = _top32_strength_window(conn, month_start, as_of)
    week_raw = _top32_strength_window(conn, week_start, as_of)
    prev_week_raw = _top32_strength_window(conn, prev_week_start, prev_week_end)
    month_dist = _bucket_other(month_raw, other_thr)
    week_dist = _with_share_deltas(
        _bucket_other(week_raw, other_thr),
        prev_week_raw,
        threshold=other_thr,
    )

    # Distinctive: historical ~14d prototype
    cfg = load_distinctive_config()

    def _display_row(r: sqlite3.Row) -> str:
        return _deck_display(conn, r)

    main_protos, side_protos, base_notes = build_historical_prototypes(
        conn,
        as_of=as_of,
        window_days=int(cfg["window_days"]),
        widen_days=int(cfg["widen_days"]),
        min_lists=int(cfg["min_archetype_lists"]),
        display_fn=_display_row,
    )
    notes.append(
        "特徴リストの同アーキ比較は直近約2週間の**メイン／サイド別**平均"
        "（不足時さらに拡張。スコアはメイン主＋サイド0.55倍）。"
        "比較基準は `直近2週間の同アーキ N本`（メイン比較に使った本数）。"
    )
    notes.append(
        "**W-L / 勝率**はマッチ単位（ゲームではない）。"
        "スイス順位表の W-L に Top8 シングルエリムのブラケット勝敗を合算。"
        "勝率 = W / (W+L)。**Top8** 列は当該アーキの finish_rank≤8 本数。"
    )
    notes.append(
        "累積分布（1ヵ月／1週間）は各イベントの **Top32** デッキ合算。"
        f"シェア≤{other_thr:.0%} は **Other** に合算（`config/report.json`）。"
        "直近1週間の**前週差**はシェア差（前週に無いアーキは —）。"
    )
    notes.append(
        "未分類デッキはアーキ名の代わりに **色アイデンティティ**"
        "（W/U/B/R/G、複数色は WUBRG 順、無色は C）を表示。"
    )
    notes.append(
        "Top8 のプレイヤー／アーキタイプは **mtgo.com の個別デッキアンカー**"
        "（`#deck_<player>`、空白は `_`）へリンク。"
        "特徴リストのリンク方針（MTGGoldfish 優先）は変更なし。"
    )

    distinctive = select_distinctive(
        _load_deck_payloads(conn, event_ids),
        main_prototypes=main_protos,
        side_prototypes=side_protos,
        baseline_notes=base_notes,
        min_count=3,
        max_count=5,
        max_finish_rank=16,
    )
    if distinctive and all(d.link_source != "goldfish-deck" for d in distinctive):
        notes.append(
            "特徴リストのリンクは MTGGoldfish 個別デッキが取れない場合、"
            "大会ページまたは mtgo.com にフォールバックします（CF回避はしません）。"
        )

    return DailyReportData(
        report_date=rd.isoformat(),
        generated_at=(now or datetime.now(tz=JST)).astimezone(JST).isoformat(timespec="seconds"),
        events=event_summaries,
        meta_overview=meta,
        month_dist=month_dist,
        week_dist=week_dist,
        distinctive=distinctive,
        notes=notes,
    )


def _render_one_diff_table(diffs: list, title: str) -> list[str]:
    added = [d for d in diffs if d.side == "+"]
    removed = [d for d in diffs if d.side == "-"]
    lines = [f"**{title}**", ""]
    if not added and not removed:
        lines.append("差分なし。")
        lines.append("")
        return lines
    n = max(len(added), len(removed))
    lines.extend(
        [
            "| +カード | 枚数 | -カード | 枚数 |",
            "|---|---:|---|---:|",
        ]
    )
    for i in range(n):
        left_card = added[i].name if i < len(added) else ""
        left_qty = f"+{added[i].qty}" if i < len(added) else ""
        right_card = removed[i].name if i < len(removed) else ""
        right_qty = f"-{removed[i].qty}" if i < len(removed) else ""
        lines.append(f"| {left_card} | {left_qty} | {right_card} | {right_qty} |")
    lines.append("")
    return lines


def _render_diff_table(flag: DistinctiveFlag) -> list[str]:
    """Two sections: メイン / サイド (side-by-side +/- each)."""
    lines: list[str] = []
    lines.extend(_render_one_diff_table(flag.main_diffs, "メイン"))
    if flag.side_skipped_reason:
        lines.append(f"**サイド** — {flag.side_skipped_reason}")
        lines.append("")
    else:
        lines.extend(_render_one_diff_table(flag.side_diffs, "サイド"))
    return lines


def _render_meta_table(rows: list[ArchCount]) -> list[str]:
    lines = [
        "| アーキタイプ | 数 | Top8 | シェア | W-L | 勝率 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for a in rows:
        wl = f"{a.match_wins}-{a.match_losses}"
        wr = f"{a.winrate:.0%}" if a.winrate is not None else "—"
        lines.append(
            f"| {a.archetype} | {a.count} | {a.top8_count} | {a.share:.0%} | {wl} | {wr} |"
        )
    return lines


def _render_cumul_table(rows: list[ArchCount], *, with_week_delta: bool = False) -> list[str]:
    if with_week_delta:
        lines = [
            "| アーキタイプ | 数 | シェア | 前週差 | W-L | 勝率 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for a in rows:
            wl = f"{a.match_wins}-{a.match_losses}"
            wr = f"{a.winrate:.0%}" if a.winrate is not None else "—"
            lines.append(
                f"| {a.archetype} | {a.count} | {a.share:.0%} | "
                f"{_fmt_share_delta(a.share_delta)} | {wl} | {wr} |"
            )
        return lines
    lines = [
        "| アーキタイプ | 数 | シェア | W-L | 勝率 |",
        "|---|---:|---:|---:|---:|",
    ]
    for a in rows:
        wl = f"{a.match_wins}-{a.match_losses}"
        wr = f"{a.winrate:.0%}" if a.winrate is not None else "—"
        lines.append(f"| {a.archetype} | {a.count} | {a.share:.0%} | {wl} | {wr} |")
    return lines


def render_markdown(data: DailyReportData) -> str:
    lines: list[str] = []
    lines.append(f"# Pauper 日次レポート — {data.report_date}（JST）")
    lines.append("")
    lines.append(f"生成: `{data.generated_at}`")
    lines.append("")
    lines.append("対象: **Pauper Challenge** および **Preliminary**（League 除外）")
    lines.append("")

    lines.append("## イベント概要")
    if not data.events:
        lines.append("新規イベントなし。")
        lines.append("")
    else:
        for ev in data.events:
            pc = ev.player_count if ev.player_count is not None else "?"
            lines.append(
                f"- **{ev.name}** (`{ev.event_type}`) — 開始 `{ev.start_time}` / "
                f"人数 {pc} / 公開デッキ {ev.deck_count} / [mtgo]({ev.source_url})"
            )
        lines.append("")

    for ev in data.events:
        lines.append(f"## Top8 ブラケット — {ev.name}")
        if not ev.top8_bracket:
            lines.append("ブラケット／最終順位データなし。")
            lines.append("")
            continue
        lines.append("| 結果 | プレイヤー | アーキタイプ | 敗因 |")
        lines.append("|---|---|---|---|")
        for e in ev.top8_bracket:
            if e.deck_url:
                player_cell = f"[{e.player}]({e.deck_url})"
                arch_cell = f"[{e.archetype}]({e.deck_url})"
            else:
                player_cell = e.player
                arch_cell = e.archetype
            lines.append(
                f"| {e.place_label} | {player_cell} | {arch_cell} | {e.eliminated_by} |"
            )
        lines.append("")

    lines.append("## メタ概要（Top32 合算）")
    if not data.meta_overview:
        lines.append("データなし。")
    else:
        lines.extend(_render_meta_table(data.meta_overview))
    lines.append("")

    lines.append("## 過去1ヵ月累積のアーキタイプ分布")
    if not data.month_dist:
        lines.append("データなし。")
    else:
        lines.extend(_render_cumul_table(data.month_dist))
    lines.append("")

    lines.append("## 直近1週間累積のアーキタイプ分布")
    if not data.week_dist:
        lines.append("データなし。")
    else:
        lines.extend(_render_cumul_table(data.week_dist, with_week_delta=True))
    lines.append("")

    lines.append("## 特徴リスト（Top16 のみ・3〜5本）")
    if not data.distinctive:
        lines.append("該当なし（節は維持）。")
    else:
        for i, d in enumerate(data.distinctive, 1):
            rank = f"#{d.finish_rank}" if d.finish_rank else "—"
            lines.append(f"### {i}. {d.player} — {d.archetype}（{rank}）")
            if d.deck_url:
                label = {
                    "goldfish-deck": "MTGGoldfish デッキ",
                    "goldfish-tournament": "MTGGoldfish 大会",
                    "mtgo-event": "mtgo.com（フォールバック）",
                }.get(d.link_source, "リンク")
                lines.append(f"- リンク: [{label}]({d.deck_url})")
            if d.baseline_note:
                lines.append(f"- 比較基準: {d.baseline_note}")
            lines.extend(_render_diff_table(d))

    lines.append("---")
    lines.append("")
    lines.append("## 注記")
    for n in data.notes:
        lines.append(f"- {n}")
    lines.append(
        "- Phase 1 — ローカル生成。常連プレイヤー集計は DB / `player-stats` CLI のみ（日次レポート非掲載）。"
    )
    lines.append("")
    return "\n".join(lines)


def write_report(
    conn: sqlite3.Connection,
    report_date: date | None = None,
    reports_dir: Path | None = None,
) -> tuple[Path, DailyReportData]:
    ensure_dirs()
    data = build_report_data(conn, report_date=report_date)
    md = render_markdown(data)
    out_dir = reports_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{data.report_date}.md"
    path.write_text(md, encoding="utf-8")
    return path, data
