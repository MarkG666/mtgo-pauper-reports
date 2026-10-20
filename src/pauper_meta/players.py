"""Player / master-player aggregation (DB only — not on daily report).

Metrics (locked): appearances, Top8 rate, match W-L (swiss + bracket).
Default frequent threshold: last 30 days with 7+ appearances (config/player_stats.json).
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from .colors import color_identity_from_cards, display_archetype
from .db import replace_player_event_results
from .paths import CONFIG_DIR, ensure_dirs

DEFAULT_CONFIG = {
    "window_days": 30,
    "min_appearances": 7,
}


def load_player_stats_config(path: Path | None = None) -> dict:
    ensure_dirs()
    cfg_path = path or (CONFIG_DIR / "player_stats.json")
    if not cfg_path.exists():
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)
    out = dict(DEFAULT_CONFIG)
    for k in ("window_days", "min_appearances"):
        if k in data:
            out[k] = int(data[k])
    return out


def _deck_color(conn: sqlite3.Connection, deck_id: str, stored: str | None) -> str:
    if stored:
        return stored
    rows = conn.execute(
        "SELECT name, qty, board, colors, card_type FROM cards WHERE deck_id = ?",
        (deck_id,),
    ).fetchall()
    cards = [
        {
            "name": r["name"],
            "qty": r["qty"],
            "board": r["board"],
            "colors": (r["colors"] or "").split(",") if r["colors"] else [],
            "card_type": r["card_type"],
        }
        for r in rows
    ]
    return color_identity_from_cards(cards)


def rebuild_player_event_results(conn: sqlite3.Connection, event_id: str | None = None) -> int:
    """Rebuild player_event_results for one or all events from decks + matches."""
    if event_id:
        event_ids = [event_id]
    else:
        event_ids = [r[0] for r in conn.execute("SELECT id FROM events").fetchall()]

    n = 0
    for eid in event_ids:
        decks = conn.execute("SELECT * FROM decks WHERE event_id = ?", (eid,)).fetchall()
        match_w: dict[str, int] = defaultdict(int)
        match_l: dict[str, int] = defaultdict(int)
        game_w: dict[str, int] = defaultdict(int)
        game_l: dict[str, int] = defaultdict(int)
        for m in conn.execute(
            "SELECT player_a, player_b, wins_a, wins_b, winner FROM matches WHERE event_id = ?",
            (eid,),
        ).fetchall():
            a, b = m["player_a"], m["player_b"]
            wa, wb = int(m["wins_a"] or 0), int(m["wins_b"] or 0)
            game_w[a] += wa
            game_l[a] += wb
            game_w[b] += wb
            game_l[b] += wa
            if m["winner"] == a:
                match_w[a] += 1
                match_l[b] += 1
            elif m["winner"] == b:
                match_w[b] += 1
                match_l[a] += 1

        rows = []
        for d in decks:
            keys = set(d.keys())
            cid = _deck_color(conn, d["id"], d["color_identity"] if "color_identity" in keys else None)
            arch = d["archetype_final"] or "Unknown"
            finish = d["finish_rank"]
            rows.append(
                {
                    "event_id": eid,
                    "player": d["player"] or "",
                    "loginid": d["loginid"],
                    "deck_id": d["id"],
                    "finish_rank": finish,
                    "swiss_rank": d["swiss_rank"],
                    "archetype_final": arch,
                    "display_archetype": display_archetype(arch, cid),
                    "color_identity": cid,
                    "swiss_wins": d["swiss_wins"] if "swiss_wins" in keys else None,
                    "swiss_losses": d["swiss_losses"] if "swiss_losses" in keys else None,
                    "bracket_match_wins": match_w.get(d["player"] or "", 0),
                    "bracket_match_losses": match_l.get(d["player"] or "", 0),
                    "bracket_game_wins": game_w.get(d["player"] or "", 0),
                    "bracket_game_losses": game_l.get(d["player"] or "", 0),
                    "made_top8": 1 if finish is not None and int(finish) <= 8 else 0,
                    "is_winner": 1 if finish is not None and int(finish) == 1 else 0,
                }
            )
        replace_player_event_results(conn, eid, rows)
        n += len(rows)
    return n


def preferred_archetypes(conn: sqlite3.Connection, player: str, limit: int = 3) -> list[tuple[str, int]]:
    rows = conn.execute(
        """
        SELECT display_archetype AS a, COUNT(*) AS c
        FROM player_event_results
        WHERE player = ?
        GROUP BY display_archetype
        ORDER BY c DESC, a
        LIMIT ?
        """,
        (player, limit),
    ).fetchall()
    return [(r["a"], int(r["c"])) for r in rows]


def query_frequent_players(
    conn: sqlite3.Connection,
    *,
    min_appearances: int | None = None,
    window_days: int | None = None,
    as_of: date | None = None,
) -> list[sqlite3.Row]:
    """
    Frequent = appearances >= min_appearances within the last window_days
    (keyed on events.start_time date), as_of default = today.
    """
    cfg = load_player_stats_config()
    min_app = min_appearances if min_appearances is not None else int(cfg["min_appearances"])
    days = window_days if window_days is not None else int(cfg["window_days"])
    end = as_of or date.today()
    start = end - timedelta(days=days - 1)
    start_s, end_s = start.isoformat(), end.isoformat()

    return list(
        conn.execute(
            """
            SELECT
              r.player AS player,
              COUNT(*) AS appearances,
              SUM(r.made_top8) AS top8_count,
              ROUND(1.0 * SUM(r.made_top8) / COUNT(*), 4) AS top8_rate,
              SUM(r.is_winner) AS wins_events,
              SUM(COALESCE(r.swiss_wins, 0)) AS swiss_wins,
              SUM(COALESCE(r.swiss_losses, 0)) AS swiss_losses,
              SUM(r.bracket_match_wins) AS bracket_match_wins,
              SUM(r.bracket_match_losses) AS bracket_match_losses,
              SUM(r.bracket_game_wins) AS bracket_game_wins,
              SUM(r.bracket_game_losses) AS bracket_game_losses
            FROM player_event_results r
            JOIN events e ON e.id = r.event_id
            WHERE substr(e.start_time, 1, 10) BETWEEN ? AND ?
            GROUP BY r.player
            HAVING COUNT(*) >= ?
            ORDER BY appearances DESC, top8_rate DESC, wins_events DESC, player
            """,
            (start_s, end_s, min_app),
        ).fetchall()
    )


def format_player_stats_table(rows: list[sqlite3.Row], conn: sqlite3.Connection) -> str:
    lines = [
        "| player | appearances | top8 | top8_rate | event_wins | swiss W-L | bracket match W-L | preferred |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        prefs = preferred_archetypes(conn, r["player"])
        pref_s = ", ".join(f"{a}×{c}" for a, c in prefs) or "—"
        swiss = f"{r['swiss_wins']}-{r['swiss_losses']}"
        br = f"{r['bracket_match_wins']}-{r['bracket_match_losses']}"
        lines.append(
            f"| {r['player']} | {r['appearances']} | {r['top8_count']} | {r['top8_rate']:.0%} | "
            f"{r['wins_events']} | {swiss} | {br} | {pref_s} |"
        )
    if len(rows) == 0:
        lines.append("| (none) | | | | | | | |")
    return "\n".join(lines)
