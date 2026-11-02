"""Persistent queue of ingest failures so catch-up can retry next day.

When decklist HTML is not ready yet, listing ingest used to fail without
leaving a DB row. Catch-up then saw ``no events (JST)`` for that day and
never retried. This queue keeps ``event_id`` / URL until decks land or the
entry ages out.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .paths import INGEST_PENDING_PATH, ensure_dirs

log = logging.getLogger(__name__)

MAX_AGE_DAYS = 7


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


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
    """Upsert a pending failure (idempotent by event_id)."""
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
