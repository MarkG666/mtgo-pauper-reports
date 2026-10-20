"""Ingest MTGO Challenge / Preliminary into SQLite."""

from __future__ import annotations

import json
import logging
import re
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

logger = logging.getLogger(__name__)

# slug example: pauper-challenge-32-2026-10-1412856206 → event_id 12856206
SLUG_EVENT_ID_RE = re.compile(r"(\d{8})$")


def event_id_from_slug(slug: str) -> str | None:
    m = SLUG_EVENT_ID_RE.search(slug)
    return m.group(1) if m else None


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
    return parsed.event_id


def run_ingest(
    *,
    max_events: int | None = None,
    force: bool = False,
    lookback: int = 8,
    db_path: Path | None = None,
    fetcher: MtgoFetcher | None = None,
    offline_listing: Path | None = None,
) -> dict:
    ensure_dirs()
    init_db(db_path)
    fetcher = fetcher or MtgoFetcher()

    if offline_listing:
        html = offline_listing.read_text(encoding="utf-8", errors="replace")
        listing_path = offline_listing
    else:
        html, listing_path = fetcher.fetch_listing(use_cache=False)

    events = extract_listing_events(html)
    if lookback:
        events = events[:lookback]
    if max_events is not None:
        events = events[:max_events]

    summary: dict = {
        "listing_path": str(listing_path),
        "candidates": len(events),
        "ingested": [],
        "skipped": [],
        "failed": [],
    }

    for le in events:
        guessed_id = event_id_from_slug(le.slug)
        try:
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

            eid = ingest_event_html(
                detail_html,
                source_url=le.url,
                raw_path=str(cache_path),
                fallback_type=le.event_type,
                db_path=db_path,
            )
            summary["ingested"].append(eid)
            logger.info("ingested event %s (%s)", eid, le.slug)
        except Exception as exc:  # noqa: BLE001
            logger.exception("failed %s: %s", le.slug, exc)
            summary["failed"].append({"slug": le.slug, "error": str(exc)})

    return summary
