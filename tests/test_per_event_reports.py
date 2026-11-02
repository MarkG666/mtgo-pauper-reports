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
    event_report_stem,
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
) -> None:
    conn.execute(
        """
        INSERT INTO events (id, name, event_type, start_time, player_count, source_url, ingested_at)
        VALUES (?, ?, 'challenge', ?, 32, ?, '2026-10-30T12:00:00+00:00')
        """,
        (event_id, name, start_time, f"https://example.test/{event_id}/"),
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
    def test_stem_scheme(self) -> None:
        self.assertEqual(event_report_stem(date(2026, 10, 30), 1), "2026-10-30-1")
        self.assertEqual(event_report_stem("2026-10-30", 2), "2026-10-30-2")

    def test_two_events_same_jst_day_two_files(self) -> None:
        conn = _mem_db()
        # JST 2026-10-30 04:00 = UTC 10-29 19:00; JST 22:00 = UTC 10-30 13:00
        _seed_event(conn, "eA", start_time="2026-10-29 19:00:00.0")
        _seed_event(conn, "eB", start_time="2026-10-30 13:00:00.0")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            written = write_reports_for_day(
                conn, report_date=date(2026, 10, 30), reports_dir=out
            )
            self.assertEqual([p.name for p, _ in written], ["2026-10-30-1.md", "2026-10-30-2.md"])
            self.assertEqual(written[0][1].events[0].event_id, "eA")
            self.assertEqual(written[1][1].events[0].event_id, "eB")
            # Meta scoped to one event — not merged 64
            self.assertEqual(sum(a.count for a in written[0][1].meta_overview), 32)
            self.assertEqual(sum(a.count for a in written[1][1].meta_overview), 32)
            text1 = written[0][0].read_text(encoding="utf-8")
            self.assertIn("eA", text1)
            self.assertNotIn("eB", text1)
            text2 = written[1][0].read_text(encoding="utf-8")
            self.assertIn("eB", text2)
            self.assertNotIn("eA", text2)
            # Discord summary mentions only one event
            summary = build_daily_summary(written[0][1])
            self.assertIn("2026-10-30-1", summary)
            self.assertNotIn("eB", summary)

    def test_discord_keys_are_stems(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "discord_posted.json"
            mark_discord_posted("daily", "2026-10-30-1", path=path)
            self.assertTrue(was_discord_posted("daily", "2026-10-30-1", path=path))
            self.assertFalse(was_discord_posted("daily", "2026-10-30-2", path=path))
            # Unmarking the calendar day clears stems
            mark_discord_posted("daily", "2026-10-30-2", path=path)
            self.assertTrue(unmark_discord_posted("daily", date(2026, 10, 30), path=path))
            data = load_discord_posted(path)
            self.assertEqual(data["daily"], [])


if __name__ == "__main__":
    unittest.main()
