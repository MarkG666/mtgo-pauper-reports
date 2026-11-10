"""U Terror vs UB Terror: Gurmag / black removal rename; Snacker alone does not."""

from __future__ import annotations

from collections import Counter

from pauper_meta.classify import classify_deck


def _mono_u_terror(**extra: int) -> Counter[str]:
    base = Counter(
        {
            "Tolarian Terror": 4,
            "Cryptic Serpent": 4,
            "Brainstorm": 4,
            "Mental Note": 4,
            "Thought Scour": 4,
            "Counterspell": 4,
            "Spell Pierce": 2,
            "Lórien Revealed": 4,
            "Island": 16,
        }
    )
    base.update(extra)
    return base


def test_mono_u_terror_stays_u() -> None:
    result = classify_deck(_mono_u_terror())
    assert result.archetype == "U Terror"


def test_snacker_alone_stays_u_terror() -> None:
    result = classify_deck(_mono_u_terror(**{"Sneaky Snacker": 4}))
    assert result.archetype == "U Terror"


def test_gurmag_makes_ub_terror() -> None:
    result = classify_deck(
        _mono_u_terror(
            **{
                "Gurmag Angler": 3,
                "Cryptic Serpent": 0,
                "Snuff Out": 3,
                "Cast Down": 1,
                "Sneaky Snacker": 4,
                "Contaminated Aquifer": 4,
            }
        )
    )
    assert result.archetype == "UB Terror"


def test_black_removal_without_gurmag_makes_ub() -> None:
    result = classify_deck(
        _mono_u_terror(**{"Cryptic Serpent": 4, "Snuff Out": 2, "Cast Down": 1})
    )
    assert result.archetype == "UB Terror"
