"""MtgoFetcher HTTP retries + listing soft-fail when primary listing dies."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from pauper_meta.fetch_mtgo import (
    FETCH_RETRY_BACKOFF,
    FETCH_RETRY_DELAY_SEC,
    MtgoFetcher,
)
from pauper_meta.ingest import run_ingest
from pauper_meta.ingest_pending import is_retryable_error


class FetchRetryTests(unittest.TestCase):
    def test_listing_retry_spacing_is_minute_scale(self) -> None:
        # ConnectTimeout recoveries need minutes between attempts, not ~20s.
        self.assertGreaterEqual(FETCH_RETRY_DELAY_SEC, 60.0)
        self.assertGreaterEqual(FETCH_RETRY_BACKOFF, 2.0)

    def test_connect_timeout_is_retryable_marker(self) -> None:
        self.assertTrue(is_retryable_error("ConnectTimeout: Connection to www.mtgo.com timed out"))
        self.assertTrue(
            is_retryable_error(
                "HTTPSConnectionPool(host='www.mtgo.com', port=443): Max retries exceeded"
            )
        )

    def test_get_text_retries_connect_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fetcher = MtgoFetcher(
                raw_dir=Path(tmp),
                delay_sec=0,
                fetch_retries=2,
                fetch_retry_delay_sec=0,
            )
            ok = MagicMock()
            ok.status_code = 200
            ok.text = "<html>ok</html>"
            ok.encoding = "utf-8"
            ok.raise_for_status = MagicMock()

            side_effects = [
                requests.exceptions.ConnectTimeout("boom"),
                requests.exceptions.ConnectTimeout("boom2"),
                ok,
            ]
            with patch.object(fetcher.session, "get", side_effect=side_effects) as get:
                text = fetcher.get_text("https://example.test/listing")
            self.assertEqual(text, "<html>ok</html>")
            self.assertEqual(get.call_count, 3)

    def test_run_ingest_soft_fails_when_all_listings_die(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw"
            db = Path(tmp) / "t.sqlite"
            raw.mkdir()
            fetcher = MtgoFetcher(raw_dir=raw, delay_sec=0, fetch_retries=0, fetch_retry_delay_sec=0)

            def boom(*_a, **_k):
                raise requests.exceptions.ConnectTimeout("listing down")

            with (
                patch.object(fetcher, "fetch_listing", side_effect=boom),
                patch.object(fetcher, "fetch_monthly_listing", side_effect=boom),
                patch("pauper_meta.ingest.init_db"),
                patch("pauper_meta.ingest._prune_pending_queues"),
            ):
                summary = run_ingest(db_path=db, fetcher=fetcher, lookback=8)

            self.assertEqual(summary["candidates"], 0)
            self.assertEqual(len(summary["listing_errors"]), 3)  # primary + 2 months
            self.assertEqual(summary["failed"][0]["slug"], "listing")


if __name__ == "__main__":
    unittest.main()
