"""Weekly Paper Markdown reports (independent of Challenge daily / meta %)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .ingest_paper import load_paper_meta
from .paths import PAPER_REPORTS_DIR, ensure_dirs
from .report_daily import today_jst

JST = ZoneInfo("Asia/Tokyo")


@dataclass
class PaperTop8Row:
    place_label: str
    finish_rank: int
    player: str
    archetype: str
    deck_url: str


@dataclass
class PaperEventBlock:
    event_id: str
    name: str
    start_time: str | None
    player_count: int | None
    source_url: str
    top8: list[PaperTop8Row] = field(default_factory=list)


def iso_week_key(d: date) -> str:
    """ISO week key used in filenames: YYYY-Www (Monday-based)."""
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def week_bounds_jst(d: date | None = None) -> tuple[date, date, str]:
    """Return (monday, sunday, YYYY-Www) for the JST week containing ``d``."""
    day = d or today_jst()
    monday = day - timedelta(days=day.weekday())  # Monday=0
    sunday = monday + timedelta(days=6)
    return monday, sunday, iso_week_key(monday)


def _parse_event_date(start_time: str | None) -> date | None:
    if not start_time:
        return None
    text = start_time.strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def _place_label(finish_rank: int | None, fallback: str | None = None) -> str:
    if fallback:
        return fallback
    if finish_rank == 1:
        return "優勝"
    if finish_rank == 2:
        return "準優勝"
    if finish_rank in (3, 4):
        return "Top4"
    if finish_rank is not None and finish_rank <= 8:
        return "Top8"
    return "—"


def load_paper_events_for_week(
    conn: sqlite3.Connection,
    *,
    week_start: date,
    week_end: date,
) -> list[PaperEventBlock]:
    """Load channel=paper events whose start_time falls in [week_start, week_end]."""
    rows = conn.execute(
        """
        SELECT id, name, start_time, player_count, source_url
        FROM events
        WHERE channel = 'paper'
        ORDER BY start_time DESC, id DESC
        """
    ).fetchall()
    blocks: list[PaperEventBlock] = []
    for ev in rows:
        ed = _parse_event_date(ev["start_time"])
        # Include undated events only when regenerating "current" week manually via all=True later
        if ed is None:
            continue
        if not (week_start <= ed <= week_end):
            continue
        raw_id = str(ev["id"]).removeprefix("paper-")
        meta = load_paper_meta(raw_id)
        meta_decks = {int(d["finish_rank"]): d for d in (meta or {}).get("decks", []) if d.get("finish_rank")}

        deck_rows = conn.execute(
            """
            SELECT finish_rank, player, archetype_final, source_archetype_label
            FROM decks
            WHERE event_id = ?
              AND finish_rank IS NOT NULL
              AND finish_rank <= 8
            ORDER BY finish_rank ASC
            """,
            (ev["id"],),
        ).fetchall()
        top8: list[PaperTop8Row] = []
        for d in deck_rows:
            fr = int(d["finish_rank"])
            md = meta_decks.get(fr) or {}
            arch = d["archetype_final"] or d["source_archetype_label"] or "Unknown"
            top8.append(
                PaperTop8Row(
                    place_label=_place_label(fr, md.get("place_label")),
                    finish_rank=fr,
                    player=d["player"] or md.get("player") or "Unknown",
                    archetype=arch,
                    deck_url=str(md.get("deck_url") or ""),
                )
            )
        blocks.append(
            PaperEventBlock(
                event_id=str(ev["id"]),
                name=ev["name"],
                start_time=ev["start_time"],
                player_count=ev["player_count"],
                source_url=ev["source_url"] or "",
                top8=top8,
            )
        )
    return blocks


def load_all_paper_events(conn: sqlite3.Connection) -> list[PaperEventBlock]:
    """All paper events (for fixture spike / sample week when dates vary)."""
    rows = conn.execute(
        """
        SELECT id, name, start_time, player_count, source_url
        FROM events
        WHERE channel = 'paper'
        ORDER BY start_time DESC, id DESC
        """
    ).fetchall()
    # Reuse week loader logic by faking a wide window via direct build
    blocks: list[PaperEventBlock] = []
    for ev in rows:
        raw_id = str(ev["id"]).removeprefix("paper-")
        meta = load_paper_meta(raw_id)
        meta_decks = {int(d["finish_rank"]): d for d in (meta or {}).get("decks", []) if d.get("finish_rank")}
        deck_rows = conn.execute(
            """
            SELECT finish_rank, player, archetype_final, source_archetype_label
            FROM decks
            WHERE event_id = ?
              AND finish_rank IS NOT NULL
              AND finish_rank <= 8
            ORDER BY finish_rank ASC
            """,
            (ev["id"],),
        ).fetchall()
        top8: list[PaperTop8Row] = []
        for d in deck_rows:
            fr = int(d["finish_rank"])
            md = meta_decks.get(fr) or {}
            arch = d["archetype_final"] or d["source_archetype_label"] or "Unknown"
            top8.append(
                PaperTop8Row(
                    place_label=_place_label(fr, md.get("place_label")),
                    finish_rank=fr,
                    player=d["player"] or "Unknown",
                    archetype=arch,
                    deck_url=str(md.get("deck_url") or ""),
                )
            )
        blocks.append(
            PaperEventBlock(
                event_id=str(ev["id"]),
                name=ev["name"],
                start_time=ev["start_time"],
                player_count=ev["player_count"],
                source_url=ev["source_url"] or "",
                top8=top8,
            )
        )
    return blocks


def render_weekly_markdown(
    *,
    week_key: str,
    week_start: date,
    week_end: date,
    events: list[PaperEventBlock],
    generated_at: datetime | None = None,
    notes: list[str] | None = None,
) -> str:
    now = generated_at or datetime.now(JST)
    lines: list[str] = [
        f"# Pauper Paper 週次レポート — {week_key}（JST）",
        "",
        f"生成: `{now.isoformat(timespec='seconds')}`",
        "",
        f"対象週: **{week_start.isoformat()}（月）〜 {week_end.isoformat()}（日）**",
        "",
        "対象: **MTGDecks Paper**（参加人数 ≥ 50）。Challenge 日次メタ％には **含まない**。",
        "",
        "※ ブラケット／敗因は不明のため省略。Top8 はプレイヤー＋アーキタイプのみ。リストリンクは MTGDecks。",
        "",
    ]
    if not events:
        lines.extend(
            [
                "## 大会",
                "",
                "この週に検出・取り込みできた 50+ Paper 大会はありません。",
                "",
            ]
        )
    else:
        lines.append("## 大会一覧")
        lines.append("")
        for ev in events:
            pc = f"{ev.player_count}人" if ev.player_count is not None else "?"
            when = ev.start_time or "?"
            link = f" / [MTGDecks]({ev.source_url})" if ev.source_url else ""
            lines.append(f"- **{ev.name}** — {when} / {pc}{link}")
        lines.append("")

        for ev in events:
            pc = f"{ev.player_count}人" if ev.player_count is not None else "?"
            lines.append(f"## {ev.name}")
            lines.append("")
            lines.append(f"- 日付: `{ev.start_time or '?'}`")
            lines.append(f"- 人数: {pc}")
            if ev.source_url:
                lines.append(f"- ソース: [MTGDecks]({ev.source_url})")
            lines.append("")
            lines.append("### Top8")
            lines.append("")
            lines.append("| 結果 | プレイヤー | アーキタイプ | リスト |")
            lines.append("|---|---|---|---|")
            if not ev.top8:
                lines.append("| — | — | — | — |")
            else:
                for row in ev.top8:
                    link = f"[MTGDecks]({row.deck_url})" if row.deck_url else "—"
                    lines.append(
                        f"| {row.place_label} | {row.player} | {row.archetype} | {link} |"
                    )
            lines.append("")

    note_lines = list(notes or [])
    note_lines.append("Paper は Challenge／Preliminary の主メタ％集計に合算しない（channel=paper）。")
    lines.append("## メモ")
    lines.append("")
    for n in note_lines:
        lines.append(f"- {n}")
    lines.append("")
    return "\n".join(lines)


def write_weekly_report(
    conn: sqlite3.Connection,
    *,
    as_of: date | None = None,
    include_all_if_empty: bool = False,
    notes: list[str] | None = None,
) -> tuple[Path, str, list[PaperEventBlock]]:
    """Write ``reports/paper/weekly/YYYY-Www.md`` and return (path, week_key, events)."""
    ensure_dirs()
    PAPER_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    monday, sunday, week_key = week_bounds_jst(as_of)
    events = load_paper_events_for_week(conn, week_start=monday, week_end=sunday)
    extra_notes = list(notes or [])
    if not events and include_all_if_empty:
        events = load_all_paper_events(conn)
        if events:
            dated_ok = [d for d in (_parse_event_date(e.start_time) for e in events) if d]
            if dated_ok:
                monday, sunday, week_key = week_bounds_jst(max(dated_ok))
            extra_notes.append(
                "スパイク用: 対象週にイベントが無かったため、取り込み済み Paper 大会をサンプル表示。"
            )
    md = render_weekly_markdown(
        week_key=week_key,
        week_start=monday,
        week_end=sunday,
        events=events,
        notes=extra_notes,
    )
    path = PAPER_REPORTS_DIR / f"{week_key}.md"
    path.write_text(md, encoding="utf-8")
    return path, week_key, events


def paper_report_github_url(week_key: str, *, repo: str | None = None, branch: str | None = None) -> str:
    import os

    repo = repo or os.environ.get("GITHUB_REPOSITORY") or "MarkG666/mtgo-pauper-reports"
    branch = branch or os.environ.get("GITHUB_REF_NAME") or "main"
    if branch.startswith("refs/heads/"):
        branch = branch.removeprefix("refs/heads/")
    return f"https://github.com/{repo}/blob/{branch}/reports/paper/weekly/{week_key}.md"
