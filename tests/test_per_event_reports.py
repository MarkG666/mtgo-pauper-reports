"""Per-event daily report filename / scoping regression tests."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from pauper_meta.db import SCHEMA
from pauper_meta.discord_summary import (
    build_daily_summary,
    load_discord_posted,
    mark_discord_posted,
    unmark_discord_posted,
    was_discord_posted,
)
from pauper_meta.report_daily import (
    assign_holding_stems,
    event_report_stem,
    mtgo_holding_date,
    write_reports_for_day,
)


def _mem_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _seed_event(
    conn: sqlite3.Connection,
    event_id: str,
    *,
    start_time: str,
    name: str = "Pauper Challenge 32",
    source_url: str | None = None,
    player_count: int = 51,
) -> None:
    url = source_url or f"https://example.test/{event_id}/"
    conn.execute(
        """
        INSERT INTO events (id, name, event_type, start_time, player_count, source_url, ingested_at)
        VALUES (?, ?, 'challenge', ?, ?, ?, '2026-10-30T12:00:00+00:00')
        """,
        (event_id, name, start_time, player_count, url),
    )
    for i in range(1, 33):
        conn.execute(
            """
            INSERT INTO decks (
              id, event_id, player, finish_rank, swiss_rank,
              archetype_final, swiss_wins, swiss_losses
            ) VALUES (?, ?, ?, ?, ?, ?, 3, 2)
            """,
            (
                f"{event_id}-d{i}",
                event_id,
                f"P{i}",
                i if i <= 8 else None,
                i,
                f"Arch{event_id[-1]}",
            ),
        )
    conn.commit()


class PerEventReportTests(unittest.TestCase):
    def test_holding_date_from_mtgo_url(self) -> None:
        url = "https://www.mtgo.com/decklist/pauper-challenge-32-2026-10-2912857378/"
        self.assertEqual(mtgo_holding_date(url), date(2026, 10, 29))

    def test_stem_scheme(self) -> None:
        # Unique holding date → bare YYYY-MM-DD
        self.assertEqual(event_report_stem(date(2026, 10, 29), None), "2026-10-29")
        # Collision only → -N
        self.assertEqual(event_report_stem(date(2026, 10, 30), 1), "2026-10-30-1")
        self.assertEqual(event_report_stem("2026-10-30", 2), "2026-10-30-2")

    def test_two_events_same_jst_day_different_holding_dates(self) -> None:
        """Typical Fri+Sat: same JST day, different MTGO holding dates → two bare files."""
        conn = _mem_db()
        # JST 2026-10-30 04:00 = UTC 10-29 19:00; JST 22:00 = UTC 10-30 13:00
        _seed_event(
            conn,
            "eA",
            start_time="2026-10-29 19:00:00.0",
            source_url="https://www.mtgo.com/decklist/pauper-challenge-32-2026-10-2912857378/",
            player_count=51,
        )
        _seed_event(
            conn,
            "eB",
            start_time="2026-10-30 13:00:00.0",
            source_url="https://www.mtgo.com/decklist/pauper-challenge-32-2026-10-3012857383/",
            player_count=53,
        )
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            written = write_reports_for_day(
                conn, report_date=date(2026, 10, 30), reports_dir=out
            )
            names = [p.name for p, _ in written]
            self.assertEqual(names, ["2026-10-29.md", "2026-10-30.md"])
            self.assertEqual(written[0][1].events[0].event_id, "eA")
            self.assertEqual(written[1][1].events[0].event_id, "eB")
            self.assertEqual(written[0][1].holding_date, "2026-10-29")
            self.assertEqual(written[1][1].holding_date, "2026-10-30")
            # Meta scoped to one event — not merged 64
            self.assertEqual(sum(a.count for a in written[0][1].meta_overview), 32)
            self.assertEqual(sum(a.count for a in written[1][1].meta_overview), 32)
            text1 = written[0][0].read_text(encoding="utf-8")
            self.assertIn("12857378", text1)
            self.assertNotIn("12857383", text1)
            # Per-event meta Top32 is ArchA only (cumulative tables may still show ArchB)
            self.assertEqual(
                [a.archetype for a in written[0][1].meta_overview], ["ArchA"]
            )
            self.assertEqual(
                [a.archetype for a in written[1][1].meta_overview], ["ArchB"]
            )
            text2 = written[1][0].read_text(encoding="utf-8")
            self.assertIn("12857383", text2)
            self.assertNotIn("12857378", text2)
            # Discord header: event name + holding date + players
            summary = build_daily_summary(written[0][1])
            self.assertIn('**Pauper Challenge 32** ("2026-10-29") — 51人', summary)
            summary2 = build_daily_summary(written[1][1])
            self.assertIn('**Pauper Challenge 32** ("2026-10-30") — 53人', summary2)

    def test_holding_date_collision_uses_suffix(self) -> None:
        conn = _mem_db()
        # Both on JST 2026-10-30, same MTGO holding date in URL → -1 / -2
        _seed_event(
            conn,
            "e1",
            start_time="2026-10-30 01:00:00.0",  # JST 10:00
            source_url="https://www.mtgo.com/decklist/pauper-challenge-32-2026-10-30aaa/",
        )
        _seed_event(
            conn,
            "e2",
            start_time="2026-10-30 10:00:00.0",  # JST 19:00
            source_url="https://www.mtgo.com/decklist/pauper-prelim-2026-10-30bbb/",
            name="Pauper Preliminary",
        )
        rows = conn.execute("SELECT * FROM events ORDER BY start_time").fetchall()
        stems = assign_holding_stems(list(rows))
        self.assertEqual(stems["e1"][0], "2026-10-30-1")
        self.assertEqual(stems["e2"][0], "2026-10-30-2")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            written = write_reports_for_day(
                conn, report_date=date(2026, 10, 30), reports_dir=out
            )
            self.assertEqual(
                [p.name for p, _ in written], ["2026-10-30-1.md", "2026-10-30-2.md"]
            )

    def test_discord_keys_are_stems(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "discord_posted.json"
            mark_discord_posted("daily", "2026-10-29", path=path)
            self.assertTrue(was_discord_posted("daily", "2026-10-29", path=path))
            self.assertFalse(was_discord_posted("daily", "2026-10-30", path=path))
            mark_discord_posted("daily", "2026-10-30", path=path)
            # Unmarking the bare day clears it; collision stems with prefix also clear
            mark_discord_posted("daily", "2026-10-30-1", path=path)
            self.assertTrue(unmark_discord_posted("daily", date(2026, 10, 30), path=path))
            data = load_discord_posted(path)
            self.assertEqual(data["daily"], ["2026-10-29"])


if __name__ == "__main__":
    unittest.main()
