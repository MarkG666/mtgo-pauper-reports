"""All 20 guild/shard/wedge color words are mapped for color-name audit."""

from __future__ import annotations

from pauper_meta.color_name_audit import expected_colors_for_archetype

# 10 guilds + 10 shards/wedges used as archetype prefixes.
_TWENTY_COLOR_WORDS: dict[str, frozenset[str]] = {
    "Azorius": frozenset("WU"),
    "Dimir": frozenset("UB"),
    "Rakdos": frozenset("BR"),
    "Gruul": frozenset("RG"),
    "Selesnya": frozenset("WG"),
    "Orzhov": frozenset("WB"),
    "Izzet": frozenset("UR"),
    "Golgari": frozenset("BG"),
    "Boros": frozenset("WR"),
    "Simic": frozenset("UG"),
    "Bant": frozenset("GWU"),
    "Esper": frozenset("WUB"),
    "Grixis": frozenset("UBR"),
    "Jund": frozenset("BRG"),
    "Naya": frozenset("RGW"),
    "Abzan": frozenset("WBG"),
    "Jeskai": frozenset("WUR"),
    "Sultai": frozenset("UBG"),
    "Mardu": frozenset("RWB"),
    "Temur": frozenset("GUR"),
}


def test_all_twenty_color_words_mapped() -> None:
    for word, expected in _TWENTY_COLOR_WORDS.items():
        got = expected_colors_for_archetype(f"{word} Example")
        assert got == expected, f"{word}: {got!r} != {expected!r}"
