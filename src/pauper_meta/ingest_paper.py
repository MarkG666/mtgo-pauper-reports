"""Ingest Paper (MTGDecks) events into SQLite with channel=paper."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .db import (
    db_session,
    event_exists,
    init_db,
    replace_decks_for_event,
    upsert_event,
    utc_now_iso,
)
from .fetch_mtgdecks import CloudflareBlockedError, MtgdecksFetcher
from .overrides import apply_all, alias_map, resolve_final
from .parse_mtgdecks import (
    ListingEvent,
    PaperDeckRow,
    PaperEventParsed,
    event_id_from_path,
    filter_large_events,
    parse_event_page,
    parse_listing_events,
)
from .paths import CONFIG_DIR, DB_PATH, FIXTURES_DIR, ensure_dirs

logger = logging.getLogger(__name__)

DEFAULT_PAPER_CONFIG: dict[str, Any] = {
    "min_players": 50,
    "listing_url": "https://mtgdecks.net/Pauper/tournaments/platform:tabletop",
    "base_url": "https://mtgdecks.net",
    "fetch_mode": "playwright",
    "playwright_timeout_ms": 60_000,
    "playwright_wait_sec": 12,
    "skip_mtgo_named": True,
    "skip_unknown_player_count": True,
    "lookback_listing_rows": 80,
}


def load_paper_config(path: Path | None = None) -> dict[str, Any]:
    ensure_dirs()
    cfg_path = path or (CONFIG_DIR / "paper.json")
    out = dict(DEFAULT_PAPER_CONFIG)
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                out.update(data)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            logger.warning("failed to load paper config %s; using defaults", cfg_path)
    return out


def _deck_id(event_id: str, finish_rank: int, player: str) -> str:
    slug = re.sub(r"[^\w]+", "_", player.strip().lower()).strip("_")[:40] or "player"
    return f"paper-{event_id}-{finish_rank:02d}-{slug}"


def resolve_paper_archetype(label: str, aliases: dict[str, str]) -> str:
    """Site label → aliases → keep label (no card list required for spike Top8)."""
    raw = (label or "").strip() or "Unknown"
    return resolve_final(raw, aliases, None)


def ingest_parsed_event(
    parsed: PaperEventParsed,
    *,
    raw_path: str | None = None,
    db_path: Path | None = None,
) -> str:
    """Upsert one paper event + Top8-ish decks (cards empty in spike)."""
    init_db(db_path)
    with db_session(db_path or DB_PATH) as conn:
        # Ensure aliases available for label resolve
        from .overrides import seed_aliases_from_csv

        seed_aliases_from_csv(conn)
        aliases = alias_map(conn)

        upsert_event(
            conn,
            {
                "id": f"paper-{parsed.event_id}",
                "name": parsed.name,
                "start_time": parsed.start_time,
                "channel": "paper",
                "event_type": "paper",
                "player_count": parsed.player_count,
                "source_url": parsed.source_url,
                "raw_path": raw_path,
                "ingested_at": utc_now_iso(),
            },
        )
        decks_rows: list[dict[str, Any]] = []
        for d in parsed.decks:
            arch = resolve_paper_archetype(d.archetype_label, aliases)
            decks_rows.append(
                {
                    "id": _deck_id(parsed.event_id, d.finish_rank, d.player),
                    "event_id": f"paper-{parsed.event_id}",
                    "player": d.player,
                    "loginid": None,
                    "finish_rank": d.finish_rank,
                    "swiss_rank": None,
                    "archetype_auto": arch,
                    "archetype_final": arch,
                    "source_archetype_label": d.archetype_label,
                    "deck_index": d.finish_rank,
                    "color_identity": None,
                    "swiss_wins": None,
                    "swiss_losses": None,
                    # stash MTGDecks URL in a side channel via source label note? keep URL in report via source_url join
                    "_deck_url": d.deck_url,
                }
            )
        # Store deck URLs in a companion JSON next to raw cache (report reads from events.source_url + parse)
        # For DB simplicity, encode deck_url into loginid field temporarily is bad.
        # Prefer: write paper deck meta JSON file keyed by event.
        clean_decks = [{k: v for k, v in row.items() if not k.startswith("_")} for row in decks_rows]
        replace_decks_for_event(conn, f"paper-{parsed.event_id}", clean_decks, cards=[])
        # Keep site-label archetype_auto; empty card lists must not reclassify to Unknown.
        apply_all(conn, reclassify=False)

    meta_path = _paper_meta_path(parsed.event_id, db_path)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "event_id": parsed.event_id,
        "db_event_id": f"paper-{parsed.event_id}",
        "source_url": parsed.source_url,
        "decks": [
            {
                "finish_rank": d.finish_rank,
                "place_label": d.place_label,
                "player": d.player,
                "archetype_label": d.archetype_label,
                "deck_url": d.deck_url,
                "deck_name": d.deck_name,
            }
            for d in parsed.decks
        ],
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"paper-{parsed.event_id}"


def _paper_meta_path(event_id: str, db_path: Path | None = None) -> Path:
    from .paths import DATA_DIR

    root = (db_path.parent if db_path else DATA_DIR)
    return root / "paper_meta" / f"{event_id}.json"


def load_paper_meta(event_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    path = _paper_meta_path(event_id.removeprefix("paper-"), db_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def ingest_event_html(
    html: str,
    *,
    event_id: str,
    source_url: str,
    raw_path: str | None = None,
    fallback_name: str | None = None,
    fallback_players: int | None = None,
    base_url: str = "https://mtgdecks.net",
    db_path: Path | None = None,
) -> str:
    parsed = parse_event_page(
        html,
        event_id=event_id,
        source_url=source_url,
        base_url=base_url,
        fallback_name=fallback_name,
        fallback_players=fallback_players,
    )
    return ingest_parsed_event(parsed, raw_path=raw_path, db_path=db_path)


def run_paper_scan(
    *,
    offline_listing: Path | None = None,
    offline_events_dir: Path | None = None,
    force: bool = False,
    max_events: int | None = None,
    db_path: Path | None = None,
    config: dict[str, Any] | None = None,
    try_live: bool = True,
) -> dict[str, Any]:
    """Scan listing → filter ≥min_players → ingest new paper events.

    Live Playwright is attempted when ``try_live`` and no offline listing is set.
    On Cloudflare block, returns ``cf_blocked=True`` and ingests any offline drops.
    """
    ensure_dirs()
    init_db(db_path)
    cfg = config or load_paper_config()
    base_url = str(cfg.get("base_url") or "https://mtgdecks.net")
    min_players = int(cfg.get("min_players") or 50)
    summary: dict[str, Any] = {
        "candidates": 0,
        "ingested": [],
        "skipped_existing": [],
        "failed": [],
        "cf_blocked": False,
        "fetch_method": None,
        "listing_events": 0,
        "large_events": 0,
    }

    fetcher = MtgdecksFetcher(
        timeout_ms=int(cfg.get("playwright_timeout_ms") or 60_000),
        wait_sec=float(cfg.get("playwright_wait_sec") or 12),
    )

    listing_html: str | None = None
    if offline_listing:
        result = fetcher.load_offline(Path(offline_listing))
        listing_html = result.html
        summary["fetch_method"] = "offline"
    elif try_live:
        try:
            result = fetcher.fetch_playwright(
                str(cfg["listing_url"]),
                "listing-tabletop.html",
            )
            listing_html = result.html
            summary["fetch_method"] = result.method
        except CloudflareBlockedError as e:
            logger.warning("listing CF blocked: %s", e)
            summary["cf_blocked"] = True
            summary["fetch_method"] = "playwright-blocked"
        except Exception as e:
            logger.exception("listing fetch failed: %s", e)
            summary["cf_blocked"] = True
            summary["fetch_method"] = f"error:{type(e).__name__}"
            summary["failed"].append({"stage": "listing", "error": str(e)})

    large: list[ListingEvent] = []
    if listing_html:
        events = parse_listing_events(
            listing_html,
            base_url=base_url,
            skip_mtgo_named=bool(cfg.get("skip_mtgo_named", True)),
        )
        summary["listing_events"] = len(events)
        large = filter_large_events(
            events,
            min_players=min_players,
            skip_unknown_player_count=bool(cfg.get("skip_unknown_player_count", True)),
        )
        lookback = int(cfg.get("lookback_listing_rows") or 80)
        large = large[:lookback]
        summary["large_events"] = len(large)

    # Manual / fixture event drops: HTML files named event-<id>-*.html
    drops_dir = Path(offline_events_dir) if offline_events_dir else (FIXTURES_DIR / "mtgdecks")
    drop_map: dict[str, Path] = {}
    if drops_dir.exists():
        for p in sorted(drops_dir.glob("event-*.html")):
            m = re.search(r"event-(\d+)", p.name)
            if m:
                drop_map[m.group(1)] = p

    # If listing empty/blocked, still process fixture/manual drops as synthetic candidates
    if not large and drop_map:
        for eid, path in drop_map.items():
            large.append(
                ListingEvent(
                    event_id=eid,
                    name=f"fixture-{eid}",
                    path=f"/Pauper/fixture-tournament-{eid}",
                    source_url=f"{base_url}/Pauper/fixture-tournament-{eid}",
                    player_count=max(min_players, 50),
                )
            )
        summary["large_events"] = len(large)
        summary["fetch_method"] = summary["fetch_method"] or "fixtures-only"

    summary["candidates"] = len(large)
    if max_events is not None:
        large = large[: max(0, max_events)]

    for ev in large:
        db_eid = f"paper-{ev.event_id}"
        with db_session(db_path or DB_PATH) as conn:
            exists = event_exists(conn, db_eid)
        if exists and not force:
            summary["skipped_existing"].append(db_eid)
            continue

        html: str | None = None
        raw_path: str | None = None
        if ev.event_id in drop_map:
            fr = fetcher.load_offline(drop_map[ev.event_id])
            html, raw_path = fr.html, str(fr.path)
        elif try_live and not summary["cf_blocked"]:
            try:
                fr = fetcher.fetch_playwright(
                    ev.source_url,
                    f"event-{ev.event_id}.html",
                )
                html, raw_path = fr.html, str(fr.path)
            except CloudflareBlockedError as e:
                logger.warning("event CF blocked %s: %s", ev.event_id, e)
                summary["cf_blocked"] = True
                summary["failed"].append({"event_id": ev.event_id, "error": "cloudflare"})
                continue
            except Exception as e:
                logger.exception("event fetch failed %s", ev.event_id)
                summary["failed"].append({"event_id": ev.event_id, "error": str(e)})
                continue
        else:
            summary["failed"].append(
                {
                    "event_id": ev.event_id,
                    "error": "no offline HTML and live fetch unavailable/blocked",
                }
            )
            continue

        try:
            db_id = ingest_event_html(
                html,
                event_id=ev.event_id,
                source_url=ev.source_url,
                raw_path=raw_path,
                fallback_name=ev.name,
                fallback_players=ev.player_count,
                base_url=base_url,
                db_path=db_path,
            )
            summary["ingested"].append(db_id)
            logger.info("ingested paper event %s (%s)", db_id, ev.name)
        except Exception as e:
            logger.exception("ingest failed %s", ev.event_id)
            summary["failed"].append({"event_id": ev.event_id, "error": str(e)})

    return summary


def parse_paste_json(data: dict[str, Any], *, base_url: str = "https://mtgdecks.net") -> PaperEventParsed:
    """Minimal structured paste → PaperEventParsed (manual handoff escape hatch)."""
    event_id = str(data.get("event_id") or "").strip()
    source_url = str(data.get("source_url") or "").strip()
    if not event_id and source_url:
        event_id = event_id_from_path(source_url) or ""
    if not event_id:
        raise ValueError("paste JSON needs event_id or source_url containing tournament-<id>")
    if not source_url:
        source_url = f"{base_url.rstrip('/')}/Pauper/manual-tournament-{event_id}"

    decks: list[PaperDeckRow] = []
    raw_decks = data.get("decks") or []
    if not isinstance(raw_decks, list):
        raise ValueError("paste JSON decks must be a list")
    for i, row in enumerate(raw_decks, start=1):
        if not isinstance(row, dict):
            continue
        rank = int(row.get("finish_rank") or i)
        deck_url = str(row.get("deck_url") or "").strip()
        deck_path = deck_url
        decks.append(
            PaperDeckRow(
                finish_rank=rank,
                place_label=str(row.get("place_label") or f"{rank}"),
                player=str(row.get("player") or f"player-{rank}").strip(),
                archetype_label=str(row.get("archetype_label") or "Unknown").strip(),
                deck_name=str(row.get("deck_name") or row.get("archetype_label") or "").strip(),
                deck_path=deck_path,
                deck_url=deck_url,
            )
        )

    pc = data.get("player_count")
    player_count = int(pc) if pc is not None and str(pc).strip() != "" else None
    start = data.get("start_time")
    start_time = str(start).strip() if start else None
    return PaperEventParsed(
        event_id=event_id,
        name=str(data.get("name") or f"manual-{event_id}").strip(),
        player_count=player_count,
        start_time=start_time,
        source_url=source_url,
        decks=decks,
    )


def run_paper_manual(
    *,
    urls: list[str] | None = None,
    html_paths: list[Path] | None = None,
    html_dir: Path | None = None,
    paste_paths: list[Path] | None = None,
    source_url_for_html: str | None = None,
    force: bool = False,
    force_small: bool = False,
    try_live: bool = True,
    db_path: Path | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Manual URL / HTML / paste handoff into the same paper ingest path.

    Live fetch is best-effort (often Cloudflare-blocked). Prefer saved HTML.
    """
    ensure_dirs()
    init_db(db_path)
    cfg = config or load_paper_config()
    base_url = str(cfg.get("base_url") or "https://mtgdecks.net")
    min_players = int(cfg.get("min_players") or 50)
    summary: dict[str, Any] = {
        "candidates": 0,
        "ingested": [],
        "skipped_existing": [],
        "skipped_small": [],
        "failed": [],
        "cf_blocked": False,
        "fetch_method": "manual",
        "notes": ["手動 URL / HTML ハンドオフ"],
    }

    fetcher = MtgdecksFetcher(
        timeout_ms=int(cfg.get("playwright_timeout_ms") or 60_000),
        wait_sec=float(cfg.get("playwright_wait_sec") or 12),
    )

    # Collect work items: (event_id, source_url, html|None, paste|None, raw_path|None)
    work: list[dict[str, Any]] = []

    def _add_html(path: Path, source_url: str | None) -> None:
        html = path.read_text(encoding="utf-8", errors="replace")
        eid = event_id_from_path(source_url or "") or None
        if not eid:
            m = re.search(r"event-(\d+)", path.name)
            eid = m.group(1) if m else None
        if not eid:
            eid = event_id_from_path(html) or None
        if not eid:
            summary["failed"].append({"path": str(path), "error": "could not resolve event_id"})
            return
        url = (source_url or "").strip() or f"{base_url.rstrip('/')}/Pauper/manual-tournament-{eid}"
        work.append(
            {
                "event_id": eid,
                "source_url": url,
                "html": html,
                "raw_path": str(path),
                "paste": None,
            }
        )

    for p in html_paths or []:
        _add_html(Path(p), source_url_for_html)

    if html_dir is not None:
        d = Path(html_dir)
        if d.exists():
            for p in sorted(d.glob("event-*.html")):
                _add_html(p, None)

    for paste_path in paste_paths or []:
        path = Path(paste_path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("paste root must be an object")
            parsed = parse_paste_json(data, base_url=base_url)
            work.append(
                {
                    "event_id": parsed.event_id,
                    "source_url": parsed.source_url,
                    "html": None,
                    "raw_path": str(path),
                    "paste": parsed,
                }
            )
        except Exception as e:
            summary["failed"].append({"path": str(path), "error": f"paste: {e}"})

    for url in urls or []:
        url = url.strip()
        if not url:
            continue
        eid = event_id_from_path(url)
        if not eid:
            summary["failed"].append({"url": url, "error": "URL missing tournament-<id>"})
            continue
        # Skip if already queued from HTML
        if any(w["event_id"] == eid and w.get("html") for w in work):
            continue
        html: str | None = None
        raw_path: str | None = None
        if try_live:
            try:
                fr = fetcher.fetch_playwright(url, f"event-{eid}.html")
                html, raw_path = fr.html, str(fr.path)
                summary["fetch_method"] = "manual+playwright"
            except CloudflareBlockedError as e:
                logger.warning("manual URL CF blocked %s: %s", eid, e)
                summary["cf_blocked"] = True
                summary["failed"].append(
                    {
                        "event_id": eid,
                        "url": url,
                        "error": "cloudflare; save HTML and pass --html",
                    }
                )
                continue
            except Exception as e:
                logger.exception("manual URL fetch failed %s", eid)
                summary["failed"].append({"event_id": eid, "url": url, "error": str(e)})
                continue
        else:
            summary["failed"].append(
                {
                    "event_id": eid,
                    "url": url,
                    "error": "live fetch disabled; pass --html or --paste",
                }
            )
            continue
        work.append(
            {
                "event_id": eid,
                "source_url": url,
                "html": html,
                "raw_path": raw_path,
                "paste": None,
            }
        )

    # Dedupe by event_id (prefer html/paste already loaded)
    by_id: dict[str, dict[str, Any]] = {}
    for item in work:
        by_id[item["event_id"]] = item
    items = list(by_id.values())
    summary["candidates"] = len(items)

    for item in items:
        eid = item["event_id"]
        db_eid = f"paper-{eid}"
        with db_session(db_path or DB_PATH) as conn:
            exists = event_exists(conn, db_eid)
        if exists and not force:
            summary["skipped_existing"].append(db_eid)
            continue

        try:
            if item.get("paste") is not None:
                parsed = item["paste"]
            else:
                parsed = parse_event_page(
                    item["html"],
                    event_id=eid,
                    source_url=item["source_url"],
                    base_url=base_url,
                )
            pc = parsed.player_count
            if not force_small and (pc is None or pc < min_players):
                summary["skipped_small"].append(
                    {"event_id": eid, "player_count": pc, "min_players": min_players}
                )
                continue
            db_id = ingest_parsed_event(
                parsed,
                raw_path=item.get("raw_path"),
                db_path=db_path,
            )
            summary["ingested"].append(db_id)
            logger.info("manual ingested %s (%s)", db_id, parsed.name)
        except Exception as e:
            logger.exception("manual ingest failed %s", eid)
            summary["failed"].append({"event_id": eid, "error": str(e)})

    return summary
