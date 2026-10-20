"""SQLite schema and upsert helpers."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .paths import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  start_time TEXT,
  channel TEXT NOT NULL DEFAULT 'mtgo',
  event_type TEXT NOT NULL,
  player_count INTEGER,
  source_url TEXT,
  raw_path TEXT,
  ingested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decks (
  id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES events(id),
  player TEXT,
  loginid TEXT,
  finish_rank INTEGER,
  swiss_rank INTEGER,
  archetype_auto TEXT,
  archetype_final TEXT,
  source_archetype_label TEXT,
  deck_index INTEGER,
  color_identity TEXT,
  swiss_wins INTEGER,
  swiss_losses INTEGER
);

CREATE TABLE IF NOT EXISTS cards (
  deck_id TEXT NOT NULL REFERENCES decks(id),
  board TEXT NOT NULL,
  name TEXT NOT NULL,
  qty INTEGER NOT NULL,
  colors TEXT,
  card_type TEXT,
  PRIMARY KEY (deck_id, board, name)
);

CREATE TABLE IF NOT EXISTS matches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL REFERENCES events(id),
  round_index INTEGER,
  round_label TEXT,
  player_a TEXT,
  player_b TEXT,
  wins_a INTEGER,
  wins_b INTEGER,
  winner TEXT
);

CREATE TABLE IF NOT EXISTS archetype_aliases (
  alias TEXT PRIMARY KEY,
  canonical TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS archetype_overrides (
  deck_id TEXT PRIMARY KEY REFERENCES decks(id),
  canonical TEXT NOT NULL,
  note TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_top8_dist (
  event_id TEXT NOT NULL,
  archetype TEXT NOT NULL,
  count INTEGER NOT NULL,
  share REAL NOT NULL,
  event_date TEXT,
  PRIMARY KEY (event_id, archetype)
);

CREATE TABLE IF NOT EXISTS player_event_results (
  event_id TEXT NOT NULL REFERENCES events(id),
  player TEXT NOT NULL,
  loginid TEXT,
  deck_id TEXT,
  finish_rank INTEGER,
  swiss_rank INTEGER,
  archetype_final TEXT,
  display_archetype TEXT,
  color_identity TEXT,
  swiss_wins INTEGER,
  swiss_losses INTEGER,
  bracket_match_wins INTEGER NOT NULL DEFAULT 0,
  bracket_match_losses INTEGER NOT NULL DEFAULT 0,
  bracket_game_wins INTEGER NOT NULL DEFAULT 0,
  bracket_game_losses INTEGER NOT NULL DEFAULT 0,
  made_top8 INTEGER NOT NULL DEFAULT 0,
  is_winner INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (event_id, player)
);

CREATE INDEX IF NOT EXISTS idx_decks_event ON decks(event_id);
CREATE INDEX IF NOT EXISTS idx_cards_deck ON cards(deck_id);
CREATE INDEX IF NOT EXISTS idx_matches_event ON matches(event_id);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_time);
CREATE INDEX IF NOT EXISTS idx_top8_date ON daily_top8_dist(event_date);
CREATE INDEX IF NOT EXISTS idx_per_player ON player_event_results(player);
CREATE INDEX IF NOT EXISTS idx_per_login ON player_event_results(loginid);

CREATE VIEW IF NOT EXISTS v_player_stats AS
SELECT
  player,
  COUNT(*) AS appearances,
  SUM(made_top8) AS top8_count,
  ROUND(1.0 * SUM(made_top8) / COUNT(*), 4) AS top8_rate,
  SUM(is_winner) AS wins_events,
  SUM(COALESCE(swiss_wins, 0)) AS swiss_wins,
  SUM(COALESCE(swiss_losses, 0)) AS swiss_losses,
  SUM(bracket_match_wins) AS bracket_match_wins,
  SUM(bracket_match_losses) AS bracket_match_losses,
  SUM(bracket_game_wins) AS bracket_game_wins,
  SUM(bracket_game_losses) AS bracket_game_losses
FROM player_event_results
GROUP BY player;
"""

