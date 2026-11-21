"""Control color-split: Dimir ≠ Izzet ≠ Grixis (Snacker / Vandalblast / Twinned Vision)."""

from __future__ import annotations

from collections import Counter

from pauper_meta.classify import classify_deck
from pauper_meta.color_name_audit import correct_color_named_archetype


def _dimir_shell(**extra: int) -> Counter[str]:
    base = Counter(
        {
            "Counterspell": 4,
            "Brainstorm": 4,
            "Murmuring Mystic": 2,
            "Snuff Out": 4,
            "Cast Down": 2,
            "Baleful Strix": 4,
            "Preordain": 4,
            "Island": 10,
            "Swamp": 4,
            "Contaminated Aquifer": 4,
        }
    )
    base.update(extra)
    return base


def _izzet_shell(**extra: int) -> Counter[str]:
    """ChaoticMTG-style UR Control (Skred / Galvanic Discharge / Breath Weapon)."""
    base = Counter(
        {
            "Counterspell": 4,
            "Brainstorm": 4,
            "Murmuring Mystic": 3,
            "Skred": 4,
            "Galvanic Discharge": 4,
            "Breath Weapon": 2,
            "Twinned Vision": 4,
            "Augur of Bolas": 3,
            "Preordain": 4,
            "Snow-Covered Island": 10,
            "Snow-Covered Mountain": 3,
            "Volatile Fjord": 4,
            "Perilous Landscape": 2,
        }
    )
    base.update(extra)
    return base


def test_dimir_control_black_shell() -> None:
    result = classify_deck(_dimir_shell())
    assert result.archetype == "Dimir Control"


def test_izzet_control_red_shell() -> None:
    result = classify_deck(_izzet_shell())
    assert result.archetype == "Izzet Control"


def test_grixis_control_black_and_red() -> None:
    result = classify_deck(
        _dimir_shell(**{"Skred": 4, "Mountain": 2, "Island": 8, "Swamp": 2})
    )
    assert result.archetype == "Grixis Control"


def test_vandalblast_alone_keeps_dimir_control() -> None:
    result = classify_deck(_dimir_shell(**{"Vandalblast": 2}))
    assert result.archetype == "Dimir Control"


def test_snacker_alone_keeps_dimir_control() -> None:
    result = classify_deck(_dimir_shell(**{"Sneaky Snacker": 2}))
    assert result.archetype == "Dimir Control"


def test_twinned_vision_alone_does_not_make_izzet() -> None:
    """UB Control + Twinned Vision loot stays Dimir (VitorCarvalho01-style)."""
    result = classify_deck(_dimir_shell(**{"Twinned Vision": 3}))
    assert result.archetype == "Dimir Control"


def test_audit_corrects_dimir_label_when_red_present() -> None:
    corrected = correct_color_named_archetype("Dimir Control", _izzet_shell())
    assert corrected == "Izzet Control"


def test_audit_noop_when_label_matches() -> None:
    assert correct_color_named_archetype("Izzet Control", _izzet_shell()) is None
    assert correct_color_named_archetype("Dimir Control", _dimir_shell()) is None
