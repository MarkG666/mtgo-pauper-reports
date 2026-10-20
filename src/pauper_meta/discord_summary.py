"""Discord Webhook 用の短い要約（案2: 要約＋GitHub全文リンク）。"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from .db import db_session, init_db
from .paths import REPORTS_DIR, ensure_dirs
from .report_daily import DailyReportData, build_report_data, today_jst

log = logging.getLogger(__name__)

DEFAULT_REPO = "MarkG666/mtgo-pauper-reports"
DEFAULT_BRANCH = "main"
MAX_CHARS = 1800
META_TOP_N = 8
WEEKLY_TOP_N = 10


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


def build_daily_summary(
    data: DailyReportData,
    *,
    report_url: str | None = None,
) -> str:
    url = report_url or report_github_url(data.report_date)
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
            for row in bracket_ev.top8_bracket:
                lines.append(f"- {row.place_label}: {row.player} — {row.archetype}")
            lines.append("")

    if data.meta_overview:
        lines.append("**メタ Top32（上位）**")
        for row in data.meta_overview[:META_TOP_N]:
            lines.append(
                f"- {row.archetype}: {row.count} ({_fmt_pct(row.share)}) "
                f"Top8:{row.top8_count}"
            )
        lines.append("")

    if data.distinctive:
        lines.append("**特徴リスト**")
        for flag in data.distinctive[:5]:
            rank = f"#{flag.finish_rank}" if flag.finish_rank else ""
            link = f" <{flag.deck_url}>" if flag.deck_url else ""
            lines.append(f"- {flag.player} — {flag.archetype} {rank}{link}".rstrip())
        lines.append("")

    lines.append(f"全文: {url}")
    return _truncate("\n".join(lines).rstrip() + "\n")


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


def generate_and_maybe_post(
    *,
    mode: str,
    report_date: date | None = None,
    webhook_url: str | None = None,
    post: bool = False,
    out_path: Path | None = None,
) -> str:
    """Build summary text; optionally write file and/or POST to Discord."""
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
        url = resolve_webhook_url(webhook_url)
        if not url:
            log.warning(
                "DISCORD_WEBHOOK_URL not set; skipping Discord post (setup Secret to enable)"
            )
        else:
            post_discord_webhook(text, url)
            log.info("posted %s summary to Discord (%s chars)", mode, len(text))

    return text
