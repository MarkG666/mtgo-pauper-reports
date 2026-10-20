"""Load aliases/overrides and recompute archetype_final + Top8 distribution."""

from __future__ import annotations

import csv
import logging
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .colors import color_identity_from_cards, display_archetype
from .db import replace_top8_dist, utc_now_iso
from .paths import ALIASES_CSV, OVERRIDES_CSV, ensure_dirs
from .players import rebuild_player_event_results

logger = logging.getLogger(__name__)


def _event_date(start_time: str | None) -> str | None:
    if not start_time:
        return None
    text = start_time.strip()
    # MTGO: "2026-10-14 12:00:00.0"
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def seed_aliases_from_csv(conn: sqlite3.Connection, path: Path | None = None) -> int:
    ensure_dirs()
    csv_path = path or ALIASES_CSV
    if not csv_path.exists():
        return 0
    n = 0
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            alias = (row.get("alias") or "").strip()
            canonical = (row.get("canonical") or "").strip()
            if not alias or not canonical:
                continue
            conn.execute(
                """
                INSERT INTO archetype_aliases (alias, canonical)
                VALUES (?, ?)
                ON CONFLICT(alias) DO UPDATE SET canonical=excluded.canonical
                """,
                (alias, canonical),
            )
            n += 1
    return n


def load_overrides_csv(conn: sqlite3.Connection, path: Path | None = None) -> int:
    ensure_dirs()
    csv_path = path or OVERRIDES_CSV
    if not csv_path.exists():
        return 0
    n = 0
    now = utc_now_iso()
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            deck_id = (row.get("deck_id") or "").strip()
            canonical = (row.get("canonical") or "").strip()
            note = (row.get("note") or "").strip() or None
            if not deck_id or not canonical:
                continue
            exists = conn.execute("SELECT 1 FROM decks WHERE id = ?", (deck_id,)).fetchone()
            if not exists:
                logger.warning("override skipped (unknown deck_id): %s", deck_id)
                continue
            conn.execute(
                """
                INSERT INTO archetype_overrides (deck_id, canonical, note, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(deck_id) DO UPDATE SET
                  canonical=excluded.canonical,
                  note=excluded.note,
                  updated_at=excluded.updated_at
                """,
                (deck_id, canonical, note, now),
            )
            n += 1
    return n


def alias_map(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        r["alias"]: r["canonical"]
        for r in conn.execute("SELECT alias, canonical FROM archetype_aliases").fetchall()
    }


def resolve_final(auto: str | None, aliases: dict[str, str], override: str | None) -> str:
    if override:
        return override
    name = (auto or "Unknown").strip() or "Unknown"
    # Apply alias chain once (alias → canonical); also allow aliasing the auto name.
    return aliases.get(name, name)


def recompute_archetype_finals(conn: sqlite3.Connection) -> int:
    aliases = alias_map(conn)
    overrides = {
        r["deck_id"]: r["canonical"]
        for r in conn.execute("SELECT deck_id, canonical FROM archetype_overrides").fetchall()
    }
    n = 0
    for row in conn.execute("SELECT id, archetype_auto FROM decks").fetchall():
        final = resolve_final(row["archetype_auto"], aliases, overrides.get(row["id"]))
        conn.execute("UPDATE decks SET archetype_final = ? WHERE id = ?", (final, row["id"]))
        n += 1
    return n


def _deck_display_label(conn: sqlite3.Connection, deck_row: sqlite3.Row) -> str:
    arch = deck_row["archetype_final"] or "Unknown"
    cid = None
    try:
        cid = deck_row["color_identity"]
    except (IndexError, KeyError):
        cid = None
    if not cid:
        card_rows = conn.execute(
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
                for r in card_rows
            ]
        )
    return display_archetype(arch, cid)


def recompute_top8_dist_for_event(conn: sqlite3.Connection, event_id: str) -> None:
    """Upsert meta snapshot into daily_top8_dist — **Top32 field** (not Top8).

    Table name is historical; values are finish_rank<=32 (else all published decks).
    """
    ev = conn.execute("SELECT start_time FROM events WHERE id = ?", (event_id,)).fetchone()
    event_date = _event_date(ev["start_time"] if ev else None)
    rows = conn.execute(
        """
        SELECT *
        FROM decks
        WHERE event_id = ?
          AND finish_rank IS NOT NULL
          AND finish_rank <= 32
        """,
        (event_id,),
    ).fetchall()
    # Fallback: swiss_rank Top32, then all published decks for the event
    if not rows:
        rows = conn.execute(
            """
            SELECT *
            FROM decks
            WHERE event_id = ?
              AND swiss_rank IS NOT NULL
              AND swiss_rank <= 32
            """,
            (event_id,),
        ).fetchall()
    if not rows:
        rows = conn.execute("SELECT * FROM decks WHERE event_id = ?", (event_id,)).fetchall()
    counts: Counter[str] = Counter()
    for r in rows:
        counts[_deck_display_label(conn, r)] += 1
    total = sum(counts.values()) or 1
    dist_rows = [
        {
            "event_id": event_id,
            "archetype": arch,
            "count": cnt,
            "share": round(cnt / total, 4),
            "event_date": event_date,
        }
        for arch, cnt in sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    ]
    replace_top8_dist(conn, event_id, dist_rows)


def recompute_all_top8(conn: sqlite3.Connection) -> int:
    ids = [r[0] for r in conn.execute("SELECT id FROM events").fetchall()]
    for eid in ids:
        recompute_top8_dist_for_event(conn, eid)
    return len(ids)


def migrate_renamed_autos(conn: sqlite3.Connection) -> int:
    """Rewrite archetype_auto for known renames so DB matches classifier canon."""
    renames = {
        "Black Burn": "BG Gardens",
        "Terror": "U Terror",
        # Interim: Kuldotha → Rally; reclassify_all_autos may then promote clear Axe.
        "Kuldotha Aggro": "Mono Red Rally",
    }
    n = 0
    for old, new in renames.items():
        cur = conn.execute(
            "UPDATE decks SET archetype_auto = ? WHERE archetype_auto = ?",
            (new, old),
        )
        n += cur.rowcount or 0
    return n


def reclassify_all_autos(conn: sqlite3.Connection) -> int:
    """Re-run heuristic classifier on every deck mainboard → archetype_auto."""
    from .classify import classify_from_card_rows

    n = 0
    for row in conn.execute("SELECT id FROM decks").fetchall():
        cards = conn.execute(
            "SELECT name, qty, board FROM cards WHERE deck_id = ?",
            (row["id"],),
        ).fetchall()
        arch = classify_from_card_rows(
            [{"name": r["name"], "qty": r["qty"], "board": r["board"]} for r in cards]
        )
        conn.execute("UPDATE decks SET archetype_auto = ? WHERE id = ?", (arch, row["id"]))
        n += 1
    return n


def apply_all(conn: sqlite3.Connection, *, reclassify: bool = True) -> dict[str, int]:
    """Seed CSV aliases/overrides, optionally reclassify, recompute finals + dists."""
    a = seed_aliases_from_csv(conn)
    o = load_overrides_csv(conn)
    renamed = migrate_renamed_autos(conn)
    reclassed = reclassify_all_autos(conn) if reclassify else 0
    decks = recompute_archetype_finals(conn)
    events = recompute_all_top8(conn)
    players = rebuild_player_event_results(conn)
    return {
        "aliases": a,
        "overrides": o,
        "renamed_autos": renamed,
        "reclassified": reclassed,
        "decks": decks,
        "events": events,
        "player_rows": players,
    }
