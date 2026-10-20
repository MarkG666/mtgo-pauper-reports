"""CLI entry: run-daily / apply-overrides / report / init-db / player-stats / discord-*."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from .backfill import run_backfill
from .db import db_session, init_db
from .discord_summary import generate_and_maybe_post
from .ingest import run_ingest
from .overrides import apply_all
from .paths import DB_PATH, LOGS_DIR, PROJECT_ROOT, ensure_dirs
from .players import (
    format_player_stats_table,
    load_player_stats_config,
    query_frequent_players,
    rebuild_player_event_results,
)
from .report_daily import today_jst, write_report


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
    rd = today_jst().isoformat()
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
        report_path, data = write_report(conn, report_date=date.fromisoformat(rd))
    logging.info("report written: %s events=%s distinctive=%s", report_path, len(data.events), len(data.distinctive))
    print(f"OK report={report_path}")
    print(f"log={log_path}")
    print(f"db={DB_PATH}")
    if summary["failed"]:
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
    )
    # Always print for Actions log / piping (safe: no secret)
    print(text, end="" if text.endswith("\n") else "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pauper_meta", description="Pauper meta tracker (Phase 1)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init-db", help="Create SQLite schema")
    s.set_defaults(func=cmd_init_db)

    s = sub.add_parser("run-daily", help="Ingest Challenge/Preliminary + write daily report")
    s.add_argument("--lookback", type=int, default=8, help="Max listing events to consider")
    s.add_argument("--max-events", type=int, default=None, help="Limit ingest count (smoke tests)")
    s.add_argument("--force", action="store_true", help="Refetch even if cache exists")
    s.add_argument("--offline-listing", type=str, default=None, help="Use local listing HTML")
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
    s.add_argument("--webhook", type=str, default=None, help="Webhook URL (prefer Secret/env)")
    s.set_defaults(func=cmd_discord_summary)

    return p


def main(argv: list[str] | None = None) -> int:
    src = PROJECT_ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
