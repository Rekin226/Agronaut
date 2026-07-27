"""The trust boundary.

Nothing enters the model except a DesignInput built HERE, after range/unit/type checks.
The LLM (or any caller) may PROPOSE raw values, but `validate_design_input` is the only
door into `aqua_model`. A bad value is rejected loudly — never silently defaulted, never
silently clamped — so a hallucinated input cannot produce a confidently-wrong design.
"""

from __future__ import annotations

from .crops import CROPS
from .species import SPECIES
from .system_types import SYSTEM_TYPES
from .types import DesignInput, HydroponicInput


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
    system_type="raft",
    crop_plan=None,
) -> DesignInput:
    errors: list[str] = []

    species_key = str(fish_species or "").strip().lower()
    if species_key not in SPECIES:
        errors.append(f"unknown fish_species {fish_species!r}; known: {sorted(SPECIES)}")

    system_type_key = _validate_system_type(system_type, errors)

    # Mixed beds: a validated crop plan supersedes the single crop/area — the dominant crop and
    # the summed area are DERIVED from it, so the single-crop fields stay meaningful downstream.
    plan = _validate_crop_plan(crop_plan, errors)
    if plan is not None:
        crop_key = max(plan, key=lambda p: p[1])[0] if plan else ""
        grow_area_m2 = sum(a for _, a in plan)
    else:
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
        system_type=system_type_key,
        crop_plan=tuple(plan) if plan is not None else (),
    )


def _validate_system_type(system_type, errors) -> str:
    key = str(system_type or "raft").strip().lower()
    if key not in SYSTEM_TYPES:
        errors.append(f"unknown system_type {system_type!r}; known: {sorted(SYSTEM_TYPES)}")
    return key


def _validate_crop_plan(crop_plan, errors) -> list | None:
    """Normalize a mixed-bed plan to [(crop_key, area_m2), ...] or return None for single-crop.

    Accepts a list of {"crop", "area_m2"} dicts or (crop, area) pairs. Every crop must be known
    and every area strictly positive — a bad plan is rejected loudly, never partially applied.
    """
    if crop_plan is None:
        return None
    if not isinstance(crop_plan, (list, tuple)) or len(crop_plan) == 0:
        errors.append("crop_plan must be a non-empty list of {crop, area_m2} entries")
        return []
    normalized: list = []
    for entry in crop_plan:
        if isinstance(entry, dict):
            ck = str(entry.get("crop") or "").strip().lower()
            area = entry.get("area_m2")
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            ck, area = str(entry[0] or "").strip().lower(), entry[1]
        else:
            errors.append(f"crop_plan entry not understood: {entry!r}")
            continue
        if ck not in CROPS:
            errors.append(f"unknown crop {ck!r} in crop_plan; known: {sorted(CROPS)}")
        a = _as_float(area, f"crop_plan area for {ck!r}", errors)
        if a is not None and a <= 0:
            errors.append(f"crop_plan area for {ck!r} must be > 0, got {a}")
        normalized.append((ck, float(a) if a is not None else 0.0))
    return normalized


def validate_hydroponic_input(
    crop,
    grow_area_m2,
    temperature_c,
    water_budget_lpd,
    source_water_note=None,
    system_type="raft",
) -> HydroponicInput:
    """Trust gate for hydroponic sizing — same discipline as validate_design_input, but no
    fish species (nutrients are dosed, not produced by fish)."""
    errors: list[str] = []

    crop_key = str(crop or "").strip().lower()
    if crop_key not in CROPS:
        errors.append(f"unknown crop {crop!r}; known: {sorted(CROPS)}")

    system_type_key = _validate_system_type(system_type, errors)

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

    return HydroponicInput(
        crop=crop_key,
        grow_area_m2=float(grow_area_m2),
        temperature_c=float(temperature_c),
        water_budget_lpd=float(water_budget_lpd),
        source_water_note=source_water_note,
        system_type=system_type_key,
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
