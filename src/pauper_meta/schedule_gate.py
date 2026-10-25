"""DST-aware schedule gate for GitHub Actions.

Desired Discord arrival (Japan has no DST; JST = UTC+9 always):

- US Eastern **DST (EDT)**: **~13:00–14:00 JST** (after typical Actions delay)
- US Eastern **standard (EST)**: **~14:00–15:00 JST** (Fri Challenge ends ~14:00 JST in EST)

Cron UTC hours are advanced ~4h vs a naive 13:00/14:00 JST wall clock because
GitHub Actions ``schedule`` often starts ~4.5–5h late. Declared crons:

- EDT daily ``0 0 * * *`` (00:00 UTC) / weekly ``30 0 * * 1``
- EST daily ``0 1 * * *`` (01:00 UTC) / weekly ``30 1 * * 1``

This module decides whether *this cron expression* is the intended one by checking
``America/New_York`` DST via ``zoneinfo``.

**Important:** Do **not** compare ``datetime.now().hour`` to the intended UTC hour
under Actions — a 00:00 UTC cron that starts at 05:00 UTC would then be skipped.
Gate on ``github.event.schedule`` (which cron fired) instead.

Prefer writing ``should_run`` to ``GITHUB_OUTPUT`` and exiting 0 always when
invoked from Actions — see ``main()``.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# UTC hour of the *cron expression* (advanced for Actions delay), by NY DST state
UTC_HOUR_EDT = 0  # expect ~13:00–14:00 JST delivery with ~5h delay
UTC_HOUR_EST = 1  # expect ~14:00–15:00 JST (EST Fri Challenge ends ~14:00 JST)

WEEKLY_MINUTE = 30  # Monday digest: same UTC hour as daily, +30 minutes

# Cron expressions declared in .github/workflows/daily.yml
CRON_DAILY_EDT = "0 0 * * *"
CRON_DAILY_EST = "0 1 * * *"
CRON_WEEKLY_EDT = "30 0 * * 1"
CRON_WEEKLY_EST = "30 1 * * 1"


def us_eastern_in_dst(now: datetime | None = None) -> bool:
    """True when America/New_York observes DST (EDT) at ``now``."""
    now = now or datetime.now(tz=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(NY)
    dst = local.dst()
    return bool(dst and dst.total_seconds() != 0)


def intended_daily_utc_hour(now: datetime | None = None) -> int:
    """Return cron UTC hour: 0 (EDT) or 1 (EST)."""
    return UTC_HOUR_EDT if us_eastern_in_dst(now) else UTC_HOUR_EST


def intended_jst_label(now: datetime | None = None) -> str:
    """Human label for expected delivery window (not the cron wall clock)."""
    if us_eastern_in_dst(now):
        return "~13:00-14:00 JST"
    return "~14:00-15:00 JST"


def parse_cron_utc_hour(schedule: str) -> int | None:
    """Extract the UTC hour field from a 5-field cron (``m h dom mon dow``)."""
    parts = schedule.strip().split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def should_run_for_schedule(
    schedule: str | None,
    *,
    kind: str = "daily",
    now: datetime | None = None,
) -> bool:
    """True if this Actions cron expression is the DST-correct slot.

    ``schedule`` is ``github.event.schedule`` (e.g. ``\"0 0 * * *\"``). When
    empty (local/dev), fall back to wall-clock hour matching — fine for tests,
    unsafe for delayed Actions runners.
    """
    now = now or datetime.now(tz=UTC)
    intended = intended_daily_utc_hour(now)
    if schedule:
        cron_hour = parse_cron_utc_hour(schedule)
        if cron_hour is None:
            return False
        return cron_hour == intended
    # No cron context: wall-clock fallback (tests / manual CLI only)
    if kind == "weekly":
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(UTC)
        return now.weekday() == 0 and now.hour == intended and now.minute == WEEKLY_MINUTE
    return should_run_daily_slot(now)


def should_run_daily_slot(now: datetime | None = None) -> bool:
    """True if current UTC hour matches the DST-aware daily slot (wall clock).

    Prefer :func:`should_run_for_schedule` under Actions so delayed crons still run.
    """
    now = now or datetime.now(tz=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(UTC)
    return now.hour == intended_daily_utc_hour(now)


def should_run_weekly_slot(now: datetime | None = None) -> bool:
    """True if Monday UTC and hour/minute match daily slot + 30 minutes."""
    now = now or datetime.now(tz=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(UTC)
    if now.weekday() != 0:  # Monday
        return False
    return now.hour == intended_daily_utc_hour(now) and now.minute == WEEKLY_MINUTE


def _write_github_output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="DST schedule gate for Pauper daily/weekly")
    p.add_argument(
        "--kind",
        choices=("daily", "weekly"),
        default="daily",
        help="Which cron slot to validate",
    )
    p.add_argument(
        "--schedule",
        default="",
        help="github.event.schedule cron string (preferred under Actions)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Always allow (workflow_dispatch)",
    )
    args = p.parse_args(argv)

    now = datetime.now(tz=UTC)
    in_dst = us_eastern_in_dst(now)
    intended = intended_daily_utc_hour(now)
    jst = intended_jst_label(now)
    schedule = (args.schedule or os.environ.get("GITHUB_EVENT_SCHEDULE") or "").strip()

    if args.force:
        run = True
        reason = "force/workflow_dispatch"
    else:
        run = should_run_for_schedule(schedule or None, kind=args.kind, now=now)
        if schedule:
            reason = (
                f"{args.kind} cron='{schedule}' cron_hour={parse_cron_utc_hour(schedule)} "
                f"intended={intended} ({jst}, NY DST={in_dst}) "
                f"wall_utc_hour={now.hour}"
            )
        else:
            reason = (
                f"{args.kind} no-schedule wall UTC hour={now.hour} intended={intended} "
                f"({jst}, NY DST={in_dst})"
            )

    print(f"schedule_gate kind={args.kind} run={int(run)} {reason}")
    _write_github_output("should_run", "1" if run else "0")
    _write_github_output("intended_utc_hour", str(intended))
    _write_github_output("intended_jst", jst)
    _write_github_output("us_eastern_dst", "1" if in_dst else "0")
    # Always exit 0 so Actions can branch on should_run without failing the job.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
