"""Deterministic extraction of durable user facts from a message.

Reuses the battle-tested regex parsers in srcs/chatbot.py (temperature, pH, fish species)
rather than asking the LLM to guess them — facts about the user's real system should be
captured deterministically. Imported lazily so this module stays light for unit tests.
"""

from __future__ import annotations

import re

# Only trust a pH reading when the text actually mentions pH. The shared _parse_ph has a
# bare-number fallback (any value 4-10) that fabricates a pH from things like "10 m2" —
# safe in the old form-driven chatbot, but a honesty hazard for free-form agent messages.
_PH_CUE = re.compile(r"\bp\s*H\b", re.IGNORECASE)

# DO needs a unit (mg/L or ppm) so the English word "do" can't fabricate a reading.
_DO_RE = re.compile(
    r"(?:dissolved\s+oxygen|\bDO\b)\D{0,8}(\d+(?:\.\d+)?)\s*(?:mg/?\s*l|ppm)",
    re.IGNORECASE,
)
# Require the number to be very close to "ammonia" (≤4 non-digit chars) OR carry a unit.
# A wide unit-less window fabricated readings from prose like "ammonia issues for 3 days".
_AMMONIA_RE = re.compile(
    r"ammonia(?:\D{0,4}(\d+(?:\.\d+)?)\b|\D{0,15}(\d+(?:\.\d+)?)\s*(?:mg/?\s*l|ppm))",
    re.IGNORECASE,
)

# Temperature only when the text actually signals temperature. The shared parser has a
# bare-number fallback (any value 10-40) that otherwise turns "30 fish" or "20 m2" into a
# water temperature — the same honesty hazard the pH cue guards against.
_TEMP_CUE = re.compile(r"\d\s*°|\d\s*c\b|°|celsius|degree|\btemp", re.IGNORECASE)

# Only accept a parsed fish species when the text names a real aquaculture fish. The shared
# parser title-cases arbitrary input ("hey there" -> "Hey There"), so gate on a vocabulary.
_FISH_WORDS = frozenset({
    "tilapia", "clarias", "catfish", "trout", "carp", "koi", "goldfish", "perch",
    "bass", "barramundi", "cod", "salmon", "shrimp", "prawn", "pacu", "arapaima",
})


def extract_facts(text: str) -> dict[str, str]:
    """Pull system facts from free text. Returns only keys that were confidently found."""
    from srcs.chatbot import _parse_temperature_c, _parse_ph, _parse_fish_species

    facts: dict[str, str] = {}
    if _TEMP_CUE.search(text):
        temp = _parse_temperature_c(text)
        if temp is not None:
            facts["temperature_c"] = str(temp)
    if _PH_CUE.search(text):
        ph = _parse_ph(text)
        if ph is not None:
            facts["ph"] = str(ph)
    species = _parse_fish_species(text)
    if species and any(w in _FISH_WORDS for w in species.lower().split()):
        facts["fish_species"] = species
    do = _DO_RE.search(text)
    if do:
        facts["dissolved_oxygen_mgl"] = do.group(1)
    amm = _AMMONIA_RE.search(text)
    if amm:
        facts["ammonia_mgl"] = amm.group(1) or amm.group(2)
    return facts
