"""DST schedule gate: cron hour matching (2026-11 advance to 22/23 UTC)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from pauper_meta.schedule_gate import (
    CRON_DAILY_EDT,
    CRON_DAILY_EST,
    UTC_HOUR_EDT,
    UTC_HOUR_EST,
    intended_daily_utc_hour,
    should_run_for_schedule,
    us_eastern_in_dst,
)


class ScheduleGateTests(unittest.TestCase):
    def test_cron_constants(self) -> None:
        self.assertEqual(UTC_HOUR_EDT, 22)
        self.assertEqual(UTC_HOUR_EST, 23)
        self.assertEqual(CRON_DAILY_EDT, "0 22 * * *")
        self.assertEqual(CRON_DAILY_EST, "0 23 * * *")

    def test_est_november_uses_23(self) -> None:
        # After US DST ends (first Sunday in Nov) — 2026-11-10 is EST.
        now = datetime(2026, 11, 10, 7, 0, tzinfo=timezone.utc)
        self.assertFalse(us_eastern_in_dst(now))
        self.assertEqual(intended_daily_utc_hour(now), 23)
        self.assertTrue(should_run_for_schedule(CRON_DAILY_EST, now=now))
        self.assertFalse(should_run_for_schedule(CRON_DAILY_EDT, now=now))

    def test_edt_july_uses_22(self) -> None:
        now = datetime(2026, 7, 15, 5, 0, tzinfo=timezone.utc)
        self.assertTrue(us_eastern_in_dst(now))
        self.assertEqual(intended_daily_utc_hour(now), 22)
        self.assertTrue(should_run_for_schedule(CRON_DAILY_EDT, now=now))
        self.assertFalse(should_run_for_schedule(CRON_DAILY_EST, now=now))


if __name__ == "__main__":
    unittest.main()
