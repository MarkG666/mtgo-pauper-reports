"""Regression tests for 2026-11-02 daily report format tweaks."""

from __future__ import annotations

import unittest
from datetime import date

from pauper_meta.distinctive import DistinctiveFlag, select_distinctive
from pauper_meta.goldfish_links import event_page_url, tournament_url
from pauper_meta.report_daily import (
    ArchCount,
    DailyReportData,
    EventSummary,
    _fmt_pct_delta,
    _with_period_deltas,
    render_markdown,
)


def _flag(player: str, rank: int, score: float = 1000.0) -> dict:
    return {
        "deck_id": f"d-{player}",
        "player": player,
        "event_id": "e1",
        "event_name": "Pauper Challenge 32",
        "archetype": f"Arch-{player}",
        "finish_rank": rank,
        "main_cards": [("Mountain", 20), ("Lightning Bolt", 4), ("Goblin", 36)],
        "side_cards": [("Pyroblast", 3), ("Red Elemental Blast", 3), ("Island", 9)],
        "deck_url": "https://example.test/deck",
        "link_source": "mtgo-event",
    }


class DistinctiveSortTests(unittest.TestCase):
    def test_selected_sorted_by_finish_rank_asc(self) -> None:
        # Force "interesting" via rare Top8 path (low arch frequency).
        decks = [
            _flag("C", 8),
            _flag("A", 2),
            _flag("B", 5),
            _flag("D", 1),
        ]
        # Empty prototypes → no card-diff path; use rare Top8 (each arch unique).
        selected = select_distinctive(
            decks,
            main_prototypes={},
            side_prototypes={},
            baseline_notes={},
            min_count=3,
            max_count=5,
            max_finish_rank=16,
            rare_archetype_threshold=2,
        )
        self.assertGreaterEqual(len(selected), 3)
        ranks = [s.finish_rank for s in selected]
        self.assertEqual(ranks, sorted(ranks))


class DeltaFormatTests(unittest.TestCase):
    def test_fmt_zero_and_none(self) -> None:
        self.assertEqual(_fmt_pct_delta(None), "—")
        self.assertEqual(_fmt_pct_delta(0.0), "-")
        self.assertEqual(_fmt_pct_delta(0.004), "-")  # rounds to 0%
        self.assertEqual(_fmt_pct_delta(0.02), "+2%")
        self.assertEqual(_fmt_pct_delta(-0.031), "-3%")

    def test_period_deltas_share_and_winrate(self) -> None:
        current = [
            ArchCount("A", 10, 0.50, 20, 10, 0.667, 2),
            ArchCount("Other", 5, 0.25, 5, 5, 0.50, 0),
        ]
        previous = [
            ArchCount("A", 8, 0.40, 10, 10, 0.50, 1),
            ArchCount("B", 4, 0.20, 4, 4, 0.50, 0),
            ArchCount("C", 2, 0.10, 1, 1, 0.50, 0),
        ]
        out = _with_period_deltas(current, previous, threshold=0.15)
        by = {a.archetype: a for a in out}
        self.assertAlmostEqual(by["A"].share_delta or 0, 0.10, places=3)
        self.assertAlmostEqual(by["A"].winrate_delta or 0, 0.167, places=2)
        self.assertIsNotNone(by["Other"].share_delta)


class GoldfishEventUrlTests(unittest.TestCase):
    def test_challenge_pattern(self) -> None:
        url = event_page_url(
            "Pauper Challenge 32",
            holding_date="2026-11-01",
            start_time="2026-11-01 18:00:00.0",
        )
        self.assertEqual(
            url,
            "https://www.mtggoldfish.com/tournament/pauper-challenge-32-2026-11-01",
        )
        self.assertEqual(
            tournament_url("Pauper Challenge 32", "2026-11-01"),
            url,
        )

    def test_holding_date_preferred(self) -> None:
        url = event_page_url(
            "Pauper Challenge 32",
            holding_date="2026-10-29",
            start_time="2026-10-28 19:00:00.0",
        )
        self.assertIn("2026-10-29", url or "")


class RenderMarkdownTests(unittest.TestCase):
    def test_no_inline_notes_target_and_has_deltas_goldfish(self) -> None:
        data = DailyReportData(
            report_date="2026-11-01",
            generated_at="2026-11-02T12:00:00+09:00",
            events=[
                EventSummary(
                    event_id="e1",
                    name="Pauper Challenge 32",
                    event_type="challenge",
                    start_time="2026-11-01 18:00:00.0",
                    player_count=59,
                    source_url="https://www.mtgo.com/decklist/pauper-challenge-32-2026-11-0112857406/",
                    deck_count=32,
                    goldfish_url="https://www.mtggoldfish.com/tournament/pauper-challenge-32-2026-11-01",
                )
            ],
            meta_overview=[],
            month_dist=[
                ArchCount("A", 10, 0.5, 10, 10, 0.5, 1, share_delta=0.02, winrate_delta=-0.01)
            ],
            week_dist=[
                ArchCount("A", 4, 0.4, 4, 4, 0.5, 1, share_delta=0.0, winrate_delta=0.03)
            ],
            distinctive=[
                DistinctiveFlag(
                    deck_id="d1",
                    player="P1",
                    event_id="e1",
                    event_name="Pauper Challenge 32",
                    archetype="A",
                    finish_rank=1,
                    reason="test",
                    score=1.0,
                    baseline_note="直近2週間の同アーキ 10本",
                )
            ],
            notes=["検算: should not appear"],
            report_stem="2026-11-01",
            holding_date="2026-11-01",
        )
        md = render_markdown(data)
        self.assertNotIn("対象: **単一**", md)
        self.assertNotIn("## 注記", md)
        self.assertNotIn("検算: should not appear", md)
        self.assertIn("MTGGoldfish", md)
        self.assertIn("シェア前月差", md)
        self.assertIn("勝率前月差", md)
        self.assertIn("シェア前週差", md)
        self.assertIn("勝率前週差", md)
        self.assertIn("reports/meta/report-notes.md", md)
        # Baseline then blank line then メイン
        self.assertIn("- 比較基準: 直近2週間の同アーキ 10本\n\n**メイン**", md)


if __name__ == "__main__":
    unittest.main()
