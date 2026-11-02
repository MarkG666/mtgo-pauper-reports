"""Ephemerate color-split: Jeskai (no black) ≠ Esper (black splash)."""

from __future__ import annotations

from collections import Counter

from pauper_meta.classify import classify_deck


def _jeskai_shell(**extra: int) -> Counter[str]:
    """True WRU Ephemerate bounce (Skred / Wildfire red)."""
    base = Counter(
        {
            "Ephemerate": 3,
            "Mulldrifter": 2,
            "Archaeomancer": 2,
            "Counterspell": 4,
            "Cleansing Wildfire": 4,
            "Lightning Bolt": 2,
            "Skred": 3,
            "Preordain": 4,
            "Mountain": 4,
            "Island": 6,
            "Plains": 2,
        }
    )
    base.update(extra)
    return base


def _esper_shell(**extra: int) -> Counter[str]:
    """BUW Ephemerate package (Brvnx / Heisen01 style)."""
    base = Counter(
        {
            "Ephemerate": 3,
            "Mulldrifter": 3,
            "Archaeomancer": 2,
            "Baleful Strix": 4,
            "Cast Down": 1,
            "Counterspell": 4,
            "Refurbished Familiar": 4,
            "Suffocating Fumes": 1,
            "Preordain": 4,
            "Island": 4,
            "Swamp": 1,
            "Plains": 1,
        }
    )
    base.update(extra)
    return base


def test_jeskai_ephemerate_without_black() -> None:
    result = classify_deck(_jeskai_shell())
    assert result.archetype == "Jeskai Ephemerate"


def test_esper_ephemerate_black_splash() -> None:
    result = classify_deck(_esper_shell())
    assert result.archetype == "Esper Ephemerate"


def test_snacker_alone_does_not_make_esper() -> None:
    """Sneaky Snacker is globally excluded from color naming."""
    result = classify_deck(_jeskai_shell(**{"Sneaky Snacker": 2}))
    assert result.archetype == "Jeskai Ephemerate"


def test_dimir_control_promotes_to_esper_with_package() -> None:
    """Brvnx 2026-09-21 style: Dimir Control score, but Ephemerate package wins."""
    result = classify_deck(
        Counter(
            {
                "Ephemerate": 3,
                "Mulldrifter": 3,
                "Archaeomancer": 2,
                "Counterspell": 4,
                "Snuff Out": 4,
                "Cast Down": 1,
                "Suffocating Fumes": 1,
                "Extract a Confession": 1,
                "Brainstorm": 3,
                "Augur of Bolas": 3,
                "Murmuring Mystic": 2,
                "Contaminated Aquifer": 3,
                "Deep Analysis": 0,
                "Gurmag Angler": 0,
                "Spell Pierce": 1,
                "Island": 6,
                "Plains": 2,
                "Swamp": 1,
            }
        )
    )
    assert result.archetype == "Esper Ephemerate"


def test_familiars_not_stolen_by_ephemerate_split() -> None:
    """Sunscape Familiar + God-Pharaoh's Faithful stays Familiars (WU)."""
    result = classify_deck(
        Counter(
            {
                "Sunscape Familiar": 4,
                "God-Pharaoh's Faithful": 4,
                "Ephemerate": 4,
                "Mulldrifter": 3,
                "Archaeomancer": 3,
                "Snap": 4,
                "Prohibit": 3,
                "Island": 8,
                "Plains": 4,
            }
        )
    )
    assert result.archetype == "Familiars"
