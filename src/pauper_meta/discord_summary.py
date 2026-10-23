"""Discord Webhook 用の短い要約（案2: 要約＋GitHub全文リンク）。"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

from .db import db_session, init_db
from .paths import DISCORD_POSTED_PATH, REPORTS_DIR, ensure_dirs
from .report_daily import DailyReportData, Top8Entry, build_report_data, today_jst

log = logging.getLogger(__name__)

DEFAULT_REPO = "MarkG666/mtgo-pauper-reports"
DEFAULT_BRANCH = "main"
MAX_CHARS = 1800
META_TOP_N = 8
WEEKLY_TOP_N = 10


def load_discord_posted(path: Path | None = None) -> dict[str, list[str]]:
    """Load posted tracker.

    Shape: ``{"daily": ["YYYY-MM-DD", ...], "weekly": [...], "paper": ["paper-<id>", ...]}``.
    ``paper`` keys are event ids (not dates) — Discord fires per newly analyzed large event.
    """
    p = path or DISCORD_POSTED_PATH
    empty: dict[str, list[str]] = {"daily": [], "weekly": [], "paper": []}
    if not p.exists():
        return empty
    try:
        data: Any = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    out: dict[str, list[str]] = {"daily": [], "weekly": [], "paper": []}
    for key in ("daily", "weekly", "paper"):
        raw = data.get(key) or []
        if isinstance(raw, list):
            out[key] = sorted({str(x) for x in raw if x})
    return out


def was_discord_posted(mode: str, report_date: date, path: Path | None = None) -> bool:
    data = load_discord_posted(path)
    return report_date.isoformat() in data.get(mode, [])


def was_paper_event_posted(event_id: str, path: Path | None = None) -> bool:
    data = load_discord_posted(path)
    return event_id in data.get("paper", [])


def mark_discord_posted(mode: str, report_date: date, path: Path | None = None) -> None:
    """Record that we successfully posted; idempotent. File is Git-tracked."""
    ensure_dirs()
    p = path or DISCORD_POSTED_PATH
    data = load_discord_posted(p)
    key = "weekly" if mode == "weekly" else "daily"
    dates = set(data.get(key, []))
    dates.add(report_date.isoformat())
    data[key] = sorted(dates)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log.info("marked discord posted mode=%s date=%s path=%s", key, report_date.isoformat(), p)


def mark_paper_event_posted(event_id: str, path: Path | None = None) -> None:
    ensure_dirs()
    p = path or DISCORD_POSTED_PATH
    data = load_discord_posted(p)
    posted = set(data.get("paper", []))
    posted.add(event_id)
    data["paper"] = sorted(posted)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log.info("marked discord paper event posted id=%s path=%s", event_id, p)


def report_github_url(report_date: str, *, repo: str | None = None, branch: str | None = None) -> str:
    repo = repo or os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO
    branch = branch or os.environ.get("GITHUB_REF_NAME") or DEFAULT_BRANCH
    if branch.startswith("refs/heads/"):
        branch = branch.removeprefix("refs/heads/")
    return f"https://github.com/{repo}/blob/{branch}/reports/daily/{report_date}.md"


def _fmt_pct(share: float) -> str:
    return f"{share * 100:.0f}%"


def _fmt_delta(delta: float | None) -> str:
    if delta is None:
        return "—"
    if abs(delta) < 0.005:
        return "-"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta * 100:.0f}%"


def _truncate(text: str, limit: int = MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    # Prefer cutting at a line boundary
    cut = text[: limit - 20].rsplit("\n", 1)[0]
    return cut + "\n…（省略）"


def _eliminator_player(eliminated_by: str) -> str | None:
    """Report 敗因 is `player（archetype）` or `—`; Discord uses player only."""
    text = (eliminated_by or "").strip()
    if not text or text == "—":
        return None
    return text.split("（", 1)[0].strip() or None


def _fmt_top8_line(row: Top8Entry, *, include_deck_link: bool = True) -> str:
    """例: `準優勝: seasonofmists — Grixis Affinity（yahakaraに敗北）`（優勝は括弧なし）.

    When ``include_deck_link`` and ``row.deck_url`` are set, appends
    `` <mtgo-per-deck-url>`` (no-embed Discord link).
    """
    line = f"{row.place_label}: {row.player} — {row.archetype}"
    if row.finish_rank != 1:
        elim = _eliminator_player(row.eliminated_by)
        if elim:
            line = f"{line}（{elim}に敗北）"
    if include_deck_link and row.deck_url:
        line = f"{line} <{row.deck_url}>"
    return f"- {line}"


def _build_daily_summary_body(
    data: DailyReportData,
    *,
    report_url: str,
    top8_links: str = "per-deck",
    distinctive_links: bool = True,
    meta_top_n: int = META_TOP_N,
) -> str:
    """Assemble daily summary text.

    ``top8_links``:
      - ``per-deck``: each Top8 line gets its MTGO ``#deck_<player>`` URL
      - ``event``: one event decklist URL under the Top8 header (fallback)
      - ``none``: no Top8 deck URLs
    """
    lines: list[str] = [
        f"**Pauper 日次** `{data.report_date}`",
        "",
    ]

    if not data.events:
        lines.append("対象イベントなし（新規なし）。")
    else:
        lines.append("**イベント**")
        for ev in data.events:
            pc = f"{ev.player_count}人" if ev.player_count is not None else "?"
            lines.append(f"- {ev.name} (`{ev.event_type}`) — {pc}")
        lines.append("")

        # Top8: first event with bracket, else first event
        bracket_ev = next((e for e in data.events if e.top8_bracket), data.events[0])
        if bracket_ev.top8_bracket:
            lines.append(f"**Top8** — {bracket_ev.name}")
            if top8_links == "event" and bracket_ev.source_url:
                lines.append(f"デッキ一覧: <{bracket_ev.source_url}>")
            include_per_deck = top8_links == "per-deck"
            for row in bracket_ev.top8_bracket:
                lines.append(_fmt_top8_line(row, include_deck_link=include_per_deck))
            lines.append("")

    if data.meta_overview:
        lines.append("**メタ Top32（上位）**")
        for row in data.meta_overview[:meta_top_n]:
            lines.append(
                f"- {row.archetype}: {row.count} ({_fmt_pct(row.share)}) "
                f"Top8:{row.top8_count}"
            )
        lines.append("")

    if data.distinctive:
        lines.append("**特徴リスト**")
        for flag in data.distinctive[:5]:
            rank = f"#{flag.finish_rank}" if flag.finish_rank else ""
            link = ""
            if distinctive_links and flag.deck_url:
                link = f" <{flag.deck_url}>"
            lines.append(f"- {flag.player} — {flag.archetype} {rank}{link}".rstrip())
        lines.append("")

    lines.append(f"全文: {report_url}")
    return "\n".join(lines).rstrip() + "\n"


def build_daily_summary(
    data: DailyReportData,
    *,
    report_url: str | None = None,
) -> str:
    """Build Discord daily text under ``MAX_CHARS`` (Discord hard limit 2000).

    Preference: per-deck Top8 MTGO links. If that exceeds the budget, drop
    distinctive URLs first (names remain; full report keeps Goldfish), then
    shrink meta rows, then fall back to a single MTGO event decklist URL for
    Top8. Hard truncate only as last resort.
    """
    url = report_url or report_github_url(data.report_date)
    attempts: list[dict[str, Any]] = [
        {"top8_links": "per-deck", "distinctive_links": True, "meta_top_n": META_TOP_N},
        {"top8_links": "per-deck", "distinctive_links": False, "meta_top_n": META_TOP_N},
        {"top8_links": "per-deck", "distinctive_links": False, "meta_top_n": 5},
        {"top8_links": "event", "distinctive_links": True, "meta_top_n": META_TOP_N},
        {"top8_links": "event", "distinctive_links": False, "meta_top_n": 5},
    ]
    text = ""
    for opts in attempts:
        text = _build_daily_summary_body(data, report_url=url, **opts)
        if len(text) <= MAX_CHARS:
            return text
    return _truncate(text)


def build_weekly_summary(
    data: DailyReportData,
    *,
    report_url: str | None = None,
) -> str:
    url = report_url or report_github_url(data.report_date)
    lines: list[str] = [
        f"**Pauper 週次ダイジェスト** as_of `{data.report_date}`",
        "",
        "**1ヵ月累積（上位）**",
    ]
    for row in data.month_dist[:WEEKLY_TOP_N]:
        if row.archetype == "Other":
            continue
        lines.append(f"- {row.archetype}: {_fmt_pct(row.share)} ({row.count})")

    other_m = next((r for r in data.month_dist if r.archetype == "Other"), None)
    if other_m:
        lines.append(f"- Other: {_fmt_pct(other_m.share)} ({other_m.count})")

    lines.append("")
    lines.append("**直近1週（前週差）**")
    for row in data.week_dist[:WEEKLY_TOP_N]:
        if row.archetype == "Other":
            continue
        lines.append(
            f"- {row.archetype}: {_fmt_pct(row.share)} ({_fmt_delta(row.share_delta)})"
        )
    other_w = next((r for r in data.week_dist if r.archetype == "Other"), None)
    if other_w:
        lines.append(
            f"- Other: {_fmt_pct(other_w.share)} ({_fmt_delta(other_w.share_delta)})"
        )

    lines.append("")
    lines.append(f"詳細レポート: {url}")
    return _truncate("\n".join(lines).rstrip() + "\n")


def post_discord_webhook(content: str, webhook_url: str, *, username: str = "Pauper Meta") -> None:
    """POST content to Discord webhook. Raises on HTTP errors."""
    payload = {"content": content, "username": username}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "pauper-meta-discord/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            log.info("Discord webhook status=%s", getattr(resp, "status", "?"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Discord webhook HTTP {e.code}: {detail}") from e


def resolve_webhook_url(explicit: str | None = None) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()
    env = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    return env or None


def build_paper_event_summary(
    event: Any,
    *,
    week_report_url: str | None = None,
) -> str:
    """Short Discord blurb for one newly analyzed ≥50 Paper event."""
    from .report_paper import PaperEventBlock, paper_report_github_url

    assert isinstance(event, PaperEventBlock)
    pc = f"{event.player_count}人" if event.player_count is not None else "?"
    lines: list[str] = [
        "**Pauper Paper（50+）**",
        "",
        f"**{event.name}**",
        f"日付 `{event.start_time or '?'}` / {pc}",
        "",
    ]
    if event.top8:
        lines.append("**Top8**（プレイヤー＋アーキ）")
        for row in event.top8:
            link = f" <{row.deck_url}>" if row.deck_url else ""
            lines.append(f"- {row.place_label}: {row.player} — {row.archetype}{link}")
        lines.append("")
    if event.source_url:
        lines.append(f"MTGDecks: {event.source_url}")
    if week_report_url:
        lines.append(f"週次レポート: {week_report_url}")
    elif week_report_url is None:
        # caller may omit; still fine
        pass
    return _truncate("\n".join(lines).rstrip() + "\n")


def post_paper_events_if_new(
    events: list[Any],
    *,
    week_key: str | None = None,
    webhook_url: str | None = None,
    post: bool = False,
    force_post: bool = False,
    username: str = "Pauper Paper",
) -> list[str]:
    """Post Discord for each paper event not yet in discord_posted.json ``paper`` list.

    Reuses ``DISCORD_WEBHOOK_URL`` (same channel for now; separate channel later if noisy).
    Returns list of posted event ids.
    """
    from .report_paper import PaperEventBlock, paper_report_github_url

    posted_ids: list[str] = []
    week_url = paper_report_github_url(week_key) if week_key else None
    for ev in events:
        if not isinstance(ev, PaperEventBlock):
            continue
        if not force_post and was_paper_event_posted(ev.event_id):
            log.info("Discord paper event %s already posted — skipping", ev.event_id)
            continue
        text = build_paper_event_summary(ev, week_report_url=week_url)
        if not post:
            log.info("paper discord dry-run %s (%s chars)", ev.event_id, len(text))
            continue
        url = resolve_webhook_url(webhook_url)
        if not url:
            log.warning(
                "DISCORD_WEBHOOK_URL not set; skipping Paper Discord post "
                "(same Secret as Challenge; separate channel later if needed)"
            )
            continue
        post_discord_webhook(text, url, username=username)
        mark_paper_event_posted(ev.event_id)
        posted_ids.append(ev.event_id)
        log.info("posted paper event %s to Discord", ev.event_id)
    return posted_ids


def generate_and_maybe_post(
    *,
    mode: str,
    report_date: date | None = None,
    webhook_url: str | None = None,
    post: bool = False,
    out_path: Path | None = None,
    skip_if_posted: bool = True,
    force_post: bool = False,
) -> str:
    """Build summary text; optionally write file and/or POST to Discord.

    When ``post`` and ``skip_if_posted`` (default), skip the webhook if
    ``data/discord_posted.json`` already lists this mode+date — prevents
    double-posts on catch-up or re-runs. Successful posts update that file.
    ``force_post`` bypasses the dedupe check.
    """
    ensure_dirs()
    init_db()
    rd = report_date or today_jst()
    with db_session() as conn:
        data = build_report_data(conn, report_date=rd)

    if mode == "weekly":
        text = build_weekly_summary(data)
    else:
        text = build_daily_summary(data)

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        log.info("wrote summary: %s (%s chars)", out_path, len(text))

    # Prefer committed report path existence for URL sanity (non-fatal)
    md = REPORTS_DIR / f"{data.report_date}.md"
    if not md.exists():
        log.warning("daily report md missing at %s (URL may 404 until commit)", md)

    if post:
        track_mode = "weekly" if mode == "weekly" else "daily"
        if skip_if_posted and not force_post and was_discord_posted(track_mode, rd):
            log.info(
                "Discord %s for %s already posted — skipping (dedupe)",
                track_mode,
                rd.isoformat(),
            )
        else:
            url = resolve_webhook_url(webhook_url)
            if not url:
                log.warning(
                    "DISCORD_WEBHOOK_URL not set; skipping Discord post (setup Secret to enable)"
                )
            else:
                post_discord_webhook(text, url)
                mark_discord_posted(track_mode, rd)
                log.info("posted %s summary to Discord (%s chars)", mode, len(text))

    return text
