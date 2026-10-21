"""Heuristic archetype classifier (existing-site-ish Pauper names)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

# (canonical_name, weight, signature_cards)
# Weights are additive; highest score wins. Ties → first listed among max.
SIGNATURES: list[tuple[str, list[tuple[str, int]]]] = [
    (
        "Grixis Affinity",
        [
            ("Reckoner's Bargain", 5),
            ("Deadly Dispute", 3),
            ("Krark-Clan Shaman", 4),
            ("Myr Enforcer", 3),
            ("Sojourner's Companion", 2),
            ("Blood Fountain", 2),
            ("Refurbished Familiar", 3),
            ("Makeshift Munitions", 2),
        ],
    ),
    (
        "Jeskai Ephemerate",
        [
            ("Ephemerate", 5),
            ("Mulldrifter", 3),
            ("Solitary Confinement", 4),
            ("Wall of Roots", 2),
            ("Kenku Artificer", 2),
            ("Sunscape Familiar", 3),
            ("Archaeomancer", 2),
        ],
    ),
    (
        "Dimir Control",
        [
            ("Counterspell", 2),
            ("Snuff Out", 3),
            ("Gurmag Angler", 3),
            ("Brainstorm", 2),
            ("Spell Pierce", 1),
            ("Murmuring Mystic", 3),
            ("Deep Analysis", 2),
            ("Contaminated Aquifer", 2),
        ],
    ),
    (
        "Dimir Faeries",
        [
            ("Ninja of the Deep Hours", 5),
            ("Spellstutter Sprite", 5),
            ("Faerie Seer", 3),
            ("Moon-Cursed Cleric", 2),
            ("Snuff Out", 2),
            ("Counterspell", 2),
        ],
    ),
    (
        "Mono-Red Madness",
        [
            ("Voldaren Epicure", 3),
            ("Sneaky Snacker", 4),
            ("Grab the Prize", 4),
            ("Highway Robbery", 3),
            ("Faithless Looting", 3),
            ("Galvanic Blast", 2),
            ("Kessig Flamebreather", 3),
        ],
    ),
    (
        "Burn",
        [
            ("Lightning Bolt", 3),
            ("Needle Drop", 4),
            ("Fireblast", 4),
            ("Lava Dart", 3),
            ("Rift Bolt", 3),
            ("Searing Blaze", 2),
            ("Chain Lightning", 2),
        ],
    ),
    (
        "Walls Combo",
        [
            ("Overgrown Battlement", 4),
            ("Axebane Guardian", 5),
            ("Secret Door", 3),
            ("Drift of Phantasms", 3),
            ("Lead the Stampede", 3),
            ("Galvanic Alchemist", 3),
            ("Shield-Wall Sentinel", 2),
            ("Valakut Invoker", 2),
        ],
    ),
    (
        "Bogles",
        [
            ("Slippery Bogle", 5),
            ("Gladecover Scout", 5),
            ("Ethereal Armor", 4),
            ("Rancor", 3),
            ("Ancestral Mask", 4),
            ("Armadillo Cloak", 3),
        ],
    ),
    (
        "Tron",
        [
            ("Urza's Mine", 4),
            ("Urza's Power Plant", 4),
            ("Urza's Tower", 4),
            ("Crop Rotation", 3),
            ("Expedition Map", 3),
            ("Fangren Marauder", 2),
            ("Breath Weapon", 2),
            ("Rolling Hamsphere", 3),
            ("Candy Trail", 2),
        ],
    ),
    (
        "Poison",
        [
            ("Glistener Elf", 4),
            ("Ichorclaw Myr", 4),
            ("Vines of Vastwood", 3),
            ("Might of Old Krosa", 3),
            ("Snakeskin Veil", 2),
            ("Llanowar Augur", 3),
        ],
    ),
    (
        "U Terror",
        [
            ("Gurmag Angler", 3),
            ("Snuff Out", 2),
            ("Troll of Khazad-dum", 4),
            ("Sneaky Snacker", 2),
            ("Brainstorm", 2),
            ("Mental Note", 3),
            ("Thought Scour", 2),
        ],
    ),
    (
        "Broodscale Combo",
        [
            ("Malevolent Rumble", 3),
            ("Writhing Chrysalis", 4),
            ("Implement of Combustion", 3),
            ("Ichor Wellspring", 2),
            ("Making of a Masterpiece", 3),
            ("Deadly Dispute", 2),
            ("Experimental Synthesizer", 2),
        ],
    ),
    # Former "Kuldotha Aggro" (Rebirth banned) → split Rally vs Axe.
    # Shared goblin/synth fillers; axis cards decide. Mixed/ambiguous → Rally.
    (
        "Mono Red Rally",
        [
            ("Rally at the Hornburg", 5),
            ("Burning-Tree Emissary", 5),
            ("Goblin Bushwhacker", 3),
            ("Goblin Tomb Raider", 3),
            ("Experimental Synthesizer", 2),
            ("Galvanic Blast", 2),
            ("Voldaren Epicure", 2),
            ("Clockwork Percussionist", 2),
        ],
    ),
    (
        "Mono Red Axe",
        [
            ("Inventor's Axe", 5),
            ("Gingerbrute", 5),
            ("Goblin Tomb Raider", 2),
            ("Goblin Bushwhacker", 2),
            ("Experimental Synthesizer", 2),
            ("Clockwork Percussionist", 2),
            ("Galvanic Blast", 1),
            ("Reckless Lackey", 2),
        ],
    ),
    (
        "Elves",
        [
            ("Quirion Ranger", 3),
            ("Priest of Titania", 4),
            ("Timberwatch Elf", 4),
            ("Lys Alana Huntmaster", 3),
            ("Wellwisher", 2),
            ("Nettle Sentinel", 3),
            ("Jaspera Sentinel", 2),
        ],
    ),
    (
        "Caw-Gates",
        [
            ("Squadron Hawk", 5),
            ("Basilisk Gate", 4),
            ("Citadel Gate", 3),
            ("Heap Gate", 2),
            ("Sacred Cat", 3),
            ("Guardian of the Guildpact", 3),
            ("Prismari Command", 2),
        ],
    ),
    (
        "Spy Combo",
        [
            ("Mesmeric Fiend", 3),
            ("Hitchclaw Recluse", 2),
            ("Masked Vandal", 2),
            ("Generous Ent", 2),
            ("Lotleth Giant", 3),
            ("Dread Return", 4),
            ("Cabal Ritual", 3),
            ("Songs of the Damned", 4),
        ],
    ),
    (
        "Cycle Storm",
        [
            ("Lotus Petal", 3),
            ("Dark Ritual", 3),
            ("Songs of the Damned", 3),
            ("Streambed Aquitects", 2),
            ("Drannith Ruins", 2),
            ("Rites of Initiation", 3),
            ("Weather the Storm", 2),
        ],
    ),
    (
        "Familiars",
        [
            ("Sunscape Familiar", 5),
            ("God-Pharaoh's Faithful", 4),
            ("Snap", 3),
            ("Prohibit", 2),
            ("Archaeomancer", 3),
            ("Ephemerate", 3),
            ("Mulldrifter", 2),
        ],
    ),
    (
        "Gruul Ramp",
        [
            ("Annoyed Altisaur", 4),
            ("Board the Weatherlight", 3),
            ("Writhing Chrysalis", 3),
            ("Malevolent Rumble", 2),
            ("Avenging Hunter", 3),
            ("Arbor Elf", 2),
            ("Utopia Sprawl", 3),
        ],
    ),
    (
        "Mono-Blue Faeries",
        [
            ("Ninja of the Deep Hours", 4),
            ("Spellstutter Sprite", 4),
            ("Faerie Seer", 3),
            ("Moon-Cursed Cleric", 2),
            ("Counterspell", 2),
            ("Spire Golem", 3),
        ],
    ),
    (
        "BG Gardens",
        [
            ("Cuombajj Witches", 4),
            ("Suffocating Fumes", 2),
            ("Spinning Darkness", 3),
            ("Okiba-Gang Shinobi", 3),
            ("Crypt Rats", 3),
            ("Chainer's Edict", 2),
        ],
    ),
    # Former "Glitters" → Hawk family (Boros base; color via _resolve_hawk_variant).
    # Glint Hawk is required to score (see classify_deck). Snacker does not add a color.
    # Refurbished Familiar / Cryogen Relic are splash signals, not signature weights
    # (Familiar also scores Grixis Affinity — Hawk core must win when Glint Hawk is present).
    (
        "Boros Hawk",
        [
            ("Glint Hawk", 6),
            ("Kor Skyfisher", 4),
            ("Lembas", 3),
            ("Prophetic Prism", 3),
            ("Cryogen Relic", 3),
            ("Experimental Synthesizer", 2),
            ("Navigator's Compass", 2),
            ("Melded Moxite", 2),
            ("Thraben Charm", 2),
            ("Cleansing Wildfire", 2),
            ("Dispatch", 2),
            ("Galvanic Blast", 1),
        ],
    ),
]

# Non-land splash signals for Hawk color naming (Sneaky Snacker intentionally omitted).
_HAWK_BLACK_SPLASH = frozenset(
    {
        "Refurbished Familiar",
        "Cast Down",
        "Tithing Blade",
        "Grim Bauble",
        "Blood Fountain",
        "Eviscerator's Insight",
        "Fanatical Offering",
        "Okiba-Gang Shinobi",
        "Thorn of the Black Rose",
        "Nihil Spellbomb",
        "Suffocating Fumes",
        "Mukotai Ambusher",
        "Black Mage's Rod",
        "Oroku Saki, Shredder Rising",
        "Deadly Dispute",
        "Snuff Out",
        "Duress",
        "Makeshift Munitions",
        "Mardu Devotee",
        "Omen of the Dead",
        "Reckoner's Bargain",
    }
)
_HAWK_BLUE_SPLASH = frozenset(
    {
        "Cryogen Relic",
        "Thoughtcast",
        "Baleful Strix",
        "Hydroblast",
        "Blue Elemental Blast",
        "Counterspell",
        "Brainstorm",
        "Lorien Revealed",
        "Spell Pierce",
        "Force Spike",
        "Metallic Rebuke",
        "Dream Stalker",
    }
)
_HAWK_GREEN_SPLASH = frozenset(
    {
        "Weather the Storm",
        "Pulse of Murasa",
        "Arbor Elf",
        "Utopia Sprawl",
        "Annoyed Altisaur",
        "Avenging Hunter",
        "Snakeskin Veil",
        "Ram Through",
    }
)
_HAWK_FAMILY = frozenset(
    {"Boros Hawk", "Mardu Hawk", "Jeskai Hawk", "4c Hawk", "Naya Hawk", "5c Hawk"}
)


@dataclass
class Classification:
    archetype: str
    score: int
    runners_up: list[tuple[str, int]]


def mainboard_counts(cards: list[tuple[str, int, str]] | dict[str, int]) -> Counter[str]:
    """cards: iterable of (name, qty, board) or mainboard name→qty map."""
    c: Counter[str] = Counter()
    if isinstance(cards, dict):
        c.update(cards)
        return c
    for name, qty, board in cards:
        if board == "main":
            c[name] += qty
    return c


def _rally_axis(counts: Counter[str]) -> int:
    return counts.get("Rally at the Hornburg", 0) + counts.get("Burning-Tree Emissary", 0)


def _axe_axis(counts: Counter[str]) -> int:
    return counts.get("Inventor's Axe", 0) + counts.get("Gingerbrute", 0)


def _resolve_mono_red_aggro(counts: Counter[str], best_name: str) -> str:
    """Rally preferred; Axe only when Axe-axis is clear and Rally-axis absent."""
    if best_name not in ("Mono Red Rally", "Mono Red Axe"):
        return best_name
    rally = _rally_axis(counts)
    axe = _axe_axis(counts)
    if rally > 0:
        # Rally present, or mixed Rally+Axe → Mono Red Rally
        return "Mono Red Rally"
    if axe >= 2:
        # Axe/Gingerbrute clearly present, no Rally-axis
        return "Mono Red Axe"
    # Goblin/synth mush or weak Axe signal → default Rally
    return "Mono Red Rally"


def _is_hawk_core(counts: Counter[str]) -> bool:
    """True Glint Hawk bounce package (not white-weenie Kor Skyfisher lists)."""
    return counts.get("Glint Hawk", 0) >= 2


def _hawk_variant_name(counts: Counter[str]) -> str:
    """Boros base; black → Mardu; blue → Jeskai; both → 4c; green → Naya/5c.

    Sneaky Snacker alone does not add a color for naming.
    """
    has_b = any(counts.get(c, 0) > 0 for c in _HAWK_BLACK_SPLASH)
    has_u = any(counts.get(c, 0) > 0 for c in _HAWK_BLUE_SPLASH)
    has_g = any(counts.get(c, 0) > 0 for c in _HAWK_GREEN_SPLASH)
    if has_b and has_u and has_g:
        return "5c Hawk"
    if has_b and has_u:
        return "4c Hawk"
    if has_b and has_g:
        return "4c Hawk"
    if has_u and has_g:
        return "4c Hawk"
    if has_g:
        return "Naya Hawk"
    if has_b:
        return "Mardu Hawk"
    if has_u:
        return "Jeskai Hawk"
    return "Boros Hawk"


def _resolve_hawk_family(counts: Counter[str], best_name: str, scores: list[tuple[str, int]]) -> str:
    """Prefer Hawk over Affinity/etc. when Glint Hawk core is present; apply color name."""
    if _is_hawk_core(counts):
        return _hawk_variant_name(counts)
    # Signature may still fire weakly without enough Hawks — do not keep as Hawk.
    if best_name in _HAWK_FAMILY or best_name == "Boros Hawk":
        # Fall through to next-best non-Hawk score if any.
        for name, score in scores:
            if name not in _HAWK_FAMILY and name != "Boros Hawk" and score >= 8:
                return name
        return "Unknown"
    return best_name


def classify_deck(main: Counter[str] | dict[str, int]) -> Classification:
    counts = Counter(main)
    scores: list[tuple[str, int]] = []
    for name, sigs in SIGNATURES:
        # Hawk signature only counts when Glint Hawk is present (avoids WW false positives).
        if name == "Boros Hawk" and counts.get("Glint Hawk", 0) < 2:
            continue
        score = 0
        for card, weight in sigs:
            qty = counts.get(card, 0)
            if qty > 0:
                score += weight * min(qty, 4)
        if score > 0:
            scores.append((name, score))
    scores.sort(key=lambda x: (-x[1], x[0]))
    if not scores or scores[0][1] < 8:
        # Still promote clear Hawk cores that lost the threshold race only on paper.
        if _is_hawk_core(counts):
            name = _hawk_variant_name(counts)
            hawk_score = next((s for n, s in scores if n == "Boros Hawk"), 0)
            return Classification(archetype=name, score=max(hawk_score, 8), runners_up=scores[:3])
        return Classification(archetype="Unknown", score=scores[0][1] if scores else 0, runners_up=scores[:3])
    best_name, best_score = scores[0]
    # Disambiguate Dimir Faeries vs Dimir Control / Mono-Blue
    if best_name == "Dimir Control" and counts.get("Spellstutter Sprite", 0) >= 2:
        best_name = "Dimir Faeries"
    if best_name == "U Terror" and counts.get("Spellstutter Sprite", 0) >= 2:
        best_name = "Dimir Faeries"
    if best_name == "Burn" and counts.get("Grab the Prize", 0) + counts.get("Sneaky Snacker", 0) >= 3:
        best_name = "Mono-Red Madness"
    best_name = _resolve_mono_red_aggro(counts, best_name)
    best_name = _resolve_hawk_family(counts, best_name, scores)
    return Classification(archetype=best_name, score=best_score, runners_up=scores[1:4])


def classify_from_card_rows(card_rows: list[dict]) -> str:
    main: Counter[str] = Counter()
    for row in card_rows:
        if row.get("board") == "main":
            main[row["name"]] += int(row.get("qty") or 0)
    return classify_deck(main).archetype
