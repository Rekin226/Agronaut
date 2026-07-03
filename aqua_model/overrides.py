"""Per-operator coefficient overrides.

A calibrated value replaces a seed for ONE sizing/optimize call only — applied by rebuilding
the frozen FishSpecies/Crop with dataclasses.replace, so seeds are NEVER mutated and the
no-override path is unchanged. The override key is the calibration key (e.g. 'tilapia.fcr'):
its prefix names the species/crop it applies to, and its value must sit within the coefficient's
published empirical range (calibration.get) or it is refused at the trust gate.
"""

from __future__ import annotations

import dataclasses

from . import calibration
from .validate import ValidationError

# calibration-key suffix -> (which object, which model attribute)
_SUFFIX_TO_ATTR: dict[str, tuple[str, str]] = {
    "fcr": ("species", "fcr"),
    "harvest_weight": ("species", "harvest_weight_kg"),
    "yield": ("crop", "yield_kg_per_m2_year"),
}


def validate_overrides(overrides: dict) -> None:
    """Raise ValidationError if any override key is unknown, lacks an empirical range, or its
    value is non-numeric or outside that range."""
    errors: list[str] = []
    for key, val in (overrides or {}).items():
        suffix = key.rpartition(".")[2]
        if suffix not in _SUFFIX_TO_ATTR:
            errors.append(f"unknown calibration coefficient {key!r}")
            continue
        try:
            cal = calibration.get(key)
        except KeyError:
            errors.append(f"no empirical range for {key!r}; cannot bound it")
            continue
        try:
            v = float(val)
        except (TypeError, ValueError):
            errors.append(f"{key}: value {val!r} is not a number")
            continue
        if not (cal.emp_low <= v <= cal.emp_high):
            errors.append(f"{key}: {v} outside empirical range [{cal.emp_low}, {cal.emp_high}]")
    if errors:
        raise ValidationError(errors)


def apply_overrides(species=None, crop=None, overrides: dict | None = None):
    """Return (species, crop) with matching overrides applied via dataclasses.replace. Only an
    override whose key prefix equals the provided species/crop `.name` takes effect. Seeds are
    untouched (replace returns a new object). Assumes `overrides` has already passed
    `validate_overrides` (values are numeric and in range)."""
    if not overrides:
        return species, crop
    sp_repl: dict[str, float] = {}
    cr_repl: dict[str, float] = {}
    for key, val in overrides.items():
        prefix, _, suffix = key.rpartition(".")
        target_attr = _SUFFIX_TO_ATTR.get(suffix)
        if target_attr is None:
            continue
        target, attr = target_attr
        if target == "species" and species is not None and prefix == species.name:
            sp_repl[attr] = float(val)
        elif target == "crop" and crop is not None and prefix == crop.name:
            cr_repl[attr] = float(val)
    if sp_repl:
        species = dataclasses.replace(species, **sp_repl)
    if cr_repl:
        crop = dataclasses.replace(crop, **cr_repl)
    return species, crop
