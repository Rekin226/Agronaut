"""Cited coefficient layer — the trust artifact.

Every magic number the model uses lives here as a `Coefficient` with a value, a
plausible range, a unit, and a SOURCE. Functions read from this registry; they never
hard-code numbers. When a design runs, it echoes exactly which coefficients (and which
sources) it used, so an institutional reviewer can audit the math without trusting any LLM.

IMPORTANT — these are SEED DEFAULTS, not universal truths. Aquaponics coefficients vary by
species, cultivar, climate, feed, and system type. The whole point of the calibration step
(validate against a real running system) is to replace these defaults with measured values.
Ranges are deliberately wide to reflect that uncertainty; a conservative SAFETY_FACTOR is
applied where undersizing would be dangerous (biofilter, aeration).

Primary sources:
  FAO589 = Somerville, Cohen, Pantanella, Stankus & Lovatelli (2014),
           "Small-scale aquaponic food production", FAO Fisheries and Aquaculture
           Technical Paper 589. (Free PDF.)
  UVI    = Rakocy et al., University of the Virgin Islands raft aquaponics work
           (feeding-rate ratio).
  LIT    = General aquaculture/hydroponics literature consensus (ranges, not a single paper).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Coefficient:
    """A single, sourced, ranged constant used by the model."""

    name: str
    value: float          # the default used in calculations
    low: float            # plausible lower bound
    high: float           # plausible upper bound
    unit: str
    source: str           # e.g. "FAO589", "UVI", "LIT"
    note: str = ""

    def __post_init__(self) -> None:
        if not (self.low <= self.value <= self.high):
            raise ValueError(
                f"Coefficient {self.name!r}: value {self.value} not within "
                f"[{self.low}, {self.high}]"
            )


# A conservative multiplier applied to sizing outputs where UNDER-sizing is dangerous
# (biofilter media, aeration headroom). >1 means "build a bit bigger to be safe".
SAFETY_FACTOR = Coefficient(
    name="safety_factor",
    value=1.3, low=1.1, high=1.5, unit="dimensionless", source="LIT",
    note="Headroom for losses, peaks, clogging, and coefficient uncertainty.",
)

# Nitrogen chemistry — well established.
N_FRACTION_OF_PROTEIN = Coefficient(
    name="n_fraction_of_protein",
    value=0.16, low=0.16, high=0.16, unit="g N / g protein", source="LIT",
    note="Protein is ~16% nitrogen by mass (Kjeldahl factor 6.25). Effectively exact.",
)

# Plant uptake fraction: of the nitrogen a fish EXCRETES, what share do plants actually
# take up (the rest leaves via solids removal, water exchange, denitrification)? Sizing
# beds to absorb 100% of excreted N oversizes them — this fraction is the guard.
PLANT_N_UPTAKE_FRACTION = Coefficient(
    name="plant_n_uptake_fraction",
    value=0.40, low=0.30, high=0.50, unit="dimensionless", source="LIT",
    note="Plants recover only ~30-50% of excreted N; the rest exits via non-plant sinks.",
)

# Water-use rates (the binding objective for the founder's market).
EVAPOTRANSPIRATION_RATE = Coefficient(
    name="evapotranspiration_rate",
    value=4.0, low=2.0, high=8.0, unit="L / m2 plant / day", source="LIT",
    note="Crop ET; climate- and stage-dependent. Wide range; calibrate per site.",
)
TANK_EVAPORATION_RATE = Coefficient(
    name="tank_evaporation_rate",
    value=3.0, low=1.0, high=7.0, unit="L / m2 water-surface / day", source="LIT",
    note="Open-water evaporation; depends on cover, humidity, temperature.",
)

# System geometry assumptions (raft / DWC, the v1 system type).
RAFT_WATER_DEPTH = Coefficient(
    name="raft_water_depth",
    value=0.30, low=0.20, high=0.40, unit="m", source="FAO589",
    note="Typical raft/DWC canal water depth.",
)
SUMP_FRACTION = Coefficient(
    name="sump_fraction",
    value=0.10, low=0.05, high=0.20, unit="fraction of system volume", source="LIT",
)
PUMP_TURNOVER_RATE = Coefficient(
    name="pump_turnover_rate",
    value=1.0, low=0.5, high=2.0, unit="system volumes / hour", source="FAO589",
)

# Biofilter: nitrification rate per m2 of media surface. HIGHLY media- and
# temperature-dependent; deliberately conservative (low) so we don't undersize.
NITRIFICATION_RATE = Coefficient(
    name="nitrification_rate",
    value=0.40, low=0.20, high=0.80, unit="g TAN / m2 media / day", source="LIT",
    note="Conservative. Tank/raft surfaces also nitrify but are NOT counted here (eng decision).",
)


def registry() -> dict[str, Coefficient]:
    """All global coefficients by name (species/crop coefficients live in their own modules)."""
    return {
        c.name: c
        for c in (
            SAFETY_FACTOR,
            N_FRACTION_OF_PROTEIN,
            PLANT_N_UPTAKE_FRACTION,
            EVAPOTRANSPIRATION_RATE,
            TANK_EVAPORATION_RATE,
            RAFT_WATER_DEPTH,
            SUMP_FRACTION,
            PUMP_TURNOVER_RATE,
            NITRIFICATION_RATE,
        )
    }
