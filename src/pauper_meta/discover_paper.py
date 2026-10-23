"""Thin Paper discovery without MTGDecks listing crawl.

Primary source: melee.gg ``/Tournament/SearchResults`` (Pauper + date window).
Optional: MTGGoldfish tournament index (names only; no reliable player counts).
Watchlist: ``config/paper_watchlist.json`` for manually curated Melee IDs / names.

Output: ``reports/paper/alerts/YYYY-MM-DD.json`` (+ ``.md``) listing candidate
large (≥min_players) events so a human can paste the MTGDecks URL for full analysis.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .ingest_paper import load_paper_config
from .paths import CONFIG_DIR, PAPER_ALERTS_DIR, ensure_dirs
from .report_daily import today_jst

logger = logging.getLogger(__name__)

MELEE_SEARCH_RESULTS = "https://melee.gg/Tournament/SearchResults"
MELEE_VIEW = "https://melee.gg/Tournament/View/{id}"
GOLDFISH_PAUPER = "https://www.mtggoldfish.com/tournaments/pauper"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
# Melee date filter tokens that actually return recent rows (verified 2026-10-22).
MELEE_DATE_WINDOWS = ("LastMonth", "ThisWeek", "Last7Days", "NextWeek", "ThisWeekend")


@dataclass
class DiscoveryCandidate:
    name: str
    players: int | None
    source: str  # melee | goldfish | watchlist
    source_url: str
    source_id: str
    start_date: str | None = None
    status: str | None = None
    organization: str | None = None
    mtgdecks_search: str = ""
    notes: str = ""

    def alert_key(self) -> str:
        return f"{self.source}-{self.source_id}"


@dataclass
class DiscoveryResult:
    as_of: str
    min_players: int
    candidates: list[DiscoveryCandidate] = field(default_factory=list)
    sources_ok: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _mtgdecks_search_hint(name: str) -> str:
    q = re.sub(r"\s+", " ", (name or "").strip())
    if not q:
        return "https://mtgdecks.net/Pauper/tournaments/platform:tabletop"
    return "https://mtgdecks.net/Pauper/tournaments?" + urllib.parse.urlencode({"name": q})


def _http_post_json(url: str, body: bytes, *, timeout: float = 45.0) -> Any:
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": DEFAULT_UA,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://melee.gg/Tournament/Search",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _http_get_text(url: str, *, timeout: float = 45.0) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": DEFAULT_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_melee_pauper(
    *,
    date_windows: list[str] | None = None,
    min_players: int = 50,
) -> tuple[list[DiscoveryCandidate], list[str]]:
    """Query Melee SearchResults for Pauper events in date windows; keep ≥min_players."""
    windows = date_windows or ["LastMonth", "ThisWeek"]
    errors: list[str] = []
    by_id: dict[str, DiscoveryCandidate] = {}
    body = b"draw=1&start=0&length=100"

    for window in windows:
        params = {
            "timeZoneId": "UTC",
            "formats": "Pauper",
            "date": window,
        }
        url = MELEE_SEARCH_RESULTS + "?" + urllib.parse.urlencode(params)
        try:
            data = _http_post_json(url, body)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
            msg = f"melee SearchResults date={window}: {type(e).__name__}: {e}"
            logger.warning(msg)
            errors.append(msg)
            continue
        if not isinstance(data, list):
            errors.append(f"melee SearchResults date={window}: unexpected payload type {type(data).__name__}")
            continue
        for row in data:
            if not isinstance(row, dict):
                continue
            try:
                players = int(row.get("enrolledPlayerCount") or 0)
            except (TypeError, ValueError):
                players = 0
            if players < min_players:
                continue
            eid = str(row.get("id") or "").strip()
            if not eid:
                continue
            name = str(row.get("name") or f"melee-{eid}").strip()
            start = str(row.get("startDate") or "").strip() or None
            if start and "T" in start:
                start = start.split("T", 1)[0]
            cand = DiscoveryCandidate(
                name=name,
                players=players,
                source="melee",
                source_url=MELEE_VIEW.format(id=eid),
                source_id=eid,
                start_date=start,
                status=str(row.get("status") or "").strip() or None,
                organization=str(row.get("organizationName") or "").strip() or None,
                mtgdecks_search=_mtgdecks_search_hint(name),
                notes=f"melee date window={window}",
            )
            prev = by_id.get(eid)
            if prev is None or (cand.players or 0) >= (prev.players or 0):
                by_id[eid] = cand

    return list(by_id.values()), errors


def fetch_goldfish_pauper_names(*, lookback_days: int = 45) -> tuple[list[DiscoveryCandidate], list[str]]:
    """Name-only hints from MTGGoldfish Pauper tournament index (no player counts)."""
    errors: list[str] = []
    try:
        html = _http_get_text(GOLDFISH_PAUPER)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        msg = f"goldfish tournaments/pauper: {type(e).__name__}: {e}"
        logger.warning(msg)
        return [], [msg]

    # Paper-ish tournament titles linked as /tournament/<id>
    skip_re = re.compile(
        r"\b(league|challenge|preliminary|mtgo|online)\b",
        re.I,
    )
    paperish_re = re.compile(
        r"(lega|liga|geddon|open|classic|nationals|championship|cup|road to|pauper to the people|"
        r"common island|fuguete|british pauper|super pauper)",
        re.I,
    )
    candidates: list[DiscoveryCandidate] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'<a[^>]+href="(/tournament/(\d+))"[^>]*>\s*([^<]{4,160})\s*</a>',
        html,
        re.I,
    ):
        path, tid, name = m.group(1), m.group(2), re.sub(r"\s+", " ", m.group(3)).strip()
        if tid in seen:
            continue
        if skip_re.search(name) and not paperish_re.search(name):
            continue
        if not paperish_re.search(name):
            # Keep only clearly paper-looking names to limit noise
            continue
        seen.add(tid)
        candidates.append(
            DiscoveryCandidate(
                name=name,
                players=None,
                source="goldfish",
                source_url="https://www.mtggoldfish.com" + path,
                source_id=tid,
                mtgdecks_search=_mtgdecks_search_hint(name),
                notes="名前のみ（人数なし）。MTGDecks URL を人が確認",
            )
        )
    _ = lookback_days  # listing has no reliable dates per row in HTML; reserved
    return candidates, errors


def load_watchlist(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or (CONFIG_DIR / "paper_watchlist.json")
    if not cfg_path.exists():
        return {"melee_ids": [], "name_keywords": [], "notes": []}
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("failed to load watchlist %s", cfg_path)
        return {"melee_ids": [], "name_keywords": [], "notes": []}
    return data if isinstance(data, dict) else {"melee_ids": [], "name_keywords": [], "notes": []}


def fetch_melee_watchlist_ids(
    ids: list[str | int],
    *,
    min_players: int = 50,
) -> tuple[list[DiscoveryCandidate], list[str]]:
    """Fetch individual Melee tournament pages for curated IDs (player count on page)."""
    errors: list[str] = []
    out: list[DiscoveryCandidate] = []
    enrolled_re = re.compile(r"(\d+)\s+of\s+(\d+)\s+Enrolled Players", re.I)
    title_re = re.compile(r"<title>([^|<]+)", re.I)
    for raw_id in ids:
        eid = str(raw_id).strip()
        if not eid:
            continue
        url = MELEE_VIEW.format(id=eid)
        try:
            html = _http_get_text(url)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            errors.append(f"melee view {eid}: {type(e).__name__}: {e}")
            continue
        m = enrolled_re.search(html)
        players = int(m.group(1)) if m else None
        tm = title_re.search(html)
        name = (tm.group(1).strip() if tm else f"melee-{eid}")
        name = name.replace(" | Melee", "").strip()
        if players is not None and players < min_players:
            continue
        out.append(
            DiscoveryCandidate(
                name=name,
                players=players,
                source="watchlist",
                source_url=url,
                source_id=eid,
                mtgdecks_search=_mtgdecks_search_hint(name),
                notes="config/paper_watchlist.json",
            )
        )
    return out, errors


def run_discovery(
    *,
    min_players: int | None = None,
    use_melee: bool = True,
    use_goldfish: bool = True,
    use_watchlist: bool = True,
    melee_date_windows: list[str] | None = None,
    as_of: date | None = None,
    config: dict[str, Any] | None = None,
) -> DiscoveryResult:
    ensure_dirs()
    cfg = config or load_paper_config()
    mp = int(min_players if min_players is not None else cfg.get("min_players") or 50)
    day = (as_of or today_jst()).isoformat()
    result = DiscoveryResult(as_of=day, min_players=mp)
    merged: dict[str, DiscoveryCandidate] = {}

    def _merge(cands: list[DiscoveryCandidate]) -> None:
        for c in cands:
            key = c.alert_key()
            prev = merged.get(key)
            if prev is None:
                merged[key] = c
                continue
            # Prefer entry with known player count
            if prev.players is None and c.players is not None:
                merged[key] = c
            elif c.players is not None and prev.players is not None and c.players > prev.players:
                merged[key] = c

    if use_melee:
        windows = melee_date_windows or list(
            cfg.get("discovery_melee_date_windows") or ["LastMonth", "ThisWeek", "Last7Days"]
        )
        cands, errs = fetch_melee_pauper(date_windows=windows, min_players=mp)
        result.sources_ok["melee"] = not errs or bool(cands)
        result.errors.extend(errs)
        _merge(cands)
        result.notes.append(
            f"Melee SearchResults formats=Pauper date={','.join(windows)} → "
            f"{len(cands)} candidates ≥{mp}"
        )

    if use_goldfish:
        cands, errs = fetch_goldfish_pauper_names()
        result.sources_ok["goldfish"] = not errs
        result.errors.extend(errs)
        # Goldfish has no counts — keep as hints only (do not claim ≥50)
        for c in cands:
            # Skip if same name already covered by melee with counts
            if any(
                (m.name or "").casefold() == c.name.casefold()
                for m in merged.values()
                if m.source == "melee"
            ):
                continue
            merged[c.alert_key()] = c
        result.notes.append(f"MTGGoldfish name hints (no player count): {len(cands)}")

    if use_watchlist:
        wl = load_watchlist()
        ids = list(wl.get("melee_ids") or [])
        cands, errs = fetch_melee_watchlist_ids(ids, min_players=mp)
        result.sources_ok["watchlist"] = True
        result.errors.extend(errs)
        _merge(cands)
        result.notes.append(f"watchlist melee_ids={len(ids)} matched≥{mp}:{len(cands)}")

    # Sort: known large counts first, then by date desc, then name
    def sort_key(c: DiscoveryCandidate) -> tuple:
        has_pc = 0 if c.players is not None else 1
        return (has_pc, -(c.players or 0), c.start_date or "", c.name.casefold())

    result.candidates = sorted(merged.values(), key=sort_key)
    if not result.candidates:
        result.notes.append("候補なし（ソース失敗 or 期間内に ≥min の大会なし）")
    return result


def write_alert_files(
    result: DiscoveryResult,
    *,
    out_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Write JSON + Markdown under reports/paper/alerts/."""
    ensure_dirs()
    directory = out_dir or PAPER_ALERTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stamp = result.as_of
    json_path = directory / f"{stamp}.json"
    md_path = directory / f"{stamp}.md"

    payload = {
        "as_of": result.as_of,
        "min_players": result.min_players,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources_ok": result.sources_ok,
        "errors": result.errors,
        "notes": result.notes,
        "candidates": [asdict(c) for c in result.candidates],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines: list[str] = [
        f"# Paper 候補大会アラート（{result.as_of}）",
        "",
        f"閾値: **{result.min_players}人以上**（Melee は人数あり。Goldfish は名前ヒントのみ）",
        "",
        "フル分析には **MTGDecks の大会 URL または保存 HTML** を `run-paper-manual` に渡す。",
        "",
    ]
    if result.notes:
        lines.append("## メモ")
        for n in result.notes:
            lines.append(f"- {n}")
        lines.append("")
    if result.errors:
        lines.append("## 取得エラー")
        for e in result.errors:
            lines.append(f"- {e}")
        lines.append("")

    lines.append("## 候補大会")
    lines.append("")
    if not result.candidates:
        lines.append("（なし）")
    else:
        lines.append("| 名前 | 人数 | 日付 | ソース | リンク | MTGDecks 検索案 |")
        lines.append("|------|------|------|--------|--------|-----------------|")
        for c in result.candidates:
            pc = str(c.players) if c.players is not None else "?"
            lines.append(
                f"| {c.name.replace('|', '/')} | {pc} | {c.start_date or '?'} | "
                f"{c.source} | {c.source_url} | {c.mtgdecks_search} |"
            )
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def build_discovery_discord_text(result: DiscoveryResult) -> str:
    """Short Discord ping listing new large-event candidates."""
    counted = [c for c in result.candidates if c.players is not None and c.players >= result.min_players]
    hints = [c for c in result.candidates if c.players is None]
    lines = [
        f"**Pauper Paper 候補** `{result.as_of}`（≥{result.min_players}）",
        "",
    ]
    if not counted and not hints:
        lines.append("期間内の大きな大会候補なし。")
        return "\n".join(lines) + "\n"
    if counted:
        lines.append("**人数つき（Melee 等）**")
        for c in counted[:12]:
            lines.append(f"- {c.name} — **{c.players}人** ({c.start_date or '?'}) <{c.source_url}>")
        if len(counted) > 12:
            lines.append(f"- …他 {len(counted) - 12} 件")
        lines.append("")
    if hints:
        lines.append("**名前ヒント（人数未確認）**")
        for c in hints[:6]:
            lines.append(f"- {c.name} <{c.source_url}>")
        lines.append("")
    lines.append("次の一手: MTGDecks で大会ページを開き URL／HTML を `run-paper-manual` へ。")
    return "\n".join(lines).rstrip() + "\n"
