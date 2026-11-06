"""Turbo Fog vs Cycle Storm: fog mill ≠ Lotus Petal cycling combo."""

from __future__ import annotations

from collections import Counter

from pauper_meta.classify import classify_deck


def _wizardhat_turbo_fog(**extra: int) -> Counter[str]:
    """WizardHat 2026-11-04 Challenge Top8 shell (UG fog mill)."""
    base = Counter(
        {
            "Arcane Denial": 4,
            "Behold the Multiverse": 4,
            "Bojuka Bog": 1,
            "Campfire": 2,
            "Counterspell": 1,
            "Echoing Truth": 1,
            "Fog": 1,
            "Forest": 3,
            "Island": 6,
            "Lórien Revealed": 4,
            "Moment's Peace": 4,
            "Opt": 1,
            "Pieces of the Puzzle": 1,
            "Plunder the Trollshaws": 4,
            "Radiant Fountain": 2,
            "Respite": 1,
            "Simic Growth Chamber": 4,
            "Stream of Thought": 2,
            "Tangle": 3,
            "Tangled Islet": 2,
            "Thornwood Falls": 4,
            "Thought Vessel": 1,
            "Weather the Storm": 4,
        }
    )
    base.update(extra)
    return base


def _cycle_storm_combo(**extra: int) -> Counter[str]:
    """StudentAnselmus-style Lotus Petal + Songs cycling storm."""
    base = Counter(
        {
            "Barren Moor": 4,
            "Cabal Ritual": 3,
            "Dark Ritual": 4,
            "Drannith Healer": 4,
            "Drannith Stinger": 4,
            "Great Furnace": 4,
            "Horror of the Broken Lands": 2,
            "Imposing Vantasaur": 4,
            "Lotus Petal": 4,
            "Monstrous Carabid": 3,
            "Page, Loose Leaf": 4,
            "Reaping the Graves": 4,
            "Rowan's Grim Search": 4,
            "Songs of the Damned": 4,
            "Street Wraith": 4,
            "Vault of Whispers": 4,
        }
    )
    base.update(extra)
    return base


def test_wizardhat_is_turbo_fog_not_cycle_storm() -> None:
    result = classify_deck(_wizardhat_turbo_fog())
    assert result.archetype == "Turbo Fog"


def test_weather_the_storm_alone_is_not_cycle_storm() -> None:
    """Former false positive: WtS×4 hit Cycle Storm threshold with no combo core."""
    result = classify_deck(Counter({"Weather the Storm": 4, "Island": 20, "Forest": 16}))
    assert result.archetype != "Cycle Storm"
    assert result.archetype != "Turbo Fog"


def test_cycle_storm_combo_core_still_cycle_storm() -> None:
    result = classify_deck(_cycle_storm_combo())
    assert result.archetype == "Cycle Storm"


def test_lotus_petal_without_songs_is_not_cycle_storm() -> None:
    """Storage-land / Petal lists without the black cycling engine stay out."""
    result = classify_deck(
        Counter(
            {
                "Lotus Petal": 4,
                "Sandstone Needle": 4,
                "Hickory Woodlot": 4,
                "Manamorphose": 4,
                "Seething Song": 4,
                "Mountain": 20,
            }
        )
    )
    assert result.archetype != "Cycle Storm"


def test_proliferate_with_weather_is_not_turbo_fog() -> None:
    """Poison/proliferate shells that splash WtS without Moment's Peace."""
    result = classify_deck(
        Counter(
            {
                "Contentious Plan": 4,
                "Experimental Augury": 4,
                "Infectious Inquiry": 4,
                "Vivisurgeon's Insight": 4,
                "Weather the Storm": 4,
                "Hickory Woodlot": 4,
                "Peat Bog": 4,
                "Saprazzan Skerry": 4,
                "Pentad Prism": 4,
                "Island": 8,
            }
        )
    )
    assert result.archetype != "Turbo Fog"
    assert result.archetype != "Cycle Storm"


def test_tron_with_fog_package_stays_tron() -> None:
    """Urzatron + fog effects must not be stolen by Turbo Fog resolve."""
    result = classify_deck(
        Counter(
            {
                "Urza's Mine": 4,
                "Urza's Power Plant": 4,
                "Urza's Tower": 4,
                "Crop Rotation": 4,
                "Expedition Map": 4,
                "Fangren Marauder": 2,
                "Breath Weapon": 2,
                "Rolling Hamsphere": 3,
                "Candy Trail": 2,
                "Moment's Peace": 4,
                "Weather the Storm": 4,
                "Tangle": 4,
                "Stream of Thought": 2,
                "Island": 4,
                "Forest": 4,
            }
        )
    )
    assert result.archetype == "Tron"
