"""ingest_pending queue + failure alert text."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import os
from unittest.mock import patch

from pauper_meta.discord_summary import (
    build_ingest_failure_alert,
    post_ingest_failure_alert,
    resolve_alert_webhook_url,
)
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


if __name__ == "__main__":
    unittest.main()
