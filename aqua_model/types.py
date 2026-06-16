"""Typed inputs and outputs for the calculator.

The DesignInput is the ONLY thing the trust zone accepts. It is built by the
validation gate (`validate.py`), never populated directly from raw LLM output.

The DesignOutput is a build artifact, not just numbers: it carries the bill of
materials, operating envelope, maintenance checklist, the nitrogen consistency
check, the coefficients used (with sources), and an explicit list of what is NOT
modeled. That honesty layer is the credibility collateral (CEO review).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DesignInput:
    """Fixed inputs for ONE system. No palette, no objective — this is a calculator,
    not an optimizer (the species/crop search and goal belong to the M2 optimizer)."""

    fish_species: str          # must exist in species.SPECIES
    crop: str                  # must exist in crops.CROPS
    grow_area_m2: float        # the anchor; effective raft/DWC planted area
    temperature_c: float       # mean water temperature
    water_budget_lpd: float    # makeup water available per day (sanity-checked)
    source_water_note: str | None = None  # salinity/quality caveat


@dataclass(frozen=True)
class CoefficientUse:
    """Provenance record: one coefficient as it was used in a specific design run."""

    name: str
    value: float
    low: float
    high: float
    unit: str
    source: str


@dataclass
class DesignOutput:
    feasible: bool
    binding_constraint: str | None = None   # set when infeasible -> nearest-feasible hint

    # --- sizing numbers ---
    system_volume_l: float = 0.0
    rearing_tank_volume_l: float = 0.0
    fish_count: int = 0
    fish_biomass_kg: float = 0.0
    feed_g_per_day: float = 0.0
    grow_area_m2: float = 0.0
    pump_turnover_lph: float = 0.0
    biofilter_media_m2: float | None = None
    makeup_water_lpd: float = 0.0

    # --- build artifacts (Shape B) ---
    bill_of_materials: list[dict] = field(default_factory=list)
    operating_envelope: dict = field(default_factory=dict)
    maintenance_checklist: list[str] = field(default_factory=list)

    # --- honesty layer ---
    nitrogen_check: dict = field(default_factory=dict)
    coefficients_used: list[CoefficientUse] = field(default_factory=list)
    not_modeled: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
