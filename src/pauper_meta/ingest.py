"""Ingest MTGO Challenge / Preliminary into SQLite."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from pathlib import Path

from .classify import classify_deck
from .colors import color_identity_from_cards
from .db import (
    db_session,
    init_db,
    replace_decks_for_event,
    replace_matches_for_event,
    upsert_event,
    utc_now_iso,
)
from .fetch_mtgo import MtgoFetcher
from .overrides import (
    load_overrides_csv,
    recompute_top8_dist_for_event,
    seed_aliases_from_csv,
)
from .parse_mtgo import extract_decklists_data, extract_listing_events, parse_event
from .paths import DB_PATH, ensure_dirs
from .players import rebuild_player_event_results
from .ingest_pending import (
    clear_pending,
    is_benign_error,
    is_retryable_error,
    prune_resolved,
    prune_stale,
    register_failure,
)

logger = logging.getLogger(__name__)

# slug example: pauper-challenge-32-2026-10-1412856206 → event_id 12856206
SLUG_EVENT_ID_RE = re.compile(r"(\d{8})$")
# date in slug: pauper-challenge-16-2026-10-3112857391
SLUG_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")

# Same-job retries for transient MTGO HTML flakes (Actions-friendly sleep).
# 2 extras × ~45s keeps worst-case overhead small when complete events are skipped.
INGEST_SAME_DAY_RETRIES = 2
INGEST_RETRY_DELAY_SEC = 45.0


def event_id_from_slug(slug: str) -> str | None:
    m = SLUG_EVENT_ID_RE.search(slug)
    return m.group(1) if m else None


def slug_date_from_slug(slug: str) -> str | None:
    m = SLUG_DATE_RE.search(slug)
    return m.group(1) if m else None


def event_has_complete_decks(conn, event_id: str) -> bool:
    """True when DB already has decks that look finished for catch-up purposes.

    Lazy-imports catch-up incompleteness to avoid import cycles at module load.
    Falls back to ``deck_count > 0`` if the row is missing fields.
    """
    row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        return False
    deck_count = conn.execute(
        "SELECT COUNT(*) AS c FROM decks WHERE event_id = ?", (event_id,)
    ).fetchone()["c"]
    if int(deck_count) <= 0:
        return False
    try:
        from .catchup import event_incomplete_reason

        return event_incomplete_reason(conn, row) is None
    except Exception:  # noqa: BLE001
        return True


def upsert_event_stub(
    *,
    event_id: str,
    source_url: str,
    event_type: str | None,
    slug: str,
    db_path: Path | None = None,
) -> None:
    """Insert/update a zero-deck event shell so catch-up can see incomplete days.

    ``start_time`` uses the slug calendar date at 12:00 UTC as a placeholder
    until a full ingest overwrites it with the real MTGO start.
    """
    day = slug_date_from_slug(slug) or "1970-01-01"
    name = slug.replace("-", " ").title()
    with db_session(db_path or DB_PATH) as conn:
        existing = conn.execute(
            "SELECT COUNT(*) AS c FROM decks WHERE event_id = ?", (event_id,)
        ).fetchone()["c"]
        if int(existing) > 0:
            # Already have decks — do not clobber with a stub.
            return
        upsert_event(
            conn,
            {
                "id": event_id,
                "name": name,
                "start_time": f"{day} 12:00:00.0",
                "channel": "mtgo",
                "event_type": event_type or "challenge",
                "player_count": None,
                "source_url": source_url,
                "raw_path": None,
                "ingested_at": utc_now_iso(),
            },
        )


def ingest_event_html(
    html: str,
    *,
    source_url: str,
    raw_path: str | None,
    fallback_type: str | None = None,
    db_path: Path | None = None,
) -> str:
    raw = extract_decklists_data(html)
    parsed = parse_event(raw, fallback_type=fallback_type, source_url=source_url)
    with db_session(db_path or DB_PATH) as conn:
        existing_decks = conn.execute(
            "SELECT COUNT(*) AS c FROM decks WHERE event_id = ?",
            (parsed.event_id,),
        ).fetchone()["c"]
        if not parsed.decks and int(existing_decks) > 0:
            raise ValueError(
                f"refusing to overwrite event {parsed.event_id} "
                f"({existing_decks} decks) with empty decklists parse"
            )
        upsert_event(
            conn,
            {
                "id": parsed.event_id,
                "name": parsed.name,
                "start_time": parsed.start_time,
                "channel": "mtgo",
                "event_type": parsed.event_type,
                "player_count": parsed.player_count,
                "source_url": parsed.source_url or source_url,
                "raw_path": raw_path,
                "ingested_at": utc_now_iso(),
            },
        )
        decks_rows = []
        cards_rows = []
        for d in parsed.decks:
            main = Counter({c.name: c.qty for c in d.cards if c.board == "main"})
            arch = classify_deck(main).archetype
            color_id = color_identity_from_cards(
                [
                    {
                        "name": c.name,
                        "qty": c.qty,
                        "board": c.board,
                        "colors": c.colors,
                        "card_type": c.card_type,
                    }
                    for c in d.cards
                ]
            )
            decks_rows.append(
                {
                    "id": d.deck_id,
                    "event_id": parsed.event_id,
                    "player": d.player,
                    "loginid": d.loginid,
                    "finish_rank": d.finish_rank,
                    "swiss_rank": d.swiss_rank,
                    "archetype_auto": arch,
                    "archetype_final": arch,
                    "source_archetype_label": None,
                    "deck_index": d.deck_index,
                    "color_identity": color_id,
                    "swiss_wins": d.swiss_wins,
                    "swiss_losses": d.swiss_losses,
                }
            )
            # Merge duplicate card names; keep first-seen colors/type
            merged: dict[tuple[str, str], dict] = {}
            for c in d.cards:
                key = (c.board, c.name)
                if key not in merged:
                    merged[key] = {
                        "deck_id": d.deck_id,
                        "board": c.board,
                        "name": c.name,
                        "qty": c.qty,
                        "colors": ",".join(c.colors) if c.colors else None,
                        "card_type": c.card_type or None,
                    }
                else:
                    merged[key]["qty"] += c.qty
            cards_rows.extend(merged.values())

        replace_decks_for_event(conn, parsed.event_id, decks_rows, cards_rows)
        replace_matches_for_event(
            conn,
            parsed.event_id,
            [
                {
                    "event_id": parsed.event_id,
                    "round_index": m.round_index,
                    "round_label": m.round_label,
                    "player_a": m.player_a,
                    "player_b": m.player_b,
                    "wins_a": m.wins_a,
                    "wins_b": m.wins_b,
                    "winner": m.winner,
                }
                for m in parsed.matches
            ],
        )
        # Per-event finalize (aliases/overrides seeded; finals only for this event's decks)
        seed_aliases_from_csv(conn)
        load_overrides_csv(conn)
        from .overrides import alias_map, resolve_final

        aliases = alias_map(conn)
        overrides = {
            r["deck_id"]: r["canonical"]
            for r in conn.execute("SELECT deck_id, canonical FROM archetype_overrides").fetchall()
        }
        for row in conn.execute(
            "SELECT id, archetype_auto FROM decks WHERE event_id = ?",
            (parsed.event_id,),
        ).fetchall():
            final = resolve_final(row["archetype_auto"], aliases, overrides.get(row["id"]))
            conn.execute("UPDATE decks SET archetype_final = ? WHERE id = ?", (final, row["id"]))
        recompute_top8_dist_for_event(conn, parsed.event_id)
        rebuild_player_event_results(conn, parsed.event_id)
    if parsed.decks:
        clear_pending(parsed.event_id)
    return parsed.event_id


def _merge_listing_events(primary: list, extras: list) -> list:
    """Dedupe listing events by slug; keep primary order then append new."""
    seen: set[str] = set()
    out: list = []
    for le in list(primary) + list(extras):
        slug = getattr(le, "slug", None) or ""
        if not slug or slug in seen:
            continue
        seen.add(slug)
        out.append(le)
    return out


def _prune_pending_queues(db_path: Path | None = None) -> None:
    prune_stale()
    with db_session(db_path or DB_PATH) as conn:
        prune_resolved(conn, event_complete_fn=event_has_complete_decks)


def _ingest_one_event_attempt(
    *,
    le,
    guessed_id: str | None,
    force: bool,
    fetcher: MtgoFetcher,
    db_path: Path | None,
) -> str:
    """Single fetch+parse attempt for a listing event. Raises on failure."""
    if guessed_id:
        detail_html, cache_path = fetcher.fetch_event(le.url, guessed_id, force=force)
    else:
        url = le.url if le.url.endswith("/") else le.url + "/"
        detail_html = fetcher.get_text(url)
        raw = extract_decklists_data(detail_html)
        eid = str(raw["event_id"])
        cache_path = fetcher.raw_dir / f"event-{eid}.html"
        cache_path.write_text(detail_html, encoding="utf-8")
        (fetcher.raw_dir / f"event-{eid}.json").write_text(
            json.dumps(raw, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return ingest_event_html(
        detail_html,
        source_url=le.url,
        raw_path=str(cache_path),
        fallback_type=le.event_type,
        db_path=db_path,
    )


def ingest_with_same_day_retries(
    *,
    attempt_fn,
    label: str,
    retries: int = INGEST_SAME_DAY_RETRIES,
    delay_sec: float = INGEST_RETRY_DELAY_SEC,
) -> str:
    """Run ``attempt_fn``; on retryable errors sleep and retry up to ``retries`` times."""
    last_exc: BaseException | None = None
    total_tries = max(0, int(retries)) + 1
    for i in range(total_tries):
        try:
            return attempt_fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if is_benign_error(exc):
                raise
            if i + 1 >= total_tries or not is_retryable_error(exc):
                raise
            wait = max(0.0, float(delay_sec))
            logger.warning(
                "ingest retry %s/%s for %s after %.0fs (%s)",
                i + 1,
                retries,
                label,
                wait,
                exc,
            )
            if wait:
                time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def run_ingest(
    *,
    max_events: int | None = None,
    force: bool = False,
    lookback: int = 8,
    db_path: Path | None = None,
    fetcher: MtgoFetcher | None = None,
    offline_listing: Path | None = None,
    same_day_retries: int = INGEST_SAME_DAY_RETRIES,
    retry_delay_sec: float = INGEST_RETRY_DELAY_SEC,
) -> dict:
    ensure_dirs()
    init_db(db_path)
    fetcher = fetcher or MtgoFetcher()

    if offline_listing:
        html = offline_listing.read_text(encoding="utf-8", errors="replace")
        listing_path = offline_listing
        events = extract_listing_events(html)
    else:
        # Main Pauper filter page often shows only the newest month (sometimes
        # a single event). Also pull current + previous monthly listings so
        # lookback still covers last weekend when the landing page is thin.
        html, listing_path = fetcher.fetch_listing(use_cache=False)
        events = extract_listing_events(html)
        from datetime import date as _date

        today = _date.today()
        months = [(today.year, today.month)]
        if today.month == 1:
            months.append((today.year - 1, 12))
        else:
            months.append((today.year, today.month - 1))
        for year, month in months:
            try:
                mhtml, _mp = fetcher.fetch_monthly_listing(year, month, use_cache=False)
                events = _merge_listing_events(events, extract_listing_events(mhtml))
            except Exception as exc:  # noqa: BLE001
                logger.warning("monthly listing %04d-%02d failed: %s", year, month, exc)

    if lookback:
        events = events[:lookback]
    if max_events is not None:
        events = events[:max_events]

    _prune_pending_queues(db_path)

    summary: dict = {
        "listing_path": str(listing_path),
        "candidates": len(events),
        "ingested": [],
        "skipped": [],
        "failed": [],
    }

    for le in events:
        guessed_id = event_id_from_slug(le.slug)
        # Lookback re-fetch of already-complete events is the main source of
        # false Discord alerts (refuse-overwrite / missing decklists.data).
        if guessed_id and not force:
            with db_session(db_path or DB_PATH) as conn:
                if event_has_complete_decks(conn, guessed_id):
                    clear_pending(guessed_id)
                    summary["skipped"].append(
                        {"event_id": guessed_id, "slug": le.slug, "reason": "already_complete"}
                    )
                    logger.info("skip already-complete event %s (%s)", guessed_id, le.slug)
                    continue
        try:
            eid = ingest_with_same_day_retries(
                attempt_fn=lambda: _ingest_one_event_attempt(
                    le=le,
                    guessed_id=guessed_id,
                    force=force,
                    fetcher=fetcher,
                    db_path=db_path,
                ),
                label=le.slug,
                retries=same_day_retries,
                delay_sec=retry_delay_sec,
            )
            summary["ingested"].append(eid)
            logger.info("ingested event %s (%s)", eid, le.slug)
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            # Protective refuse: DB already has decks — treat as soft success.
            if is_benign_error(exc) and guessed_id:
                clear_pending(guessed_id)
                summary["skipped"].append(
                    {
                        "event_id": guessed_id,
                        "slug": le.slug,
                        "reason": "refuse_overwrite_kept_existing",
                        "error": err,
                    }
                )
                logger.warning("kept existing decks for %s (%s)", guessed_id, err)
                continue
            # HTML flake on an event that somehow still looks complete.
            if guessed_id:
                with db_session(db_path or DB_PATH) as conn:
                    already_ok = event_has_complete_decks(conn, guessed_id)
                if already_ok:
                    clear_pending(guessed_id)
                    summary["skipped"].append(
                        {
                            "event_id": guessed_id,
                            "slug": le.slug,
                            "reason": "complete_despite_fetch_error",
                            "error": err,
                            "benign": True,
                        }
                    )
                    logger.warning(
                        "ignore fetch error for already-complete %s: %s", guessed_id, err
                    )
                    continue
            logger.exception("failed %s: %s", le.slug, exc)
            fail_item = {
                "slug": le.slug,
                "url": le.url,
                "event_id": guessed_id,
                "error": err,
            }
            summary["failed"].append(fail_item)
            if guessed_id:
                register_failure(
                    event_id=guessed_id,
                    slug=le.slug,
                    url=le.url,
                    event_type=le.event_type,
                    error=err,
                )
                # Shell row so catch-up / incomplete scans still see the day.
                try:
                    upsert_event_stub(
                        event_id=guessed_id,
                        source_url=le.url,
                        event_type=le.event_type,
                        slug=le.slug,
                        db_path=db_path,
                    )
                except Exception as stub_exc:  # noqa: BLE001
                    logger.warning("stub upsert failed for %s: %s", guessed_id, stub_exc)

    return summary


def retry_pending_ingests(
    *,
    db_path: Path | None = None,
    fetcher: MtgoFetcher | None = None,
    same_day_retries: int = INGEST_SAME_DAY_RETRIES,
    retry_delay_sec: float = INGEST_RETRY_DELAY_SEC,
) -> dict:
    """Force-refetch every ``ingest_pending`` entry. Clears successes."""
    from .ingest_pending import load_pending

    ensure_dirs()
    init_db(db_path)
    fetcher = fetcher or MtgoFetcher()
    _prune_pending_queues(db_path)
    pending = load_pending()
    summary: dict = {"candidates": len(pending), "ingested": [], "failed": [], "skipped": []}
    for item in pending:
        eid = str(item["event_id"])
        url = (item.get("url") or "").strip()
        slug = item.get("slug") or eid
        if not url:
            summary["failed"].append({"event_id": eid, "error": "missing url"})
            continue
        with db_session(db_path or DB_PATH) as conn:
            if event_has_complete_decks(conn, eid):
                clear_pending(eid)
                summary["skipped"].append(
                    {"event_id": eid, "slug": slug, "reason": "already_complete"}
                )
                logger.info("pending skip already-complete %s", eid)
                continue
        try:

            def _once() -> str:
                html, cache_path = fetcher.fetch_event(url, eid, force=True)
                got = ingest_event_html(
                    html,
                    source_url=url,
                    raw_path=str(cache_path),
                    fallback_type=item.get("event_type"),
                    db_path=db_path,
                )
                with db_session(db_path or DB_PATH) as conn:
                    n = conn.execute(
                        "SELECT COUNT(*) AS c FROM decks WHERE event_id = ?", (got,)
                    ).fetchone()["c"]
                if int(n) == 0:
                    raise ValueError("decklists still empty after pending retry")
                return got

            got = ingest_with_same_day_retries(
                attempt_fn=_once,
                label=slug,
                retries=same_day_retries,
                delay_sec=retry_delay_sec,
            )
            summary["ingested"].append(got)
            logger.info("pending retry ingested %s (%s)", got, slug)
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            if is_benign_error(exc):
                clear_pending(eid)
                summary["skipped"].append(
                    {
                        "event_id": eid,
                        "slug": slug,
                        "reason": "refuse_overwrite_kept_existing",
                        "error": err,
                    }
                )
                continue
            with db_session(db_path or DB_PATH) as conn:
                already_ok = event_has_complete_decks(conn, eid)
            if already_ok:
                clear_pending(eid)
                summary["skipped"].append(
                    {
                        "event_id": eid,
                        "slug": slug,
                        "reason": "complete_despite_fetch_error",
                        "error": err,
                        "benign": True,
                    }
                )
                continue
            logger.warning("pending retry failed %s: %s", eid, exc)
            summary["failed"].append({"event_id": eid, "slug": slug, "url": url, "error": err})
            register_failure(
                event_id=eid,
                slug=slug,
                url=url,
                event_type=item.get("event_type"),
                error=err,
            )
    return summary
