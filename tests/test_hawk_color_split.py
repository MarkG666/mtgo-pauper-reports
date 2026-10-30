"""Hawk color-split: Snacker / Munitions must not turn Boros into Mardu."""

from __future__ import annotations

from collections import Counter

from pauper_meta.classify import classify_deck


def _boros_moxite_shell(**extra: int) -> Counter[str]:
    """seasonofmists 2026-10-28 Top3-style Boros Hawk (Moxite + Snacker)."""
    base = Counter(
        {
            "Glint Hawk": 4,
            "Kor Skyfisher": 4,
            "Melded Moxite": 4,
            "Dispatch": 4,
            "Galvanic Blast": 4,
            "Lightning Bolt": 3,
            "Pursue the Past": 4,
            "Sneaky Snacker": 4,
            "Makeshift Munitions": 1,
            "Lembas": 2,
            "Ancient Den": 3,
            "Great Furnace": 4,
            "Rustvale Bridge": 3,
            "Boros Garrison": 2,
            "Mountain": 2,
            "Plains": 1,
        }
    )
    base.update(extra)
    return base


def test_boros_hawk_snacker_and_munitions_stay_boros() -> None:
    result = classify_deck(_boros_moxite_shell())
    assert result.archetype == "Boros Hawk"


def test_mardu_hawk_refurbished_familiar() -> None:
    result = classify_deck(_boros_moxite_shell(**{"Refurbished Familiar": 3}))
    assert result.archetype == "Mardu Hawk"


def test_jeskai_hawk_cryogen_relic() -> None:
    result = classify_deck(
        _boros_moxite_shell(**{"Cryogen Relic": 4, "Sneaky Snacker": 0, "Makeshift Munitions": 0})
    )
    assert result.archetype == "Jeskai Hawk"


def test_vandalblast_alone_does_not_add_color_to_hawk() -> None:
    result = classify_deck(_boros_moxite_shell(**{"Vandalblast": 1, "Sneaky Snacker": 0}))
    assert result.archetype == "Boros Hawk"
