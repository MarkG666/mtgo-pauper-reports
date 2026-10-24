"""Yesterday one-shot catch-up after the daily ingest.

On each successful schedule path we look at **yesterday (JST)** only:

Re-ingest / regenerate when:

1. ``reports/daily/YYYY-MM-DD.md`` is missing **and** that JST day has at least
   one Challenge/Preliminary event, or
2. Any Challenge/Preliminary event for that JST report date looks **incomplete**.

Skip when:

- Already recorded in ``data/discord_posted.json`` for that day **and** no
  incomplete events remain (Discord does not need another try; report md may
  already exist from a prior successful path).
- No Challenge/Preliminary events for that JST day (do not invent a report by
  carrying forward another day's event).

## Incomplete (definition)

Documented here and used by ``event_incomplete_reason``:

- **Zero decks**: event row exists but ``COUNT(decks) = 0`` (listed/ingested shell,
  no decklists yet — typical of early publish).
- **Challenge Top8 ranks empty**: event_type looks like Challenge, decks exist,
  and either (a) SE bracket ``matches`` exist but fewer than 8 decks have
  ``finish_rank`` in 1..8, or (b) no bracket matches yet and zero decks have
  ``finish_rank`` set while ``player_count`` suggests a finished field (≥16).
  Preliminary is not judged on Top8 ranks.
- **Report absent**: only with events present for that JST day (not per-event).

Events that finish >24h late can still be missed; cancelled listings may look
incomplete once — see project doc ``yesterday-catchup.md``.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .db import db_session, init_db
from .discord_summary import was_discord_posted
from .ingest import ingest_event_html, run_ingest
from .overrides import apply_all
from .paths import DB_PATH, REPORTS_DIR, ensure_dirs
from .report_daily import _events_on_date, today_jst, write_report
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
    report_path = REPORTS_DIR / f"{day.isoformat()}.md"
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

    if not report_path.exists():
        reasons.append(f"missing_report:{report_path.name}")

    if was_discord_posted("daily", day) and not incomplete:
        # Already delivered; nothing incomplete to refresh.
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
) -> CatchupResult:
    """If yesterday (JST) is incomplete or missing a report, re-ingest and regenerate.

    ``force=True`` always regenerates yesterday's report (still one lookback day).
    """
    ensure_dirs()
    init_db(db_path)
    today = now_jst_date or today_jst()
    yesterday = today - timedelta(days=1)
    result = CatchupResult(today=today, yesterday=yesterday, needed=False)
    fetcher = fetcher or MtgoFetcher()

    with db_session(db_path or DB_PATH) as conn:
        needed, reasons, incomplete = day_catchup_needed(conn, yesterday)
        if force and not needed:
            needed = True
            reasons = reasons + ["force"]
        result.needed = needed
        result.reasons = reasons
        result.incomplete_events = incomplete

        if not needed:
            logger.info(
                "catch-up skip: yesterday=%s complete (report+events ok)",
                yesterday.isoformat(),
            )
            return result

        logger.info(
            "catch-up start yesterday=%s reasons=%s",
            yesterday.isoformat(),
            reasons,
        )

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

    # Force-refresh specifically incomplete events still in DB (close session first:
    # ingest_event_html opens its own connection).
    to_refresh: list[tuple[IncompleteEvent, dict]] = []
    with db_session(db_path or DB_PATH) as conn:
        _, _, incomplete = day_catchup_needed(conn, yesterday)
        result.incomplete_events = incomplete
        for item in incomplete:
            row = conn.execute(
                "SELECT * FROM events WHERE id = ?", (item.event_id,)
            ).fetchone()
            if row:
                to_refresh.append((item, dict(row)))

    for item, row in to_refresh:
        try:
            eid = _reingest_event(row, fetcher=fetcher)
            result.reinested.append(eid)
            logger.info("catch-up reinested %s (%s)", eid, item.reason)
        except Exception as exc:  # noqa: BLE001
            logger.exception("catch-up failed %s: %s", item.event_id, exc)
            result.failed.append({"event_id": item.event_id, "error": str(exc)})

    with db_session(db_path or DB_PATH) as conn:
        apply_all(conn)
        report_path, _data = write_report(conn, report_date=yesterday)
        result.report_path = str(report_path)
        result.report_regenerated = True
        logger.info("catch-up report written: %s", report_path)

    return result
