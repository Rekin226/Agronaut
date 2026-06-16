"""The trust boundary.

Nothing enters the model except a DesignInput built HERE, after range/unit/type checks.
The LLM (or any caller) may PROPOSE raw values, but `validate_design_input` is the only
door into `aqua_model`. A bad value is rejected loudly — never silently defaulted, never
silently clamped — so a hallucinated input cannot produce a confidently-wrong design.
"""

from __future__ import annotations

from .crops import CROPS
from .species import SPECIES
from .types import DesignInput


class ValidationError(ValueError):
    """Raised when a proposed input cannot safely enter the model. Carries all problems."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


# Hard sanity bounds. Outside these, refuse rather than compute nonsense.
_BOUNDS = {
    "grow_area_m2": (0.1, 100_000.0),
    "temperature_c": (0.0, 45.0),
    "water_budget_lpd": (0.0, 10_000_000.0),
}


def validate_design_input(
    fish_species,
    crop,
    grow_area_m2,
    temperature_c,
    water_budget_lpd,
    source_water_note=None,
) -> DesignInput:
    errors: list[str] = []

    species_key = str(fish_species or "").strip().lower()
    if species_key not in SPECIES:
        errors.append(f"unknown fish_species {fish_species!r}; known: {sorted(SPECIES)}")

    crop_key = str(crop or "").strip().lower()
    if crop_key not in CROPS:
        errors.append(f"unknown crop {crop!r}; known: {sorted(CROPS)}")

    grow_area_m2 = _as_float(grow_area_m2, "grow_area_m2", errors)
    temperature_c = _as_float(temperature_c, "temperature_c", errors)
    water_budget_lpd = _as_float(water_budget_lpd, "water_budget_lpd", errors)

    for field, val in (
        ("grow_area_m2", grow_area_m2),
        ("temperature_c", temperature_c),
        ("water_budget_lpd", water_budget_lpd),
    ):
        if val is None:
            continue
        lo, hi = _BOUNDS[field]
        if not (lo <= val <= hi):
            errors.append(f"{field}={val} out of range [{lo}, {hi}]")

    if source_water_note is not None and not isinstance(source_water_note, str):
        errors.append("source_water_note must be a string or None")

    if errors:
        raise ValidationError(errors)

    return DesignInput(
        fish_species=species_key,
        crop=crop_key,
        grow_area_m2=float(grow_area_m2),
        temperature_c=float(temperature_c),
        water_budget_lpd=float(water_budget_lpd),
        source_water_note=source_water_note,
    )


def _as_float(value, field, errors):
    if isinstance(value, bool):  # bool is an int subclass; reject explicitly
        errors.append(f"{field} must be a number, got bool")
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        errors.append(f"{field} must be a number, got {value!r}")
        return None
