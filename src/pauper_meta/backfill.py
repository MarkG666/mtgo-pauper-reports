"""Historical Challenge/Preliminary ingest via monthly mtgo.com listings."""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from .db import db_session, init_db, list_event_ids
from .fetch_mtgo import MtgoFetcher
from .ingest import event_id_from_slug, ingest_event_html
from .parse_mtgo import extract_listing_events
from .paths import DB_PATH, ensure_dirs

logger = logging.getLogger(__name__)

SLUG_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _slug_date(slug: str) -> date | None:
    m = SLUG_DATE_RE.search(slug)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def iter_year_months(since: date, until: date) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    y, m = since.year, since.month
    while (y, m) <= (until.year, until.month):
        out.append((y, m))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


def collect_listing_events_since(
    fetcher: MtgoFetcher,
    since: date,
    until: date | None = None,
) -> list:
    until = until or date.today()
    seen: set[str] = set()
    events = []
    for y, m in iter_year_months(since, until):
        html, path = fetcher.fetch_monthly_listing(y, m, use_cache=True)
        logger.info("listing %04d-%02d → %s", y, m, path)
        for le in extract_listing_events(html):
            if le.slug in seen:
                continue
            sd = _slug_date(le.slug)
            if sd is None or sd < since or sd > until:
                continue
            seen.add(le.slug)
            events.append(le)
    # Oldest first for stable backfill progress
    events.sort(key=lambda e: (_slug_date(e.slug) or date.min, e.slug))
    return events


def run_backfill(
    *,
    since: date,
    until: date | None = None,
    force: bool = False,
    max_events: int | None = None,
    skip_existing: bool = True,
    db_path: Path | None = None,
    fetcher: MtgoFetcher | None = None,
) -> dict:
    """
    Idempotent ingest of Challenge + Preliminary from monthly listings since `since`.
    Uses mtgo.com `?filter=Pauper&month=&year=` (no CF bypass).
    """
    ensure_dirs()
    init_db(db_path)
    fetcher = fetcher or MtgoFetcher()
    until = until or date.today()

    events = collect_listing_events_since(fetcher, since, until)
    if max_events is not None:
        events = events[:max_events]

    with db_session(db_path or DB_PATH) as conn:
        existing = list_event_ids(conn) if skip_existing else set()

    summary: dict = {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "candidates": len(events),
        "ingested": [],
        "skipped_existing": [],
        "failed": [],
        "months_scanned": iter_year_months(since, until),
    }

    for le in events:
        guessed_id = event_id_from_slug(le.slug)
        if skip_existing and guessed_id and guessed_id in existing and not force:
            summary["skipped_existing"].append(guessed_id)
            continue
        try:
            if guessed_id:
                detail_html, cache_path = fetcher.fetch_event(le.url, guessed_id, force=force)
            else:
                url = le.url if le.url.endswith("/") else le.url + "/"
                detail_html = fetcher.get_text(url)
                from .parse_mtgo import extract_decklists_data
                import json

                raw = extract_decklists_data(detail_html)
                guessed_id = str(raw["event_id"])
                cache_path = fetcher.raw_dir / f"event-{guessed_id}.html"
                cache_path.write_text(detail_html, encoding="utf-8")
                (fetcher.raw_dir / f"event-{guessed_id}.json").write_text(
                    json.dumps(raw, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            if skip_existing and guessed_id in existing and not force:
                summary["skipped_existing"].append(guessed_id)
                continue

            eid = ingest_event_html(
                detail_html,
                source_url=le.url,
                raw_path=str(cache_path),
                fallback_type=le.event_type,
                db_path=db_path,
            )
            summary["ingested"].append(eid)
            existing.add(eid)
            logger.info("backfill ingested %s (%s)", eid, le.slug)
        except Exception as exc:  # noqa: BLE001
            logger.exception("backfill failed %s: %s", le.slug, exc)
            summary["failed"].append({"slug": le.slug, "error": str(exc)})

    return summary
