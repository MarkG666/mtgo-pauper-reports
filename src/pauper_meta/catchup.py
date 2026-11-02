"""Yesterday one-shot catch-up after the daily ingest.

On each successful schedule path we look at **yesterday (JST)** only:

Re-ingest / regenerate when:

1. Any per-event report ``reports/daily/YYYY-MM-DD-<n>.md`` is missing for an
   event on that JST day, or
2. Any Challenge/Preliminary event for that JST report date looks **incomplete**, or
3. A DB event id for that JST day does not appear in its expected ``-N`` md
   (stale / wrong index after new same-day events land).

Skip when:

- Every event stem is already in ``data/discord_posted.json`` **and** no
  incomplete events remain **and** reports are not missing/stale.
- No Challenge/Preliminary events for that JST day (do not invent a report by
  carrying forward another day's event).

When catch-up regenerates a day that was already discord_posted (stale or
incomplete refresh), it **unmarks** that calendar day (and its ``-N`` stems)
so the Discord step can re-post per event.

## Incomplete (definition)

Documented here and used by ``event_incomplete_reason``:

- **Zero decks**: event row exists but ``COUNT(decks) = 0`` (listed/ingested shell,
  no decklists yet — typical of early publish).
- **Challenge Top8 ranks empty**: event_type looks like Challenge, decks exist,
  and either (a) SE bracket ``matches`` exist but fewer than 8 decks have
  ``finish_rank`` in 1..8, or (b) no bracket matches yet and zero decks have
  ``finish_rank`` set while ``player_count`` suggests a finished field (≥16).
  Preliminary is not judged on Top8 ranks.
- **Report absent**: per-event ``YYYY-MM-DD-<n>.md`` missing for a known event.
- **Stale report**: md missing the event id that belongs at that index.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .db import db_session, init_db
from .discord_summary import unmark_discord_posted, was_discord_posted
from .ingest import ingest_event_html, retry_pending_ingests, run_ingest
from .overrides import apply_all
from .paths import DB_PATH, REPORTS_DIR, ensure_dirs
from .report_daily import (
    _events_on_date,
    event_report_path,
    event_report_stem,
    today_jst,
    write_reports_for_day,
)
from .fetch_mtgo import MtgoFetcher

logger = logging.getLogger(__name__)

CHALLENGE_HINTS = ("challenge",)


@dataclass
class IncompleteEvent:
    event_id: str
    name: str
    reason: str


@dataclass
class CatchupResult:
    today: date
    yesterday: date
    needed: bool
    reasons: list[str] = field(default_factory=list)
    incomplete_events: list[IncompleteEvent] = field(default_factory=list)
    reinested: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    report_path: str | None = None
    report_regenerated: bool = False
    report_paths: list[str] = field(default_factory=list)


def _is_challenge(event_type: str | None, name: str | None) -> bool:
    blob = f"{event_type or ''} {name or ''}".lower()
    return any(h in blob for h in CHALLENGE_HINTS)


def event_incomplete_reason(conn: sqlite3.Connection, ev: sqlite3.Row) -> str | None:
    """Return a short reason string if the event looks incomplete, else None."""
    eid = ev["id"]
    deck_count = conn.execute(
        "SELECT COUNT(*) AS c FROM decks WHERE event_id = ?", (eid,)
    ).fetchone()["c"]
    if int(deck_count) == 0:
        return "zero_decks"

    if not _is_challenge(ev["event_type"], ev["name"]):
        return None

    top8_ranked = conn.execute(
        """
        SELECT COUNT(*) AS c FROM decks
        WHERE event_id = ?
          AND finish_rank IS NOT NULL
          AND finish_rank BETWEEN 1 AND 8
        """,
        (eid,),
    ).fetchone()["c"]
    match_count = conn.execute(
        "SELECT COUNT(*) AS c FROM matches WHERE event_id = ?", (eid,)
    ).fetchone()["c"]
    player_count = ev["player_count"]

    if int(match_count) > 0 and int(top8_ranked) < 8:
        return f"challenge_top8_ranks_incomplete (ranked={top8_ranked}, matches={match_count})"

    # Bracket not published yet, but field size suggests a finished Challenge
    # and we still have no finish_rank at all — treat as incomplete for catch-up.
    if (
        int(match_count) == 0
        and int(top8_ranked) == 0
        and player_count is not None
        and int(player_count) >= 16
    ):
        return "challenge_missing_bracket_and_ranks"

    return None


def events_on_report_date(conn: sqlite3.Connection, d: date) -> list[sqlite3.Row]:
    """Events whose **JST** start date equals ``d`` (same as daily report)."""
    return list(_events_on_date(conn, d))


def day_catchup_needed(
    conn: sqlite3.Connection, day: date
) -> tuple[bool, list[str], list[IncompleteEvent]]:
    """Decide whether ``day`` needs re-ingest / report regenerate."""
    reasons: list[str] = []
    incomplete: list[IncompleteEvent] = []
    events = events_on_report_date(conn, day)

    for ev in events:
        reason = event_incomplete_reason(conn, ev)
        if reason:
            incomplete.append(
                IncompleteEvent(
                    event_id=ev["id"],
                    name=ev["name"] or "",
                    reason=reason,
                )
            )
            reasons.append(f"incomplete:{ev['id']}:{reason}")

    if not events:
        # No Challenge/Prelim on this JST day — do not invent a carry-forward report.
        logger.info(
            "catch-up day=%s: no events (JST); skip missing_report invent",
            day.isoformat(),
        )
        return False, ["no_events_jst"], incomplete

    for idx, ev in enumerate(events, 1):
        path = event_report_path(day, idx)
        stem = event_report_stem(day, idx)
        eid = str(ev["id"])
        if not path.exists():
            # Legacy combined day file still counts as present if it mentions eid
            legacy = REPORTS_DIR / f"{day.isoformat()}.md"
            if legacy.exists() and eid in legacy.read_text(encoding="utf-8"):
                reasons.append(f"legacy_combined_report:{legacy.name}:need_split:{stem}")
            else:
                reasons.append(f"missing_report:{stem}.md")
            continue
        md = path.read_text(encoding="utf-8")
        if eid not in md:
            reasons.append(f"stale_report_missing_event:{stem}:{eid}")

    stale_or_missing = any(
        r.startswith("missing_report:")
        or r.startswith("stale_report_missing_event:")
        or r.startswith("legacy_combined_report:")
        for r in reasons
    )

    # All per-event stems already posted and complete → skip
    stems = [event_report_stem(day, i) for i in range(1, len(events) + 1)]
    all_posted = all(was_discord_posted("daily", s) for s in stems)
    # Legacy day-level key also counts as "already delivered combined"
    legacy_posted = was_discord_posted("daily", day)
    if (all_posted or legacy_posted) and not incomplete and not stale_or_missing:
        logger.info(
            "catch-up day=%s: already discord_posted and complete — skip",
            day.isoformat(),
        )
        return False, ["already_discord_posted"], incomplete

    needed = bool(reasons)
    return needed, reasons, incomplete


def _reingest_event(ev: dict | sqlite3.Row, *, fetcher: MtgoFetcher) -> str:
    eid = str(ev["id"])
    url = (ev["source_url"] or "").strip()
    if not url:
        raise RuntimeError(f"event {eid} has no source_url")
    html, cache_path = fetcher.fetch_event(url, eid, force=True)
    return ingest_event_html(
        html,
        source_url=url,
        raw_path=str(cache_path),
        fallback_type=ev["event_type"],
    )


def run_yesterday_catchup(
    *,
    now_jst_date: date | None = None,
    force: bool = False,
    lookback: int = 16,
    db_path: Path | None = None,
    fetcher: MtgoFetcher | None = None,
    lookback_days: int = 3,
) -> CatchupResult:
    """Re-ingest / regenerate recent incomplete JST days (default: yesterday + 2).

    Also force-retries ``data/ingest_pending.json`` entries (events that failed
    decklist parse and never landed decks). ``force=True`` always regenerates
    yesterday even when complete.
    """
    ensure_dirs()
    init_db(db_path)
    today = now_jst_date or today_jst()
    yesterday = today - timedelta(days=1)
    result = CatchupResult(today=today, yesterday=yesterday, needed=False)
    fetcher = fetcher or MtgoFetcher()
    days = max(1, int(lookback_days))

    days_needed: list[tuple[date, list[str], list[IncompleteEvent]]] = []
    with db_session(db_path or DB_PATH) as conn:
        for i in range(1, days + 1):
            day = today - timedelta(days=i)
            needed, reasons, incomplete = day_catchup_needed(conn, day)
            if force and day == yesterday and not needed:
                needed = True
                reasons = reasons + ["force"]
            if needed and reasons != ["no_events_jst"] and reasons != ["already_discord_posted"]:
                days_needed.append((day, reasons, incomplete))
                result.incomplete_events.extend(incomplete)

    # Always attempt pending queue (may be empty).
    pending_summary = retry_pending_ingests(db_path=db_path, fetcher=fetcher)
    logger.info(
        "catch-up pending retry candidates=%s ingested=%s failed=%s",
        pending_summary["candidates"],
        len(pending_summary["ingested"]),
        len(pending_summary["failed"]),
    )
    for f in pending_summary["failed"]:
        result.failed.append(f)
    if pending_summary["ingested"]:
        result.needed = True
        result.reinested.extend(pending_summary["ingested"])

    if not days_needed and pending_summary["candidates"] == 0:
        logger.info(
            "catch-up skip: yesterday=%s complete (report+events ok); pending empty",
            yesterday.isoformat(),
        )
        return result

    if days_needed:
        result.needed = True
        result.reasons = [f"{d.isoformat()}:{','.join(r)}" for d, r, _ in days_needed]
        logger.info("catch-up start days=%s", result.reasons)
    else:
        # Pending queue had entries (success and/or still failing).
        result.needed = True
        result.reasons = ["pending_retry"]
        logger.info("catch-up start pending_retry only")

    # Fresh listing pass (may pick up events that appeared after yesterday's run)
    listing_summary = run_ingest(
        force=False,
        lookback=lookback,
        db_path=db_path,
        fetcher=fetcher,
    )
    logger.info(
        "catch-up listing ingest candidates=%s ingested=%s failed=%s",
        listing_summary["candidates"],
        len(listing_summary["ingested"]),
        len(listing_summary["failed"]),
    )
    for f in listing_summary["failed"]:
        result.failed.append(f)

    # Force-refresh incomplete events for each needed day.
    to_refresh: list[tuple[IncompleteEvent, dict]] = []
    with db_session(db_path or DB_PATH) as conn:
        for day, _reasons, _inc in days_needed:
            _, _, incomplete = day_catchup_needed(conn, day)
            for item in incomplete:
                row = conn.execute(
                    "SELECT * FROM events WHERE id = ?", (item.event_id,)
                ).fetchone()
                if row and not any(t[0].event_id == item.event_id for t in to_refresh):
                    to_refresh.append((item, dict(row)))
        result.incomplete_events = [t[0] for t in to_refresh]

    for item, row in to_refresh:
        try:
            eid = _reingest_event(row, fetcher=fetcher)
            result.reinested.append(eid)
            logger.info("catch-up reinested %s (%s)", eid, item.reason)
        except Exception as exc:  # noqa: BLE001
            logger.exception("catch-up failed %s: %s", item.event_id, exc)
            result.failed.append({"event_id": item.event_id, "error": str(exc)})

    # Regenerate reports for every day we touched (needed days + pending successes).
    regen_days = {d for d, _, _ in days_needed}
    if pending_summary["ingested"] or result.reinested:
        # Recompute which JST days those event ids belong to.
        with db_session(db_path or DB_PATH) as conn:
            for eid in set(result.reinested) | set(pending_summary["ingested"]):
                row = conn.execute(
                    "SELECT start_time FROM events WHERE id = ?", (eid,)
                ).fetchone()
                if not row:
                    continue
                from .report_daily import _parse_event_date

                d = _parse_event_date(row["start_time"])
                if d:
                    regen_days.add(d)

    if not regen_days:
        return result

    with db_session(db_path or DB_PATH) as conn:
        apply_all(conn)
        for day in sorted(regen_days):
            written = write_reports_for_day(conn, report_date=day)
            paths = [str(p) for p, _ in written]
            result.report_paths.extend(paths)
            if unmark_discord_posted("daily", day):
                logger.info(
                    "catch-up unmarked discord_posted for %s so Discord can re-post",
                    day.isoformat(),
                )
            logger.info("catch-up reports written for %s: %s", day.isoformat(), paths)
        result.report_path = result.report_paths[0] if result.report_paths else None
        result.report_regenerated = bool(result.report_paths)

    return result
