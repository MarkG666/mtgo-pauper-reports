"""Hawk color-split: Snacker / Munitions / Orzhov (WB) ≠ Mardu (WRB)."""

from __future__ import annotations

from collections import Counter

from pauper_meta.classify import classify_deck
from pauper_meta.color_name_audit import (
    correct_color_named_archetype,
    expected_colors_for_archetype,
)


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


def _orzhov_drugo_shell(**extra: int) -> Counter[str]:
    """Drugo 2026-11-22 Top4-style Orzhov Hawk (WB; no red package)."""
    base = Counter(
        {
            "Glint Hawk": 4,
            "Kor Skyfisher": 4,
            "Refurbished Familiar": 4,
            "Novice Inspector": 4,
            "Dispatch": 4,
            "Lembas": 3,
            "Tithing Blade": 2,
            "Grim Bauble": 2,
            "Hopeless Nightmare": 2,
            "Duress": 2,
            "Fanatical Offering": 2,
            "Okiba-Gang Shinobi": 2,
            "Oroku Saki, Shredder Rising": 2,
            "Eviscerator's Insight": 1,
            "Omen of the Dead": 1,
            "Ancient Den": 3,
            "Vault of Whispers": 3,
            "Orzhov Basilica": 2,
            "Goldmire Bridge": 1,
            "Scoured Barrens": 2,
            "Plains": 3,
            "Swamp": 2,
            "Bojuka Bog": 1,
            "Radiant Fountain": 2,
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


def test_orzhov_hawk_wb_without_red_package() -> None:
    result = classify_deck(_orzhov_drugo_shell())
    assert result.archetype == "Orzhov Hawk"


def test_orzhov_plus_munitions_alone_stays_orzhov() -> None:
    """Makeshift Munitions must not invent Mardu from a WB Hawk shell."""
    result = classify_deck(_orzhov_drugo_shell(**{"Makeshift Munitions": 1}))
    assert result.archetype == "Orzhov Hawk"


def test_orzhov_plus_galvanic_becomes_mardu() -> None:
    result = classify_deck(_orzhov_drugo_shell(**{"Galvanic Blast": 4}))
    assert result.archetype == "Mardu Hawk"


def test_jeskai_hawk_cryogen_relic() -> None:
    result = classify_deck(
        _boros_moxite_shell(**{"Cryogen Relic": 4, "Sneaky Snacker": 0, "Makeshift Munitions": 0})
    )
    assert result.archetype == "Jeskai Hawk"


def test_vandalblast_alone_does_not_add_color_to_hawk() -> None:
    result = classify_deck(_boros_moxite_shell(**{"Vandalblast": 1, "Sneaky Snacker": 0}))
    assert result.archetype == "Boros Hawk"


def test_audit_corrects_mardu_label_to_orzhov() -> None:
    corrected = correct_color_named_archetype("Mardu Hawk", _orzhov_drugo_shell())
    assert corrected == "Orzhov Hawk"


def test_audit_noop_when_orzhov_matches() -> None:
    assert correct_color_named_archetype("Orzhov Hawk", _orzhov_drugo_shell()) is None


def test_orzhov_hawk_expected_colors_are_wb() -> None:
    assert expected_colors_for_archetype("Orzhov Hawk") == frozenset("WB")
    assert expected_colors_for_archetype("Mardu Hawk") == frozenset("RWB")
