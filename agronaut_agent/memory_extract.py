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


def extract_facts(text: str) -> dict[str, str]:
    """Pull system facts from free text. Returns only keys that were confidently found."""
    from srcs.chatbot import _parse_temperature_c, _parse_ph, _parse_fish_species

    facts: dict[str, str] = {}
    temp = _parse_temperature_c(text)
    if temp is not None:
        facts["temperature_c"] = str(temp)
    if _PH_CUE.search(text):
        ph = _parse_ph(text)
        if ph is not None:
            facts["ph"] = str(ph)
    species = _parse_fish_species(text)
    if species:
        facts["fish_species"] = species
    return facts
