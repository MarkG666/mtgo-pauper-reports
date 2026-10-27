"""Madness color-split: Mono-Red ≠ Rakdos ≠ Boros (Snacker alone does not add color)."""

from __future__ import annotations

from collections import Counter

from pauper_meta.classify import classify_deck


def _mono_red_shell(**extra: int) -> Counter[str]:
    base = Counter(
        {
            "Voldaren Epicure": 4,
            "Sneaky Snacker": 4,
            "Grab the Prize": 4,
            "Highway Robbery": 4,
            "Faithless Looting": 4,
            "Fiery Temper": 4,
            "Lightning Bolt": 4,
            "Mountain": 16,
        }
    )
    base.update(extra)
    return base


def test_mono_red_madness_snacker_does_not_add_black() -> None:
    result = classify_deck(_mono_red_shell())
    assert result.archetype == "Mono-Red Madness"


def test_rakdos_madness_kitchen_imp_package() -> None:
    """DarioSmp-style: Imp / Kiss / Alms → Rakdos (not Mono-Red)."""
    result = classify_deck(
        _mono_red_shell(
            **{
                "Kitchen Imp": 4,
                "Vampire's Kiss": 4,
                "Alms of the Vein": 4,
                "Jagged Barrens": 4,
                "Swamp": 3,
                "Mountain": 8,
            }
        )
    )
    assert result.archetype == "Rakdos Madness"


def test_boros_madness_tireless_tribe() -> None:
    result = classify_deck(
        Counter(
            {
                "Tireless Tribe": 4,
                "Thrilling Discovery": 4,
                "Pursue the Past": 4,
                "Faithless Looting": 4,
                "Sneaky Snacker": 4,
                "Lightning Bolt": 4,
                "Aurora Eidolon": 4,
                "Spirit Mascot": 4,
                "Mountain": 4,
                "Plains": 4,
            }
        )
    )
    assert result.archetype == "Boros Madness"


def test_twinned_vision_does_not_make_izzet() -> None:
    result = classify_deck(_mono_red_shell(**{"Twinned Vision": 1}))
    assert result.archetype == "Mono-Red Madness"
