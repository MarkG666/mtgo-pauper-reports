"""Faeries: Vandalblast alone must not add red for color naming."""

from __future__ import annotations

from collections import Counter

from pauper_meta.classify import classify_deck


def _mono_blue_faeries(**extra: int) -> Counter[str]:
    base = Counter(
        {
            "Spellstutter Sprite": 4,
            "Ninja of the Deep Hours": 4,
            "Faerie Seer": 4,
            "Counterspell": 4,
            "Island": 18,
        }
    )
    base.update(extra)
    return base


def test_vandalblast_alone_keeps_mono_blue_faeries() -> None:
    result = classify_deck(_mono_blue_faeries(**{"Vandalblast": 1}))
    assert result.archetype == "Mono-Blue Faeries"


def test_snacker_alone_keeps_mono_blue_faeries() -> None:
    result = classify_deck(_mono_blue_faeries(**{"Sneaky Snacker": 4}))
    assert result.archetype == "Mono-Blue Faeries"


def test_skred_makes_izzet_faeries() -> None:
    result = classify_deck(_mono_blue_faeries(**{"Skred": 4, "Mountain": 4, "Island": 14}))
    assert result.archetype == "Izzet Faeries"
