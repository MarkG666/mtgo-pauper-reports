"""CLI entry: run-daily / run-paper / run-paper-manual / apply-overrides / report / init-db / player-stats / discord-*."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from .backfill import run_backfill
from .catchup import run_yesterday_catchup
from .db import db_session, init_db
from .discord_summary import generate_and_maybe_post, post_paper_events_if_new
from .ingest import run_ingest
from .ingest_paper import load_paper_config, run_paper_manual, run_paper_scan
from .overrides import apply_all
from .paths import DB_PATH, FIXTURES_DIR, LOGS_DIR, PROJECT_ROOT, ensure_dirs
from .players import (
    format_player_stats_table,
    load_player_stats_config,
    query_frequent_players,
    rebuild_player_event_results,
)
from .report_daily import today_jst, write_report
from .report_paper import write_weekly_report


def _setup_logging(report_date: str) -> Path:
    ensure_dirs()
    log_path = LOGS_DIR / f"daily-{report_date}.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(sh)
    root.addHandler(fh)
    return log_path


def cmd_init_db(_args: argparse.Namespace) -> int:
    path = init_db()
    print(f"initialized: {path}")
    return 0


def cmd_run_daily(args: argparse.Namespace) -> int:
    ensure_dirs()
    today = today_jst()
    rd = today.isoformat()
    log_path = _setup_logging(rd)
    logging.info("run-daily start (JST date=%s) root=%s", rd, PROJECT_ROOT)
    init_db()
    summary = run_ingest(
        max_events=args.max_events,
        force=args.force,
        lookback=args.lookback,
        offline_listing=Path(args.offline_listing) if args.offline_listing else None,
    )
    logging.info(
        "ingest done candidates=%s ingested=%s failed=%s",
        summary["candidates"],
        summary["ingested"],
        summary["failed"],
    )
    with db_session() as conn:
        apply_all(conn)
        report_path, data = write_report(conn, report_date=today)
    logging.info(
        "report written: %s events=%s distinctive=%s",
        report_path,
        len(data.events),
        len(data.distinctive),
    )

    catchup = None
    if not args.skip_catchup:
        catchup = run_yesterday_catchup(
            now_jst_date=today,
            force=bool(args.force_catchup),
            lookback=max(args.lookback, 16),
        )
        if catchup.needed:
            logging.info(
                "catch-up done yesterday=%s regenerated=%s reinested=%s reasons=%s",
                catchup.yesterday.isoformat(),
                catchup.report_regenerated,
                catchup.reinested,
                catchup.reasons,
            )
            print(
                f"CATCHUP yesterday={catchup.yesterday.isoformat()} "
                f"needed=1 report={catchup.report_path} "
                f"reinested={','.join(catchup.reinested) or '-'}"
            )
        else:
            print(f"CATCHUP yesterday={catchup.yesterday.isoformat()} needed=0")
    else:
        print("CATCHUP skipped")

    print(f"OK report={report_path}")
    print(f"log={log_path}")
    print(f"db={DB_PATH}")
    failed = list(summary["failed"])
    if catchup and catchup.failed:
        failed.extend(catchup.failed)
    if failed:
        return 2
    return 0


def cmd_apply_overrides(args: argparse.Namespace) -> int:
    ensure_dirs()
    init_db()
    with db_session() as conn:
        stats = apply_all(conn)
        rd = date.fromisoformat(args.date) if args.date else today_jst()
        report_path, _data = write_report(conn, report_date=rd)
    print(f"overrides applied: {stats}")
    print(f"report={report_path}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    ensure_dirs()
    init_db()
    rd = date.fromisoformat(args.date) if args.date else today_jst()
    with db_session() as conn:
        report_path, data = write_report(conn, report_date=rd)
    print(f"report={report_path} events={len(data.events)}")
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    ensure_dirs()
    since = date.fromisoformat(args.since)
    until = date.fromisoformat(args.until) if args.until else today_jst()
    log_path = _setup_logging(f"backfill-{since.isoformat()}")
    logging.info("backfill start since=%s until=%s", since, until)
    summary = run_backfill(
        since=since,
        until=until,
        force=args.force,
        max_events=args.max_events,
        skip_existing=not args.force,
    )
    logging.info(
        "backfill done candidates=%s ingested=%s skipped=%s failed=%s",
        summary["candidates"],
        len(summary["ingested"]),
        len(summary["skipped_existing"]),
        len(summary["failed"]),
    )
    print(
        f"OK backfill ingested={len(summary['ingested'])} "
        f"skipped={len(summary['skipped_existing'])} "
        f"failed={len(summary['failed'])} candidates={summary['candidates']}"
    )
    print(f"log={log_path}")
    print(f"db={DB_PATH}")
    if summary["failed"]:
        for f in summary["failed"][:10]:
            print(f"  fail {f['slug']}: {f['error']}")
        return 2
    return 0


def cmd_player_stats(args: argparse.Namespace) -> int:
    """Print frequent-player table (not written to daily report)."""
    ensure_dirs()
    init_db()
    cfg = load_player_stats_config()
    window = args.window_days if args.window_days is not None else int(cfg["window_days"])
    min_app = args.min_appearances if args.min_appearances is not None else int(cfg["min_appearances"])
    as_of = date.fromisoformat(args.as_of) if args.as_of else today_jst()
    with db_session() as conn:
        if args.rebuild:
            n = rebuild_player_event_results(conn)
            print(f"rebuilt player_event_results rows={n}")
        rows = query_frequent_players(
            conn,
            min_appearances=min_app,
            window_days=window,
            as_of=as_of,
        )
        print(f"# frequent players (last {window}d, >= {min_app} appearances, as_of={as_of})")
        print(format_player_stats_table(rows, conn))
    return 0


def cmd_discord_summary(args: argparse.Namespace) -> int:
    """Generate Discord daily/weekly summary; optionally POST to webhook."""
    ensure_dirs()
    rd = date.fromisoformat(args.date) if args.date else today_jst()
    out = Path(args.out) if args.out else None
    text = generate_and_maybe_post(
        mode=args.mode,
        report_date=rd,
        webhook_url=args.webhook,
        post=bool(args.post),
        out_path=out,
        skip_if_posted=not bool(args.force_post),
        force_post=bool(args.force_post),
    )
    # Always print for Actions log / piping (safe: no secret)
    print(text, end="" if text.endswith("\n") else "\n")
    return 0


def cmd_run_paper_manual(args: argparse.Namespace) -> int:
    """Manual URL / HTML / paste → same paper ingest + weekly md + Discord."""
    ensure_dirs()
    today = today_jst()
    log_path = _setup_logging(f"paper-manual-{today.isoformat()}")
    logging.info("run-paper-manual start (JST date=%s)", today.isoformat())
    init_db()
    cfg = load_paper_config()

    urls = list(args.url or [])
    html_paths = [Path(p) for p in (args.html or [])]
    paste_paths = [Path(p) for p in (args.paste or [])]
    html_dir = Path(args.html_dir) if args.html_dir else None
    if not urls and not html_paths and not paste_paths and html_dir is None:
        logging.error("need at least one of --url / --html / --html-dir / --paste")
        print("ERROR: pass --url and/or --html / --html-dir / --paste")
        return 2

    summary = run_paper_manual(
        urls=urls,
        html_paths=html_paths,
        html_dir=html_dir,
        paste_paths=paste_paths,
        source_url_for_html=args.source_url,
        force=bool(args.force),
        force_small=bool(args.force_small),
        try_live=not bool(args.offline_only),
    )
    logging.info(
        "paper manual cf_blocked=%s candidates=%s ingested=%s skipped_existing=%s skipped_small=%s failed=%s",
        summary.get("cf_blocked"),
        summary.get("candidates"),
        summary.get("ingested"),
        summary.get("skipped_existing"),
        summary.get("skipped_small"),
        summary.get("failed"),
    )

    notes = list(summary.get("notes") or [])
    if summary.get("cf_blocked"):
        notes.append("一部 URL が Cloudflare で失敗。HTML ドロップを推奨。")

    with db_session() as conn:
        report_path, week_key, events = write_weekly_report(
            conn,
            as_of=today,
            include_all_if_empty=True,
            notes=notes,
        )

    posted: list[str] = []
    if args.post_discord or args.discord_dry_run:
        notify = events
        if summary.get("ingested") and not args.force_discord:
            want = set(summary["ingested"])
            notify = [e for e in events if e.event_id in want]
        posted = post_paper_events_if_new(
            notify,
            week_key=week_key,
            webhook_url=args.webhook,
            post=bool(args.post_discord),
            force_post=bool(args.force_discord),
            username=str(cfg.get("discord_username") or "Pauper Paper"),
        )

    print(
        f"OK paper-manual week={week_key} report={report_path} "
        f"ingested={len(summary.get('ingested') or [])} "
        f"cf_blocked={int(bool(summary.get('cf_blocked')))} "
        f"discord_posted={len(posted)}"
    )
    print(f"log={log_path}")
    print(f"db={DB_PATH}")
    if summary.get("failed"):
        for f in summary["failed"][:10]:
            print(f"  fail {f}")
    if summary.get("skipped_small"):
        for f in summary["skipped_small"][:5]:
            print(f"  skip-small {f}")
    if not summary.get("ingested") and summary.get("failed") and not events:
        return 2
    return 0


def cmd_run_paper(args: argparse.Namespace) -> int:
    """Paper spike: scan MTGDecks (≥50) → SQLite channel=paper → weekly md → Discord."""
    ensure_dirs()
    today = today_jst()
    log_path = _setup_logging(f"paper-{today.isoformat()}")
    logging.info("run-paper start (JST date=%s)", today.isoformat())
    init_db()
    cfg = load_paper_config()
    offline_listing = Path(args.offline_listing) if args.offline_listing else None
    offline_events = Path(args.offline_events_dir) if args.offline_events_dir else None
    # Default offline fixtures when --fixtures (Actions CF fallback path)
    if args.fixtures and not offline_listing:
        candidate = FIXTURES_DIR / "mtgdecks" / "listing-tabletop-mini.html"
        if candidate.exists():
            offline_listing = candidate
        offline_events = offline_events or (FIXTURES_DIR / "mtgdecks")

    summary = run_paper_scan(
        offline_listing=offline_listing,
        offline_events_dir=offline_events,
        force=bool(args.force),
        max_events=args.max_events,
        try_live=not bool(args.fixtures) and not bool(args.offline_only),
    )
    logging.info(
        "paper scan method=%s cf_blocked=%s candidates=%s ingested=%s skipped=%s failed=%s",
        summary.get("fetch_method"),
        summary.get("cf_blocked"),
        summary.get("candidates"),
        summary.get("ingested"),
        summary.get("skipped_existing"),
        summary.get("failed"),
    )

    notes: list[str] = []
    if summary.get("cf_blocked"):
        notes.append(
            "Cloudflare によりライブ取得がブロックされた（または失敗）。"
            "フィクスチャ／手動ドロップがあればそれを使用。"
        )
    if summary.get("fetch_method") in ("fixtures-only", "offline"):
        notes.append(f"取得モード: {summary.get('fetch_method')}")

    with db_session() as conn:
        report_path, week_key, events = write_weekly_report(
            conn,
            as_of=today,
            include_all_if_empty=True,
            notes=notes,
        )

    posted: list[str] = []
    if args.post_discord or args.discord_dry_run:
        # Only notify for events ingested this run (new analyses), unless force
        notify = events
        if summary.get("ingested") and not args.force_discord:
            want = set(summary["ingested"])
            notify = [e for e in events if e.event_id in want]
        posted = post_paper_events_if_new(
            notify,
            week_key=week_key,
            webhook_url=args.webhook,
            post=bool(args.post_discord),
            force_post=bool(args.force_discord),
            username=str(cfg.get("discord_username") or "Pauper Paper"),
        )

    print(
        f"OK paper week={week_key} report={report_path} "
        f"ingested={len(summary.get('ingested') or [])} "
        f"cf_blocked={int(bool(summary.get('cf_blocked')))} "
        f"method={summary.get('fetch_method')} discord_posted={len(posted)}"
    )
    print(f"log={log_path}")
    print(f"db={DB_PATH}")
    if summary.get("failed"):
        for f in summary["failed"][:10]:
            print(f"  fail {f}")
        # Non-fatal for CF-only failures when fixtures produced a report
        if not events and summary.get("cf_blocked"):
            return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pauper_meta", description="Pauper meta tracker")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init-db", help="Create SQLite schema")
    s.set_defaults(func=cmd_init_db)

    s = sub.add_parser(
        "run-daily",
        help="Ingest Challenge/Preliminary + write daily report (+ yesterday catch-up)",
    )
    s.add_argument("--lookback", type=int, default=8, help="Max listing events to consider")
    s.add_argument("--max-events", type=int, default=None, help="Limit ingest count (smoke tests)")
    s.add_argument("--force", action="store_true", help="Refetch even if cache exists")
    s.add_argument("--offline-listing", type=str, default=None, help="Use local listing HTML")
    s.add_argument(
        "--skip-catchup",
        action="store_true",
        help="Skip yesterday (JST) incomplete/missing-report catch-up",
    )
    s.add_argument(
        "--force-catchup",
        action="store_true",
        help="Always regenerate yesterday's report (still one JST day lookback)",
    )
    s.set_defaults(func=cmd_run_daily)

    s = sub.add_parser("apply-overrides", help="Apply aliases/overrides CSV and regenerate report")
    s.add_argument("--date", type=str, default=None, help="Report date YYYY-MM-DD (JST)")
    s.set_defaults(func=cmd_apply_overrides)

    s = sub.add_parser("report", help="Regenerate Markdown report only")
    s.add_argument("--date", type=str, default=None, help="Report date YYYY-MM-DD (JST)")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser(
        "backfill",
        help="Ingest Challenge/Preliminary via monthly listings since a date (idempotent)",
    )
    s.add_argument("--since", type=str, required=True, help="Start date YYYY-MM-DD (inclusive)")
    s.add_argument("--until", type=str, default=None, help="End date YYYY-MM-DD (default today JST)")
    s.add_argument("--max-events", type=int, default=None, help="Limit number of events")
    s.add_argument("--force", action="store_true", help="Refetch/reingest even if already in DB")
    s.set_defaults(func=cmd_backfill)

    s = sub.add_parser(
        "player-stats",
        help="Query frequent/master players (DB only; not on daily report). Default: 30d / 7+",
    )
    s.add_argument("--window-days", type=int, default=None, help="Lookback days (default from config)")
    s.add_argument("--min-appearances", type=int, default=None, help="Min events in window")
    s.add_argument("--as-of", type=str, default=None, help="End date YYYY-MM-DD (default today JST)")
    s.add_argument("--rebuild", action="store_true", help="Rebuild player_event_results first")
    s.set_defaults(func=cmd_player_stats)

    s = sub.add_parser(
        "discord-summary",
        help="Build Discord webhook summary (daily or weekly). Optional --post via DISCORD_WEBHOOK_URL",
    )
    s.add_argument(
        "--mode",
        choices=("daily", "weekly"),
        default="daily",
        help="daily = 日次要約 / weekly = 累積ダイジェスト",
    )
    s.add_argument("--date", type=str, default=None, help="Report date YYYY-MM-DD (JST)")
    s.add_argument("--out", type=str, default=None, help="Write summary text to this path")
    s.add_argument(
        "--post",
        action="store_true",
        help="POST to Discord (uses --webhook or env DISCORD_WEBHOOK_URL; skips with warning if missing)",
    )
    s.add_argument(
        "--force-post",
        action="store_true",
        help="Post even if data/discord_posted.json already has this mode+date",
    )
    s.add_argument("--webhook", type=str, default=None, help="Webhook URL (prefer Secret/env)")
    s.set_defaults(func=cmd_discord_summary)

    s = sub.add_parser(
        "run-paper",
        help="Paper spike: MTGDecks ≥50 scan → channel=paper → weekly md → Discord (new events)",
    )
    s.add_argument("--force", action="store_true", help="Reingest even if event already in DB")
    s.add_argument("--max-events", type=int, default=None, help="Limit events this run")
    s.add_argument(
        "--fixtures",
        action="store_true",
        help="Use fixtures/mtgdecks offline HTML only (no live Playwright)",
    )
    s.add_argument(
        "--offline-only",
        action="store_true",
        help="Do not attempt live fetch (requires --offline-listing and/or event HTML drops)",
    )
    s.add_argument("--offline-listing", type=str, default=None, help="Local listing HTML path")
    s.add_argument(
        "--offline-events-dir",
        type=str,
        default=None,
        help="Directory of event-<id>-*.html drops (default fixtures/mtgdecks when --fixtures)",
    )
    s.add_argument(
        "--post-discord",
        action="store_true",
        help="POST new paper events to Discord (DISCORD_WEBHOOK_URL; same channel as Challenge for now)",
    )
    s.add_argument(
        "--discord-dry-run",
        action="store_true",
        help="Build paper Discord texts without POSTing",
    )
    s.add_argument(
        "--force-discord",
        action="store_true",
        help="Post even if data/discord_posted.json already lists the paper event id",
    )
    s.add_argument("--webhook", type=str, default=None, help="Webhook URL override")
    s.set_defaults(func=cmd_run_paper)

    s = sub.add_parser(
        "run-paper-manual",
        help="Paper manual handoff: event URL(s) and/or saved HTML / paste JSON → weekly md → Discord",
    )
    s.add_argument(
        "--url",
        action="append",
        default=[],
        help="MTGDecks event URL (repeatable). Live fetch often CF-blocked; prefer --html",
    )
    s.add_argument(
        "--html",
        action="append",
        default=[],
        help="Saved event HTML path (repeatable). Use with --source-url when possible",
    )
    s.add_argument(
        "--source-url",
        type=str,
        default=None,
        help="Canonical MTGDecks URL for --html (single-html case)",
    )
    s.add_argument(
        "--html-dir",
        type=str,
        default=None,
        help="Directory of event-<id>-*.html drops",
    )
    s.add_argument(
        "--paste",
        action="append",
        default=[],
        help="Structured paste JSON path (repeatable); see store docs/phase2-paper-manual-handoff.md",
    )
    s.add_argument("--force", action="store_true", help="Reingest even if event already in DB")
    s.add_argument(
        "--force-small",
        action="store_true",
        help="Ingest even when player_count < min_players (or unknown)",
    )
    s.add_argument(
        "--offline-only",
        action="store_true",
        help="Do not attempt live fetch for --url (HTML/paste only)",
    )
    s.add_argument("--post-discord", action="store_true", help="POST new paper events to Discord")
    s.add_argument("--discord-dry-run", action="store_true", help="Build Discord texts without POSTing")
    s.add_argument("--force-discord", action="store_true", help="Post even if already in discord_posted.json")
    s.add_argument("--webhook", type=str, default=None, help="Webhook URL override")
    s.set_defaults(func=cmd_run_paper_manual)

    return p


def main(argv: list[str] | None = None) -> int:
    src = PROJECT_ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
