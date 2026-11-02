#!/usr/bin/env python3
"""Sync reports/daily (+ meta notes) into docs/ for GitHub Pages (Jekyll).

Source of truth remains reports/daily/*.md.
Run locally or via .github/workflows/sync-pages.yml after reports change.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DAILY = ROOT / "reports" / "daily"
SRC_META = ROOT / "reports" / "meta"
DOCS = ROOT / "docs"
DST_DAILY = DOCS / "daily"
DST_META = DOCS / "meta"
INDEX = DOCS / "index.md"

# Relative .md links in report bodies break on Pages; point at Jekyll HTML output.
MD_LINK_RE = re.compile(r"(\]\([^\)]*?)\.md(\)|#)")
TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def yaml_escape(value: str) -> str:
    """Quote a YAML string safely for front matter."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def extract_title(text: str, fallback: str) -> str:
    match = TITLE_RE.search(text)
    if not match:
        return fallback
    return match.group(1).strip()


def ensure_blank_line_before_tables(text: str) -> str:
    """Insert a blank line before a GFM table that follows non-table content.

    Jekyll (kramdown + GFM) does not recognize pipe tables that start on the
    line immediately after a heading/paragraph; they render as one collapsed
    ``<p>| ... || ... |</p>``. GitHub.com is more lenient, but a blank line is
    valid GFM either way.
    """
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        if (
            line.startswith("|")
            and out
            and out[-1].strip() != ""
            and not out[-1].lstrip().startswith("|")
        ):
            out.append("")
        out.append(line)
    ended_with_newline = text.endswith("\n")
    result = "\n".join(out)
    if ended_with_newline:
        result += "\n"
    return result


def sort_key(stem: str) -> tuple:
    """Reverse-chronological friendly key: date desc, then optional -N suffix."""
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:-(\d+))?", stem)
    if not m:
        return ("", 0, stem)
    date, suffix = m.group(1), int(m.group(2) or 0)
    return (date, suffix, stem)


def write_page(src: Path, dst: Path, *, layout: str = "default") -> str:
    raw = src.read_text(encoding="utf-8")
    title = extract_title(raw, src.stem)
    body = MD_LINK_RE.sub(r"\1.html\2", raw)
    # Drop the leading H1; layout already shows page.title.
    body = re.sub(r"^#\s+.+\n+", "", body, count=1)
    body = ensure_blank_line_before_tables(body)
    front = (
        "---\n"
        f"layout: {layout}\n"
        f"title: {yaml_escape(title)}\n"
        f"date: {src.stem[:10]}\n"
        "---\n\n"
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(front + body, encoding="utf-8")
    return title


def sync_daily() -> list[tuple[str, str]]:
    """Copy daily reports; return [(stem, title), ...] newest first."""
    if DST_DAILY.exists():
        shutil.rmtree(DST_DAILY)
    DST_DAILY.mkdir(parents=True)

    entries: list[tuple[str, str]] = []
    for src in sorted(SRC_DAILY.glob("*.md")):
        title = write_page(src, DST_DAILY / src.name)
        entries.append((src.stem, title))

    entries.sort(key=lambda item: sort_key(item[0]), reverse=True)
    return entries


def sync_meta() -> None:
    """Mirror reports/meta so relative footer links keep working."""
    if DST_META.exists():
        shutil.rmtree(DST_META)
    DST_META.mkdir(parents=True)
    if not SRC_META.exists():
        return
    for src in SRC_META.glob("*.md"):
        write_page(src, DST_META / src.name)


def write_index(entries: list[tuple[str, str]]) -> None:
    lines = [
        "---",
        "layout: default",
        'title: "日次レポート一覧"',
        "permalink: /",
        "---",
        "",
        "Magic Online の Pauper Challenge / Preliminary 日次レポートです。",
        "新しい開催日が上です。本文の出典はリポジトリの `reports/daily/` です。",
        "",
        "| 開催日 | レポート |",
        "|---|---|",
    ]
    for stem, title in entries:
        date = stem[:10]
        lines.append(f"| {date} | [{title}](daily/{stem}.html) |")
    lines.append("")
    INDEX.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    if not SRC_DAILY.is_dir():
        raise SystemExit(f"missing source directory: {SRC_DAILY}")
    DOCS.mkdir(parents=True, exist_ok=True)
    entries = sync_daily()
    sync_meta()
    write_index(entries)
    print(f"synced {len(entries)} daily report(s) → {DST_DAILY.relative_to(ROOT)}/")
    print(f"index → {INDEX.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
