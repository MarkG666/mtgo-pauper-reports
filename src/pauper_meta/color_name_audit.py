"""Pre-publish color-name audit for archetypes with a color word in the name.

Verifies that Dimir/Izzet/Azorius/… labels match mainboard splash identity
(Snacker / Vandalblast excluded; Control also ignores Twinned Vision as red).
Auto-corrects via the same family resolvers used at classify time.

Reusable for distinctive-list selection, Top8, and meta tables: call
``audit_and_correct_event_decks`` once per event before report assembly.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass

from .classify import (
    _CONTROL_FAMILY,
    _EPHEMERATE_FAMILY,
    _FAERIES_FAMILY,
    _HAWK_FAMILY,
    _TERROR_FAMILY,
    _affinity_variant_name,
    _control_variant_name,
    _ephemerate_variant_name,
    _faeries_variant_name,
    _hawk_variant_name,
    _is_affinity_core,
    _is_affinity_name,
    _is_ephemerate_core,
    _is_faeries_core,
    _is_hawk_core,
    _is_madness_core,
    _is_madness_name,
    _madness_variant_name,
    _terror_variant_name,
)

log = logging.getLogger(__name__)

# Prefix → expected WUBRG letters (guild / shard / wedge / mono).
_COLOR_WORD_EXPECTED: dict[str, frozenset[str]] = {
    "Mono-White": frozenset("W"),
    "Mono-Blue": frozenset("U"),
    "Mono-Black": frozenset("B"),
    "Mono-Red": frozenset("R"),
    "Mono-Green": frozenset("G"),
    "Azorius": frozenset("WU"),
    "Dimir": frozenset("UB"),
    "Rakdos": frozenset("BR"),
    "Gruul": frozenset("RG"),
    "Selesnya": frozenset("WG"),
    "Orzhov": frozenset("WB"),
    "Izzet": frozenset("UR"),
    "Golgari": frozenset("BG"),
    "Boros": frozenset("WR"),
    "Simic": frozenset("UG"),
    "Esper": frozenset("WUB"),
    "Grixis": frozenset("UBR"),
    "Jund": frozenset("BRG"),
    "Naya": frozenset("RGW"),
    "Bant": frozenset("GWU"),
    "Jeskai": frozenset("WUR"),
    "Mardu": frozenset("RWB"),
    "Temur": frozenset("GUR"),
    "Abzan": frozenset("WBG"),
    "Sultai": frozenset("UBG"),
    "4c": frozenset("WUBR"),  # Hawk 4c = no green
    "5c": frozenset("WUBRG"),
    "BG": frozenset("BG"),
    "UB": frozenset("UB"),
    "U": frozenset("U"),
}


@dataclass(frozen=True)
class ColorAuditFix:
    deck_id: str
    player: str
    old_archetype: str
    new_archetype: str
    reason: str


def _color_word(archetype: str) -> str | None:
    """Longest matching color prefix for a named archetype (e.g. Dimir, Mono-Blue)."""
    name = (archetype or "").strip()
    if not name or name == "Unknown":
        return None
    # Prefer longer keys (Mono-Blue before Mono-*, 4c before digit noise).
    for key in sorted(_COLOR_WORD_EXPECTED, key=len, reverse=True):
        if name == key or name.startswith(key + " ") or name.startswith(key + "-"):
            return key
        # "U Terror" / "UB Terror" / "BG Gardens"
        if name.startswith(key + " "):
            return key
    return None


def expected_colors_for_archetype(archetype: str) -> frozenset[str] | None:
    word = _color_word(archetype)
    if word is None:
        return None
    return _COLOR_WORD_EXPECTED.get(word)


def correct_color_named_archetype(archetype: str, counts: Counter[str]) -> str | None:
    """Re-run the family color resolver when ``archetype`` is in a known split family.

    Returns the corrected name, or None when this audit has nothing to change
    (unknown family, or resolver agrees with the current label).
    """
    name = (archetype or "").strip() or "Unknown"
    corrected: str | None = None

    if name in _CONTROL_FAMILY:
        corrected = _control_variant_name(counts)
    elif name in _FAERIES_FAMILY or (_is_faeries_core(counts) and "Faeries" in name):
        if _is_faeries_core(counts):
            corrected = _faeries_variant_name(counts)
    elif name in _HAWK_FAMILY or (_is_hawk_core(counts) and "Hawk" in name):
        if _is_hawk_core(counts):
            corrected = _hawk_variant_name(counts)
    elif _is_affinity_name(name) or (_is_affinity_core(counts) and "Affinity" in name):
        if _is_affinity_core(counts):
            corrected = _affinity_variant_name(counts)
    elif _is_madness_name(name) or (_is_madness_core(counts) and "Madness" in name):
        if _is_madness_core(counts):
            corrected = _madness_variant_name(counts)
    elif name in _EPHEMERATE_FAMILY:
        if _is_ephemerate_core(counts):
            corrected = _ephemerate_variant_name(counts)
    elif name in _TERROR_FAMILY:
        corrected = _terror_variant_name(counts)

    if corrected is None or corrected == name:
        return None
    return corrected


def _mainboard_counts(conn: sqlite3.Connection, deck_id: str) -> Counter[str]:
    rows = conn.execute(
        "SELECT name, qty FROM cards WHERE deck_id = ? AND board = 'main'",
        (deck_id,),
    ).fetchall()
    return Counter({r["name"]: int(r["qty"]) for r in rows})


def audit_and_correct_event_decks(
    conn: sqlite3.Connection,
    event_id: str,
    *,
    persist: bool = True,
) -> list[ColorAuditFix]:
    """Audit color-named archetypes for one event; optionally write corrected finals.

    Skips decks with an ``archetype_overrides`` row (manual wins). Updates
    ``archetype_auto`` and ``archetype_final`` when persist=True so Top8 / meta /
    distinctive all see the same label.
    """
    overrides = {
        r["deck_id"]
        for r in conn.execute(
            "SELECT deck_id FROM archetype_overrides WHERE deck_id IN "
            "(SELECT id FROM decks WHERE event_id = ?)",
            (event_id,),
        ).fetchall()
    }
    fixes: list[ColorAuditFix] = []
    for row in conn.execute(
        "SELECT id, player, archetype_auto, archetype_final FROM decks WHERE event_id = ?",
        (event_id,),
    ).fetchall():
        if row["id"] in overrides:
            continue
        current = (row["archetype_final"] or row["archetype_auto"] or "Unknown").strip()
        if expected_colors_for_archetype(current) is None and current not in (
            _CONTROL_FAMILY | _FAERIES_FAMILY | _HAWK_FAMILY | _EPHEMERATE_FAMILY | _TERROR_FAMILY
        ):
            # Still try Madness / Affinity via suffix helpers inside correct_*.
            if not (
                _is_madness_name(current)
                or _is_affinity_name(current)
                or "Faeries" in current
                or "Hawk" in current
                or "Control" in current
                or "Terror" in current
                or "Ephemerate" in current
                or "Madness" in current
                or "Affinity" in current
            ):
                continue
        counts = _mainboard_counts(conn, row["id"])
        new_name = correct_color_named_archetype(current, counts)
        if not new_name:
            continue
        reason = f"色名監査: {current} → {new_name}（スプラッシュと名前の不一致）"
        fixes.append(
            ColorAuditFix(
                deck_id=row["id"],
                player=row["player"] or "",
                old_archetype=current,
                new_archetype=new_name,
                reason=reason,
            )
        )
        if persist:
            conn.execute(
                "UPDATE decks SET archetype_auto = ?, archetype_final = ? WHERE id = ?",
                (new_name, new_name, row["id"]),
            )
            log.info(
                "color audit event=%s deck=%s player=%s %s → %s",
                event_id,
                row["id"],
                row["player"],
                current,
                new_name,
            )
    return fixes
