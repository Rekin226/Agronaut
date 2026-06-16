"""Fact collection seam between the UI/LLM and the deterministic model.

For M1 the design calculator takes FIXED, structured inputs, so the safest "fact collection"
is a validated form (no free-text parsing, no LLM guessing engineering numbers). This module
exposes the option lists the UI needs and a single typed entry point into the trust zone.

The free-text parsers in `srcs/chatbot.py` (temperature/pH/species extraction for the
conversational troubleshooting flow) are extracted here in M3, when the orchestrator is
refactored. They are not needed by the M1 calculator and are intentionally left untouched.
"""

from __future__ import annotations

from aqua_model.crops import CROPS
from aqua_model.species import SPECIES
from aqua_model.types import DesignInput
from aqua_model.validate import ValidationError, validate_design_input


def available_species() -> list[str]:
    return sorted(SPECIES)


def available_crops() -> list[str]:
    return sorted(CROPS)


def design_from_form(
    fish_species: str,
    crop: str,
    grow_area_m2: float,
    temperature_c: float,
    water_budget_lpd: float,
    source_water_note: str | None = None,
) -> DesignInput:
    """Validate structured form values into a DesignInput. Raises ValidationError on bad input.

    This is the ONLY way the UI puts numbers into the model — the validation gate, reused.
    """
    return validate_design_input(
        fish_species=fish_species,
        crop=crop,
        grow_area_m2=grow_area_m2,
        temperature_c=temperature_c,
        water_budget_lpd=water_budget_lpd,
        source_water_note=source_water_note,
    )


__all__ = [
    "available_species",
    "available_crops",
    "design_from_form",
    "ValidationError",
]
