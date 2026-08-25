"""Crop productivity in time: the cited yield, modulated by the climate you actually have.

`crops.py` states what each crop yields per m² per year, with sources. Those citations were
measured somewhere with adequate light, suitable temperature and full nutrition. This module
asks the operator's next question: what fraction of that do I get HERE, in January, with my
nitrate where it is?

The model is deliberately a MODULATION, not a mechanistic canopy simulation:

    growth = cited_yield_rate x f_light(DLI) x f_temp(T) x f_nitrogen(NO3)

with each factor in [0, ~1]. Anchoring on the cited yield keeps every output traceable to a
source in `crops.py` and stops the model inventing yields no trial has produced (a hard cap
holds even when all factors are favourable). The cost is honesty about what this is: a
seasonal productivity estimate for planning, not a per-plant growth curve. Under steady
staggered cropping — how aquaponic beds are actually run — the standing crop is a mix of
ages and the daily harvestable increment is what matters, which is exactly what this states.

Light response uses the Daily Light Integral saturation values horticulture extension
services publish (leafy greens saturate near 14-17 mol/m²/day; fruiting crops keep
responding into the low 20s). Temperature response is a flat-top ramp inside the crop's own
cited band. Nitrogen response is Monod-shaped in nitrate-N, which couples this module to the
nitrogen twin in both directions: low nitrate throttles growth, and realized growth sets the
plant uptake capacity the twin sees.

Pure and deterministic; trust-zone rules hold.
"""

from __future__ import annotations

from dataclasses import dataclass

from .coefficients import Coefficient
from .crops import Crop

NOT_MODELLED = (
    "per-plant size structure and cycle timing (staggered steady-state only)",
    "germination/transplant losses and crop failure",
    "pests, disease, tipburn and bolting (heat shows up only as slower growth)",
    "CO2, humidity/VPD and airflow effects",
    "quality grades — a kilogram is a kilogram here; markets disagree",
    "other nutrients than nitrogen (K, Ca, Fe deficiencies are real in aquaponics)",
)

# Light saturation (DLI, mol PAR/m²/day). Below the saturation point growth scales nearly
# linearly with light; above it, extra light buys little. Values per extension guidance for
# greenhouse crops (e.g. Purdue/Michigan State DLI guides: leafy 12-17, fruiting 20-30).
DLI_SATURATION = {
    "leafy": Coefficient(
        name="dli_saturation.leafy", value=15.0, low=12.0, high=17.0, unit="mol/m²/day",
        source="LIT: extension DLI targets for lettuce/leafy greens (12-17 mol saturates)"),
    "fruiting": Coefficient(
        name="dli_saturation.fruiting", value=22.0, low=18.0, high=30.0, unit="mol/m²/day",
        source="LIT: extension DLI targets for tomato/cucumber/pepper (20-30 mol)"),
}

# Monod half-saturation for growth on nitrate-N. Hydroponic lettuce holds near-max growth
# down to low-double-digit NO3-N; response falls off quickly only below a few mg/L.
K_NITRATE = Coefficient(
    name="k_nitrate_monod", value=3.0, low=1.0, high=8.0, unit="mg/L NO3-N",
    source="LIT: hydroponic/aquaponic lettuce trials show little yield loss at 10-40 mg/L "
           "NO3-N and sharp decline only under ~5; half-saturation placed accordingly")

# Above-full-citation cap: better-than-reference conditions may beat the cited yield a
# little (the citations are typical practice, not records), but not by much.
_MAX_OVER_CITED = 1.15


@dataclass(frozen=True)
class CropFactors:
    """The three modulations for one day, kept separate so a report can say WHY yield
    fell (light, temperature, or nitrogen) instead of just that it did."""

    f_light: float
    f_temp: float
    f_nitrogen: float

    def combined(self) -> float:
        return min(_MAX_OVER_CITED, self.f_light * self.f_temp * self.f_nitrogen)


def f_light(dli_mol_m2: float, crop: Crop) -> float:
    sat = DLI_SATURATION.get(crop.category, DLI_SATURATION["leafy"]).value
    if dli_mol_m2 <= 0:
        return 0.0
    return min(1.0, dli_mol_m2 / sat)


def f_temp(temperature_c: float, crop: Crop) -> float:
    """Flat-top ramp inside the crop's cited [temp_min_c, temp_max_c] band: full speed in
    the middle half, linear falloff to zero at the cited limits."""
    lo, hi = crop.temp_min_c, crop.temp_max_c
    if not (lo < hi):
        return 1.0
    if temperature_c <= lo or temperature_c >= hi:
        return 0.0
    span = hi - lo
    ramp = span / 4.0                       # the outer quarters are the falloff zones
    if temperature_c < lo + ramp:
        return (temperature_c - lo) / ramp
    if temperature_c > hi - ramp:
        return (hi - temperature_c) / ramp
    return 1.0


def f_nitrogen(no3_mg_l: float) -> float:
    if no3_mg_l <= 0:
        return 0.0
    return no3_mg_l / (no3_mg_l + K_NITRATE.value)


def factors(crop: Crop, *, dli_mol_m2: float, temperature_c: float,
            no3_mg_l: float) -> CropFactors:
    return CropFactors(
        f_light=f_light(dli_mol_m2, crop),
        f_temp=f_temp(temperature_c, crop),
        f_nitrogen=f_nitrogen(no3_mg_l),
    )


def harvest_rate_kg_m2_day(crop: Crop, fac: CropFactors) -> float:
    """Fresh harvestable growth per m² of planted area per day, under these conditions."""
    cited_daily = crop.yield_kg_per_m2_year / 365.0
    return cited_daily * fac.combined()


def n_uptake_g_day(crop: Crop, fac: CropFactors, area_m2: float) -> float:
    """Nitrogen the crop pulls from the water while growing at this rate — the coupling
    the nitrogen twin consumes as its plant uptake capacity. Scaled from the crop's cited
    full-speed uptake by the same factors that scale growth: a stalled crop stops eating."""
    return crop.n_uptake_g_per_m2_day * fac.combined() * area_m2
