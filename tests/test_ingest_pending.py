"""ingest_pending queue + failure alert text."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pauper_meta.discord_summary import build_ingest_failure_alert
from pauper_meta.ingest_pending import (
    clear_pending,
    load_pending,
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
        self.assertIsNone(build_ingest_failure_alert([]))


if __name__ == "__main__":
    unittest.main()
