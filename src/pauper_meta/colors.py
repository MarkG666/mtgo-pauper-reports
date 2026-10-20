"""Deck color-identity labels (Goldfish-style W/U/B/R/G, WUBRG order)."""

from __future__ import annotations

from collections.abc import Iterable

COLOR_MAP = {
    "COLOR_WHITE": "W",
    "COLOR_BLUE": "U",
    "COLOR_BLACK": "B",
    "COLOR_RED": "R",
    "COLOR_GREEN": "G",
    "WHITE": "W",
    "BLUE": "U",
    "BLACK": "B",
    "RED": "R",
    "GREEN": "G",
    "W": "W",
    "U": "U",
    "B": "B",
    "R": "R",
    "G": "G",
}

WUBRG = ("W", "U", "B", "R", "G")


def normalize_mtgo_colors(raw_colors: Iterable[str] | None) -> set[str]:
    out: set[str] = set()
    for c in raw_colors or []:
        key = str(c).strip().upper()
        letter = COLOR_MAP.get(key) or COLOR_MAP.get(key.replace("COLOR_", ""))
        if letter:
            out.add(letter)
    return out


def is_land_type(card_type: str | None) -> bool:
    if not card_type:
        return False
    return "LAND" in card_type.upper().replace(" ", "")


def color_identity_from_cards(
    cards: Iterable[dict],
    *,
    board: str = "main",
) -> str:
    """
    cards items: {name, qty, board, colors (iterable or 'W,U'), card_type?}
    Prefer non-land mainboard colors. If none, fall back to any colored cards, else C.
    """
    primary: set[str] = set()
    fallback: set[str] = set()
    for c in cards:
        if board and c.get("board") and c.get("board") != board:
            continue
        colors = c.get("colors")
        if isinstance(colors, str):
            letters = normalize_mtgo_colors(colors.replace(",", " ").split())
        else:
            letters = normalize_mtgo_colors(colors)
        if not letters:
            continue
        fallback |= letters
        if not is_land_type(c.get("card_type")):
            primary |= letters
    chosen = primary or fallback
    if not chosen:
        return "C"
    return "".join(letter for letter in WUBRG if letter in chosen)


def display_archetype(archetype: str | None, color_id: str | None) -> str:
    """Unknown → color identity (WUBRG / C). Named archetypes unchanged."""
    name = (archetype or "Unknown").strip() or "Unknown"
    if name == "Unknown":
        return (color_id or "C").strip() or "C"
    return name
