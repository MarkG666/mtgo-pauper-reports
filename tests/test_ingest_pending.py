"""ingest_pending queue + failure alert text + same-day retry helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pauper_meta.discord_summary import (
    build_ingest_failure_alert,
    post_ingest_failure_alert,
    resolve_alert_webhook_url,
)
from pauper_meta.ingest import ingest_with_same_day_retries
from pauper_meta.ingest_pending import (
    clear_pending,
    filter_alertable_failures,
    is_benign_error,
    is_retryable_error,
    load_pending,
    prune_resolved,
    register_failure,
)


class IngestPendingTests(unittest.TestCase):
    def test_register_and_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending.json"
            register_failure(
                event_id="12857391",
                slug="pauper-challenge-16-2026-10-3112857391",
                url="https://example.test/e/",
                event_type="challenge",
                error="window.MTGO.decklists.data not found in HTML",
                path=path,
            )
            register_failure(
                event_id="12857391",
                slug="pauper-challenge-16-2026-10-3112857391",
                url="https://example.test/e/",
                event_type="challenge",
                error="still missing",
                path=path,
            )
            items = load_pending(path)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["attempts"], 2)
            self.assertTrue(clear_pending("12857391", path=path))
            self.assertEqual(load_pending(path), [])

    def test_refuse_overwrite_not_queued(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending.json"
            register_failure(
                event_id="12856975",
                slug="pauper-challenge-16-2026-10-2412856975",
                url="https://example.test/e/",
                event_type="challenge",
                error="refusing to overwrite event 12856975 (32 decks) with empty decklists parse",
                path=path,
            )
            self.assertEqual(load_pending(path), [])

    def test_failure_alert_text(self) -> None:
        text = build_ingest_failure_alert(
            [
                {
                    "event_id": "12857391",
                    "slug": "pauper-challenge-16-2026-10-3112857391",
                    "url": "https://www.mtgo.com/decklist/x/",
                    "error": "window.MTGO.decklists.data not found in HTML",
                }
            ]
        )
        assert text is not None
        self.assertIn("12857391", text)
        self.assertIn("ingest 失敗", text)
        self.assertIn("同日ジョブ内で再試行済み", text)
        self.assertIsNone(build_ingest_failure_alert([]))

    def test_alert_filters_refuse_overwrite(self) -> None:
        text = build_ingest_failure_alert(
            [
                {
                    "event_id": "12856975",
                    "slug": "x",
                    "error": "refusing to overwrite event 12856975 (32 decks) with empty decklists parse",
                }
            ]
        )
        self.assertIsNone(text)
        filtered = filter_alertable_failures(
            [
                {"event_id": "1", "error": "refusing to overwrite"},
                {"event_id": "2", "error": "window.MTGO.decklists.data not found in HTML"},
            ]
        )
        self.assertEqual([f["event_id"] for f in filtered], ["2"])

    def test_alert_webhook_never_falls_back_to_group(self) -> None:
        env = {
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/group/xxx",
            # alert secret intentionally absent
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("DISCORD_ALERT_WEBHOOK_URL", None)
            self.assertIsNone(resolve_alert_webhook_url())
            sent = post_ingest_failure_alert(
                [{"event_id": "1", "slug": "x", "error": "boom"}]
            )
            self.assertFalse(sent)

    def test_retryable_and_benign_markers(self) -> None:
        self.assertTrue(is_retryable_error("window.MTGO.decklists.data not found in HTML"))
        self.assertTrue(is_retryable_error("Read timed out"))
        self.assertFalse(is_retryable_error("refusing to overwrite event 1 (32 decks)"))
        self.assertTrue(is_benign_error("refusing to overwrite event 1 (32 decks)"))

    def test_same_day_retries_then_succeed(self) -> None:
        calls = {"n": 0}

        def flaky() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise ValueError("window.MTGO.decklists.data not found in HTML")
            return "ok-id"

        got = ingest_with_same_day_retries(
            attempt_fn=flaky,
            label="test",
            retries=2,
            delay_sec=0,
        )
        self.assertEqual(got, "ok-id")
        self.assertEqual(calls["n"], 3)

    def test_same_day_retries_exhausted(self) -> None:
        def always_fail() -> str:
            raise ValueError("window.MTGO.decklists.data not found in HTML")

        with self.assertRaises(ValueError):
            ingest_with_same_day_retries(
                attempt_fn=always_fail,
                label="test",
                retries=2,
                delay_sec=0,
            )

    def test_prune_resolved_drops_events_with_decks(self) -> None:
        import sqlite3

        from pauper_meta.db import init_db

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            pending = Path(tmp) / "pending.json"
            init_db(db)
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            conn.execute(
                "INSERT INTO events (id, name, start_time, channel, event_type, ingested_at) "
                "VALUES ('99', 'Test', '2026-10-01 12:00:00.0', 'mtgo', 'challenge', '2026-10-01T12:00:00+00:00')"
            )
            conn.execute(
                "INSERT INTO decks (id, event_id, player, archetype_auto, archetype_final) "
                "VALUES ('d1', '99', 'p', 'X', 'X')"
            )
            conn.commit()
            register_failure(
                event_id="99",
                slug="test",
                url="https://example.test/",
                event_type="challenge",
                error="window.MTGO.decklists.data not found in HTML",
                path=pending,
            )
            register_failure(
                event_id="100",
                slug="empty",
                url="https://example.test/2/",
                event_type="challenge",
                error="window.MTGO.decklists.data not found in HTML",
                path=pending,
            )
            dropped = prune_resolved(conn, path=pending)
            conn.close()
            self.assertEqual(dropped, 1)
            left = load_pending(pending)
            self.assertEqual(len(left), 1)
            self.assertEqual(left[0]["event_id"], "100")


if __name__ == "__main__":
    unittest.main()
