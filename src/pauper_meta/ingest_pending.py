"""Persistent queue of ingest failures so catch-up can retry next day.

When decklist HTML is not ready yet, listing ingest used to fail without
leaving a DB row. Catch-up then saw ``no events (JST)`` for that day and
never retried. This queue keeps ``event_id`` / URL until decks land or the
entry ages out.

Same-day ingest also retries transient HTML flakes a few times; this queue
remains the next-day safety net.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .paths import INGEST_PENDING_PATH, ensure_dirs

log = logging.getLogger(__name__)

MAX_AGE_DAYS = 7

# Errors that are worth sleeping + re-fetching in the same Actions job.
RETRYABLE_ERROR_MARKERS = (
    "window.MTGO.decklists.data not found",
    "decklists still empty",
    "empty decklists",
    "Read timed out",
    "timed out",
    "Timeout",
    "ConnectionError",
    "Connection reset",
    "RemoteDisconnected",
    "ChunkedEncodingError",
    "503",
    "502",
    "504",
    "500",
)

# Protective / already-have-data — not an ops alert.
BENIGN_ERROR_MARKERS = (
    "refusing to overwrite",
)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def is_retryable_error(error: str | BaseException) -> bool:
    text = str(error)
    if is_benign_error(text):
        return False
    return any(m in text for m in RETRYABLE_ERROR_MARKERS)


def is_benign_error(error: str | BaseException) -> bool:
    """True for protective refuses that must not page ops Discord."""
    text = str(error)
    return any(m in text for m in BENIGN_ERROR_MARKERS)


def is_alertable_failure(item: dict[str, Any]) -> bool:
    """False for refuse-overwrite / already-complete lookback flakes."""
    if item.get("benign") or item.get("skip_alert"):
        return False
    err = str(item.get("error") or "")
    if is_benign_error(err):
        return False
    return True


def filter_alertable_failures(failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [f for f in failures if is_alertable_failure(f)]


def load_pending(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or INGEST_PENDING_PATH
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict) and x.get("event_id")]


def save_pending(items: list[dict[str, Any]], path: Path | None = None) -> None:
    ensure_dirs()
    p = path or INGEST_PENDING_PATH
    p.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def register_failure(
    *,
    event_id: str,
    slug: str,
    url: str,
    event_type: str | None,
    error: str,
    path: Path | None = None,
) -> None:
    """Upsert a pending failure (idempotent by event_id).

    Benign protective errors (refuse-overwrite) are not queued.
    """
    if is_benign_error(error):
        log.info(
            "ingest_pending skip register (benign) event_id=%s error=%s",
            event_id,
            str(error)[:120],
        )
        clear_pending(event_id, path=path)
        return
    items = load_pending(path)
    now = _now_iso()
    found = False
    for item in items:
        if str(item.get("event_id")) == str(event_id):
            item["slug"] = slug
            item["url"] = url
            item["event_type"] = event_type or item.get("event_type") or "challenge"
            item["last_error"] = error[:500]
            item["last_failed_at"] = now
            item["attempts"] = int(item.get("attempts") or 0) + 1
            found = True
            break
    if not found:
        items.append(
            {
                "event_id": str(event_id),
                "slug": slug,
                "url": url,
                "event_type": event_type or "challenge",
                "first_failed_at": now,
                "last_failed_at": now,
                "last_error": error[:500],
                "attempts": 1,
            }
        )
    save_pending(items, path)
    log.info("ingest_pending registered event_id=%s slug=%s", event_id, slug)


def clear_pending(event_id: str, path: Path | None = None) -> bool:
    items = load_pending(path)
    keep = [x for x in items if str(x.get("event_id")) != str(event_id)]
    if len(keep) == len(items):
        return False
    save_pending(keep, path)
    log.info("ingest_pending cleared event_id=%s", event_id)
    return True


def prune_stale(path: Path | None = None, *, max_age_days: int = MAX_AGE_DAYS) -> int:
    """Drop entries older than ``max_age_days`` (by first_failed_at)."""
    items = load_pending(path)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max_age_days)
    keep: list[dict[str, Any]] = []
    dropped = 0
    for item in items:
        raw = item.get("first_failed_at") or item.get("last_failed_at") or ""
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            keep.append(item)
            continue
        if ts < cutoff:
            dropped += 1
            log.info("ingest_pending prune stale event_id=%s", item.get("event_id"))
            continue
        keep.append(item)
    if dropped:
        save_pending(keep, path)
    return dropped


def prune_resolved(
    conn: sqlite3.Connection,
    path: Path | None = None,
    *,
    event_complete_fn=None,
) -> int:
    """Drop pending rows whose event already has usable decks in DB.

    ``event_complete_fn(conn, event_id) -> bool`` defaults to deck_count > 0.
    Prefer passing catch-up completeness when available.
    """
    items = load_pending(path)
    if not items:
        return 0
    keep: list[dict[str, Any]] = []
    dropped = 0
    for item in items:
        eid = str(item.get("event_id") or "")
        if not eid:
            continue
        if event_complete_fn is not None:
            try:
                resolved = bool(event_complete_fn(conn, eid))
            except Exception as exc:  # noqa: BLE001
                log.debug("prune_resolved complete check failed %s: %s", eid, exc)
                resolved = False
        else:
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM decks WHERE event_id = ?", (eid,)
            ).fetchone()["c"]
            resolved = int(n) > 0
        if resolved:
            dropped += 1
            log.info("ingest_pending prune resolved event_id=%s", eid)
            continue
        keep.append(item)
    if dropped:
        save_pending(keep, path)
    return dropped
