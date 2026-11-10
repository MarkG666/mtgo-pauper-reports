"""Boros Tribe (Tireless Tribe + Inside Out) must not score as Caw-Gates."""

from __future__ import annotations

from collections import Counter

from pauper_meta.classify import classify_deck


def test_pnascimento_style_tribe_inside_out_is_boros_tribe() -> None:
    """2026-11-05 Top4-style: Hawk + Tribe + Inside Out, no Gate lands."""
    result = classify_deck(
        Counter(
            {
                "Tireless Tribe": 4,
                "Inside Out": 4,
                "Squadron Hawk": 4,
                "Thrilling Discovery": 4,
                "Pursue the Past": 4,
                "Battle Screech": 4,
                "Spirit Mascot": 4,
                "Aurora Eidolon": 4,
                "Prismatic Strands": 2,
                "Mountain": 5,
                "Plains": 5,
                "Wind-Scarred Crag": 4,
                "Boros Garrison": 2,
                "Escape Tunnel": 4,
            }
        )
    )
    assert result.archetype == "Boros Tribe"


def test_true_caw_gates_still_caw() -> None:
    result = classify_deck(
        Counter(
            {
                "Squadron Hawk": 4,
                "Basilisk Gate": 4,
                "Citadel Gate": 4,
                "Heap Gate": 2,
                "Sacred Cat": 4,
                "Guardian of the Guildpact": 2,
                "Prismari Command": 2,
                "Plains": 4,
                "Mountain": 2,
            }
        )
    )
    assert result.archetype == "Caw-Gates"


def test_ww_screech_hawk_without_gates_is_not_caw() -> None:
    result = classify_deck(
        Counter(
            {
                "Squadron Hawk": 4,
                "Battle Screech": 4,
                "Kor Skyfisher": 4,
                "Thraben Inspector": 4,
                "Novice Inspector": 4,
                "Plains": 20,
                "Journey to Nowhere": 4,
                "Prismatic Strands": 4,
            }
        )
    )
    assert result.archetype != "Caw-Gates"
