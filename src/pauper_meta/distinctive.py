"""Select 3–5 distinctive lists (Top16; main+side diffs vs ~14d same-arch averages)."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .paths import CONFIG_DIR, ensure_dirs

DEFAULT_DISTINCTIVE_CONFIG = {
    "window_days": 14,
    "widen_days": 28,
    "min_archetype_lists": 3,
}

# Scoring: main is primary; side adds a weighted boost (and can carry a bland-main deck).
SIDE_SCORE_WEIGHT = 0.55
MAIN_THRESHOLD = 6.0
SIDE_THRESHOLD = 5.0  # side-only interesting (when main below threshold)


def load_distinctive_config(path: Path | None = None) -> dict:
    ensure_dirs()
    cfg_path = path or (CONFIG_DIR / "distinctive.json")
    out = dict(DEFAULT_DISTINCTIVE_CONFIG)
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            for k in DEFAULT_DISTINCTIVE_CONFIG:
                if k in data:
                    out[k] = int(data[k])
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return out


@dataclass
class CardDelta:
    side: str  # '+' | '-'
    name: str
    qty: int


@dataclass
class DistinctiveFlag:
    deck_id: str
    player: str
    event_id: str
    event_name: str
    archetype: str
    finish_rank: int | None
    reason: str
    score: float
    main_diffs: list[CardDelta] = field(default_factory=list)
    side_diffs: list[CardDelta] = field(default_factory=list)
    has_sideboard: bool = True
    side_skipped_reason: str = ""
    deck_url: str | None = None
    link_source: str = "none"
    baseline_note: str = ""
    extras: dict = field(default_factory=dict)

    # Back-compat for older renderers
    @property
    def card_diffs(self) -> list[CardDelta]:
        return self.main_diffs


def _board(cards: list[tuple[str, int]]) -> Counter[str]:
    return Counter({n: q for n, q in cards})


def _diff_cards(
    deck: Counter[str],
    prototype: Counter[str],
    *,
    normalize_to: int = 60,
) -> tuple[float, list[CardDelta]]:
    names = set(deck) | set(prototype)
    score = 0.0
    added: list[CardDelta] = []
    removed: list[CardDelta] = []
    for name in names:
        d = deck.get(name, 0)
        p = prototype.get(name, 0)
        delta = d - p
        if delta == 0:
            continue
        score += abs(delta)
        if delta > 0:
            added.append(CardDelta(side="+", name=name, qty=delta))
        else:
            removed.append(CardDelta(side="-", name=name, qty=abs(delta)))
    size = sum(deck.values()) or normalize_to
    score = score * (normalize_to / size)
    added.sort(key=lambda x: (-x.qty, x.name))
    removed.sort(key=lambda x: (-x.qty, x.name))
    return score, added[:5] + removed[:5]


def _is_color_only(label: str) -> bool:
    if not label:
        return False
    return re.fullmatch(r"[WUBRGC]+", label) is not None and label != "Unknown"


def _rounded_avg_prototype(boards: list[Counter[str]]) -> Counter[str]:
    if not boards:
        return Counter()
    totals: Counter[str] = Counter()
    for b in boards:
        totals.update(b)
    n = len(boards)
    proto: Counter[str] = Counter()
    for name, total in totals.items():
        avg = round(total / n)
        if avg > 0:
            proto[name] = avg
    return proto


def build_historical_prototypes(
    conn: sqlite3.Connection,
    *,
    as_of: date,
    window_days: int,
    widen_days: int,
    min_lists: int,
    display_fn,
) -> tuple[dict[str, Counter[str]], dict[str, Counter[str]], dict[str, str]]:
    """
    Build rounded-avg **main** and **side** prototypes per archetype over the date window.

    Returns (main_prototypes, side_prototypes, notes_by_arch).
    Side proto is only built from decks that have at least one sideboard card in the window.
    """
    start_wide = (as_of - timedelta(days=widen_days - 1)).isoformat()
    end = as_of.isoformat()
    rows = conn.execute(
        """
        SELECT d.id, d.event_id, d.archetype_final, d.color_identity, e.start_time
        FROM decks d
        JOIN events e ON e.id = d.event_id
        WHERE substr(e.start_time, 1, 10) BETWEEN ? AND ?
        """,
        (start_wide, end),
    ).fetchall()

    by_arch_main: dict[str, list[tuple[str, Counter[str]]]] = defaultdict(list)
    by_arch_side: dict[str, list[tuple[str, Counter[str]]]] = defaultdict(list)

    for r in rows:
        ed = (r["start_time"] or "")[:10]
        label = display_fn(r)
        if _is_color_only(label) or label == "Unknown":
            continue
        cards = conn.execute(
            "SELECT name, qty, board FROM cards WHERE deck_id = ?",
            (r["id"],),
        ).fetchall()
        main = Counter()
        side = Counter()
        for c in cards:
            if c["board"] == "main":
                main[c["name"]] += int(c["qty"])
            elif c["board"] == "side":
                side[c["name"]] += int(c["qty"])
        by_arch_main[label].append((ed, main))
        if side:
            by_arch_side[label].append((ed, side))

    start_primary = (as_of - timedelta(days=window_days - 1)).isoformat()
    main_protos: dict[str, Counter[str]] = {}
    side_protos: dict[str, Counter[str]] = {}
    notes: dict[str, str] = {}

    all_archs = set(by_arch_main) | set(by_arch_side)
    for arch in all_archs:
        main_items = by_arch_main.get(arch, [])
        side_items = by_arch_side.get(arch, [])

        main_primary = [m for ed, m in main_items if start_primary <= ed <= end]
        main_wide = [m for ed, m in main_items if start_wide <= ed <= end]
        side_primary = [m for ed, m in side_items if start_primary <= ed <= end]
        side_wide = [m for ed, m in side_items if start_wide <= ed <= end]

        # Side prototypes tracked separately for diffs; baseline line uses main count
        # (or max(main,side) if somehow only side exists — prefer main).
        window_label = "直近2週間" if window_days == 14 else f"直近{window_days}日"
        main_n = 0
        if len(main_primary) >= min_lists:
            main_protos[arch] = _rounded_avg_prototype(main_primary)
            main_n = len(main_primary)
        elif len(main_wide) >= min_lists:
            main_protos[arch] = _rounded_avg_prototype(main_wide)
            main_n = len(main_wide)

        side_n = 0
        if len(side_primary) >= min_lists:
            side_protos[arch] = _rounded_avg_prototype(side_primary)
            side_n = len(side_primary)
        elif len(side_wide) >= min_lists:
            side_protos[arch] = _rounded_avg_prototype(side_wide)
            side_n = len(side_wide)

        if main_n > 0:
            notes[arch] = f"{window_label}の同アーキ {main_n}本"
        elif side_n > 0:
            notes[arch] = f"{window_label}の同アーキ {side_n}本"
        else:
            notes[arch] = f"同アーキ履歴不足（{len(main_wide)}本）→ 差分スキップ"

    return main_protos, side_protos, notes


def select_distinctive(
    decks: list[dict],
    *,
    main_prototypes: dict[str, Counter[str]] | None = None,
    side_prototypes: dict[str, Counter[str]] | None = None,
    baseline_notes: dict[str, str] | None = None,
    # legacy alias
    historical_prototypes: dict[str, Counter[str]] | None = None,
    min_count: int = 3,
    max_count: int = 5,
    rare_archetype_threshold: int = 1,
    max_finish_rank: int = 16,
) -> list[DistinctiveFlag]:
    """
    Top16 only. Same-archetype diffs vs historical main/side averages.

    Scoring: score = main_diff + SIDE_SCORE_WEIGHT * side_diff (+ Top8 bonus).
    Eligible if main_diff >= MAIN_THRESHOLD, or side_diff >= SIDE_THRESHOLD (side-driven).
    """
    eligible = [
        d
        for d in decks
        if d.get("finish_rank") is not None and int(d["finish_rank"]) <= max_finish_rank
    ]
    if not eligible:
        return []

    main_protos = main_prototypes or historical_prototypes or {}
    side_protos = side_prototypes or {}
    notes = baseline_notes or {}

    arch_counts_t16: Counter[str] = Counter()
    for d in eligible:
        arch_counts_t16[d.get("archetype") or "Unknown"] += 1

    candidates: list[DistinctiveFlag] = []

    for d in eligible:
        arch = d.get("archetype") or "Unknown"
        main = _board(d.get("main_cards") or [])
        side_cards = d.get("side_cards")
        has_side_data = side_cards is not None
        side = _board(side_cards or []) if has_side_data else Counter()
        rank = int(d["finish_rank"])
        url = d.get("deck_url")
        link_source = d.get("link_source") or "none"
        bnote = notes.get(arch, "")

        main_diffs: list[CardDelta] = []
        side_diffs: list[CardDelta] = []
        main_score = 0.0
        side_score = 0.0
        side_skip = ""

        if arch in main_protos and not _is_color_only(arch) and arch != "Unknown":
            main_score, main_diffs = _diff_cards(main, main_protos[arch], normalize_to=60)

        if not has_side_data:
            side_skip = "サイドボード未取得のためサイド表スキップ"
        elif not side:
            side_skip = "サイドボード0枚のためサイド表スキップ"
        elif arch not in side_protos:
            side_skip = "同アーキのサイド履歴不足のためサイド表スキップ"
        elif not _is_color_only(arch) and arch != "Unknown":
            side_score, side_diffs = _diff_cards(side, side_protos[arch], normalize_to=15)

        combined = main_score + SIDE_SCORE_WEIGHT * side_score
        interesting = main_score >= MAIN_THRESHOLD or side_score >= SIDE_THRESHOLD

        def _flag(
            reason: str,
            score: float,
            *,
            md: list[CardDelta] | None = None,
            sd: list[CardDelta] | None = None,
            skip: str = "",
        ) -> DistinctiveFlag:
            return DistinctiveFlag(
                deck_id=d["deck_id"],
                player=d.get("player") or "",
                event_id=d["event_id"],
                event_name=d.get("event_name") or "",
                archetype=arch,
                finish_rank=rank,
                reason=reason,
                score=score,
                main_diffs=md or [],
                side_diffs=sd or [],
                has_sideboard=has_side_data and bool(side) and not skip,
                side_skipped_reason=skip,
                deck_url=url,
                link_source=link_source,
                baseline_note=bnote,
            )

        if interesting and (arch in main_protos or arch in side_protos):
            if main_score >= MAIN_THRESHOLD and side_score >= SIDE_THRESHOLD:
                reason = "同アーキ内カード差分（メイン＋サイド・過去窓平均）"
            elif main_score >= MAIN_THRESHOLD:
                reason = "同アーキ内カード差分（メイン・過去窓平均）"
            else:
                reason = "同アーキ内カード差分（サイド主導・過去窓平均）"
            candidates.append(
                _flag(
                    reason,
                    1000 + combined + (8 if rank <= 8 else 0),
                    md=main_diffs,
                    sd=side_diffs,
                    skip=side_skip,
                )
            )

        if rank <= 8 and arch_counts_t16.get(arch, 0) <= rare_archetype_threshold:
            candidates.append(_flag("低頻度アーキの Top8", 500 + (9 - rank) * 10, skip=side_skip))

        if rank <= 8 and (arch == "Unknown" or _is_color_only(arch)):
            candidates.append(
                _flag("色ラベルのみの Top8（要レビュー）", 400 + (9 - rank) * 5, skip=side_skip)
            )

    best: dict[str, DistinctiveFlag] = {}
    for c in candidates:
        prev = best.get(c.deck_id)
        if prev is None or c.score > prev.score:
            best[c.deck_id] = c

    ranked = sorted(best.values(), key=lambda x: (-x.score, x.finish_rank or 99, x.player))
    selected: list[DistinctiveFlag] = []
    seen_arch: set[str] = set()
    for c in ranked:
        if len(selected) >= max_count:
            break
        if c.archetype in seen_arch and len(selected) >= min_count:
            if "同アーキ内カード差分" not in c.reason:
                continue
        selected.append(c)
        seen_arch.add(c.archetype)

    if len(selected) < min_count:
        for c in ranked:
            if c.deck_id in {s.deck_id for s in selected}:
                continue
            selected.append(c)
            if len(selected) >= min_count:
                break

    return selected[:max_count]
