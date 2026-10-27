"""Meta Top32 cohort + checksum regression tests (no network)."""

from __future__ import annotations

import sqlite3
import unittest
from datetime import date

from pauper_meta.db import SCHEMA
from pauper_meta.report_daily import (
    ArchCount,
    EventSummary,
    Top8Entry,
    _arch_strength_from_decks,
    _top32_deck_rows,
    meta_top32_checksum,
)


def _mem_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _seed_challenge(conn: sqlite3.Connection, event_id: str, *, only_top8_finish: bool) -> None:
    conn.execute(
        """
        INSERT INTO events (id, name, event_type, start_time, player_count, source_url, ingested_at)
        VALUES (?, 'Pauper Challenge 32', 'challenge', '2026-10-24 09:00:00.0', 33,
                'https://example.test/', '2026-10-24T12:00:00+00:00')
        """,
        (event_id,),
    )
    for i in range(1, 33):
        fr = i if (not only_top8_finish or i <= 8) else None
        conn.execute(
            """
            INSERT INTO decks (
              id, event_id, player, loginid, finish_rank, swiss_rank,
              archetype_final, swiss_wins, swiss_losses
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 3, 2)
            """,
            (
                f"{event_id}-d{i}",
                event_id,
                f"P{i}",
                f"L{i}",
                fr,
                i,
                f"Arch{(i - 1) // 4}",
            ),
        )
    # Minimal bracket so Top8 backfill / W-L paths have something
    for rnd, a, b, w in [
        (2, "P1", "P2", "P1"),
        (2, "P3", "P4", "P3"),
        (1, "P1", "P3", "P1"),
        (2, "P5", "P6", "P5"),
        (2, "P7", "P8", "P7"),
        (1, "P5", "P7", "P5"),
        (0, "P1", "P5", "P1"),
    ]:
        conn.execute(
            """
            INSERT INTO matches (event_id, round_index, round_label, player_a, player_b, wins_a, wins_b, winner)
            VALUES (?, ?, ?, ?, ?, 2, 0, ?)
            """,
            (event_id, rnd, f"r{rnd}", a, b, w),
        )
    conn.commit()


class Top32CohortTests(unittest.TestCase):
    def test_partial_finish_rank_still_yields_32(self) -> None:
        """Regression: finish_rank only on Top8 must not truncate meta to 8."""
        conn = _mem_db()
        _seed_challenge(conn, "e1", only_top8_finish=True)
        rows = _top32_deck_rows(conn, "e1")
        self.assertEqual(len(rows), 32)
        strength = _arch_strength_from_decks(conn, rows)
        self.assertEqual(sum(a.count for a in strength), 32)
        self.assertEqual(sum(a.top8_count for a in strength), 8)

    def test_full_finish_rank_yields_32(self) -> None:
        conn = _mem_db()
        _seed_challenge(conn, "e2", only_top8_finish=False)
        rows = _top32_deck_rows(conn, "e2")
        self.assertEqual(len(rows), 32)

    def test_checksum_flags_top8_only_cohort(self) -> None:
        ev = EventSummary(
            event_id="e",
            name="Challenge",
            event_type="challenge",
            start_time="2026-10-24 09:00:00.0",
            player_count=33,
            source_url="",
            deck_count=32,
            top32=[ArchCount("X", count=8, share=1.0, top8_count=8)],
            top8_bracket=[
                Top8Entry("優勝", 1, "a", "X"),
                Top8Entry("準優勝", 2, "b", "X"),
                Top8Entry("Top4", 3, "c", "X"),
                Top8Entry("Top4", 4, "d", "X"),
                Top8Entry("Top8", 5, "e", "X"),
                Top8Entry("Top8", 6, "f", "X"),
                Top8Entry("Top8", 7, "g", "X"),
                Top8Entry("Top8", 8, "h", "X"),
            ],
        )
        notes = meta_top32_checksum([ev], ev.top32)
        self.assertTrue(any("Top8 のみ" in n for n in notes))
        self.assertTrue(any(n.startswith("検算:") and "一致" in n for n in notes))

    def test_checksum_flags_top8_column_mismatch(self) -> None:
        ev = EventSummary(
            event_id="e",
            name="Challenge",
            event_type="challenge",
            start_time="2026-10-24 09:00:00.0",
            player_count=33,
            source_url="",
            deck_count=32,
            top32=[ArchCount("X", count=32, share=1.0, top8_count=3)],
            top8_bracket=[
                Top8Entry("優勝", 1, "a", "X"),
                Top8Entry("準優勝", 2, "b", "X"),
            ],
        )
        notes = meta_top32_checksum([ev], ev.top32)
        self.assertTrue(any(n.startswith("検算警告") and "Top8" in n for n in notes))


class DiscordTop8LinkTests(unittest.TestCase):
    def test_short_list_markdown(self) -> None:
        from pauper_meta.discord_summary import _fmt_top8_line

        row = Top8Entry(
            "優勝",
            1,
            "Alice",
            "Bogles",
            deck_url="https://www.mtgo.com/decklist/x/#deck_Alice",
        )
        line = _fmt_top8_line(row, include_deck_link=True)
        self.assertIn("[リスト](https://www.mtgo.com/decklist/x/#deck_Alice)", line)
        self.assertNotIn("<https://", line)


class GithubTop8LinkTests(unittest.TestCase):
    def test_archetype_only_deck_link(self) -> None:
        from pauper_meta.report_daily import _fmt_top8_md_cells

        row = Top8Entry(
            "優勝",
            1,
            "Alice",
            "Bogles",
            deck_url="https://www.mtgo.com/decklist/pauper-x/#deck_Alice",
        )
        player_cell, arch_cell = _fmt_top8_md_cells(row)
        self.assertEqual(player_cell, "Alice")
        self.assertEqual(
            arch_cell,
            "[Bogles](https://www.mtgo.com/decklist/pauper-x/#deck_Alice)",
        )
        self.assertNotIn("[Alice]", player_cell)

    def test_no_url_leaves_plain_text(self) -> None:
        from pauper_meta.report_daily import _fmt_top8_md_cells

        row = Top8Entry("Top8", 8, "Bob", "Elves")
        player_cell, arch_cell = _fmt_top8_md_cells(row)
        self.assertEqual(player_cell, "Bob")
        self.assertEqual(arch_cell, "Elves")


if __name__ == "__main__":
    unittest.main()