# Columns added after initial Phase 1 schema — applied idempotently.
_MIGRATIONS: list[tuple[str, str]] = [
    ("decks", "deck_index", "ALTER TABLE decks ADD COLUMN deck_index INTEGER"),
    ("decks", "color_identity", "ALTER TABLE decks ADD COLUMN color_identity TEXT"),
    ("decks", "swiss_wins", "ALTER TABLE decks ADD COLUMN swiss_wins INTEGER"),
    ("decks", "swiss_losses", "ALTER TABLE decks ADD COLUMN swiss_losses INTEGER"),
    ("cards", "colors", "ALTER TABLE cards ADD COLUMN colors TEXT"),
    ("cards", "card_type", "ALTER TABLE cards ADD COLUMN card_type TEXT"),
]


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    ensure_dirs()
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_session(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def migrate(conn: sqlite3.Connection) -> None:
    for table, col, ddl in _MIGRATIONS:
        try:
            cols = _table_columns(conn, table)
        except sqlite3.Error:
            continue
        if col not in cols:
            conn.execute(ddl)


def init_db(db_path: Path | None = None) -> Path:
    ensure_dirs()
    path = db_path or DB_PATH
    with db_session(path) as conn:
        conn.executescript(SCHEMA)
        migrate(conn)
    return path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def upsert_event(conn: sqlite3.Connection, event: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO events (id, name, start_time, channel, event_type, player_count,
                            source_url, raw_path, ingested_at)
        VALUES (:id, :name, :start_time, :channel, :event_type, :player_count,
                :source_url, :raw_path, :ingested_at)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,
          start_time=excluded.start_time,
          channel=excluded.channel,
          event_type=excluded.event_type,
          player_count=excluded.player_count,
          source_url=excluded.source_url,
          raw_path=excluded.raw_path,
          ingested_at=excluded.ingested_at
        """,
        event,
    )


def replace_decks_for_event(
    conn: sqlite3.Connection,
    event_id: str,
    decks: list[dict[str, Any]],
    cards: list[dict[str, Any]],
) -> None:
    """Replace decks/cards for an event while preserving overrides for surviving deck ids."""
    existing_ids = {
        r[0]
        for r in conn.execute("SELECT id FROM decks WHERE event_id = ?", (event_id,)).fetchall()
    }
    new_ids = {d["id"] for d in decks}
    removed = existing_ids - new_ids
    if removed:
        conn.executemany("DELETE FROM archetype_overrides WHERE deck_id = ?", [(i,) for i in removed])
        conn.executemany("DELETE FROM cards WHERE deck_id = ?", [(i,) for i in removed])
        conn.executemany("DELETE FROM decks WHERE id = ?", [(i,) for i in removed])

    for d in decks:
        conn.execute(
            """
            INSERT INTO decks (
              id, event_id, player, loginid, finish_rank, swiss_rank,
              archetype_auto, archetype_final, source_archetype_label,
              deck_index, color_identity, swiss_wins, swiss_losses
            )
            VALUES (
              :id, :event_id, :player, :loginid, :finish_rank, :swiss_rank,
              :archetype_auto, :archetype_final, :source_archetype_label,
              :deck_index, :color_identity, :swiss_wins, :swiss_losses
            )
            ON CONFLICT(id) DO UPDATE SET
              event_id=excluded.event_id,
              player=excluded.player,
              loginid=excluded.loginid,
              finish_rank=excluded.finish_rank,
              swiss_rank=excluded.swiss_rank,
              archetype_auto=excluded.archetype_auto,
              archetype_final=excluded.archetype_final,
              source_archetype_label=excluded.source_archetype_label,
              deck_index=excluded.deck_index,
              color_identity=excluded.color_identity,
              swiss_wins=excluded.swiss_wins,
              swiss_losses=excluded.swiss_losses
            """,
            d,
        )
        conn.execute("DELETE FROM cards WHERE deck_id = ?", (d["id"],))

    if cards:
        conn.executemany(
            """
            INSERT INTO cards (deck_id, board, name, qty, colors, card_type)
            VALUES (:deck_id, :board, :name, :qty, :colors, :card_type)
            ON CONFLICT(deck_id, board, name) DO UPDATE SET
              qty=excluded.qty,
              colors=excluded.colors,
              card_type=excluded.card_type
            """,
            cards,
        )


def replace_matches_for_event(conn: sqlite3.Connection, event_id: str, matches: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM matches WHERE event_id = ?", (event_id,))
    if matches:
        conn.executemany(
            """
            INSERT INTO matches (event_id, round_index, round_label, player_a, player_b,
                                 wins_a, wins_b, winner)
            VALUES (:event_id, :round_index, :round_label, :player_a, :player_b,
                    :wins_a, :wins_b, :winner)
            """,
            matches,
        )


def replace_top8_dist(conn: sqlite3.Connection, event_id: str, rows: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM daily_top8_dist WHERE event_id = ?", (event_id,))
    if rows:
        conn.executemany(
            """
            INSERT INTO daily_top8_dist (event_id, archetype, count, share, event_date)
            VALUES (:event_id, :archetype, :count, :share, :event_date)
            """,
            rows,
        )


def replace_player_event_results(conn: sqlite3.Connection, event_id: str, rows: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM player_event_results WHERE event_id = ?", (event_id,))
    if rows:
        conn.executemany(
            """
            INSERT INTO player_event_results (
              event_id, player, loginid, deck_id, finish_rank, swiss_rank,
              archetype_final, display_archetype, color_identity,
              swiss_wins, swiss_losses,
              bracket_match_wins, bracket_match_losses,
              bracket_game_wins, bracket_game_losses,
              made_top8, is_winner
            ) VALUES (
              :event_id, :player, :loginid, :deck_id, :finish_rank, :swiss_rank,
              :archetype_final, :display_archetype, :color_identity,
              :swiss_wins, :swiss_losses,
              :bracket_match_wins, :bracket_match_losses,
              :bracket_game_wins, :bracket_game_losses,
              :made_top8, :is_winner
            )
            """,
            rows,
        )


def event_exists(conn: sqlite3.Connection, event_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone()
    return row is not None


def list_event_ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT id FROM events").fetchall()}
