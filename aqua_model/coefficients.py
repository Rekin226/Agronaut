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

# How far above saturation a dissolved-oxygen reading may sit before it is a broken probe rather
# than a bloom. Photosynthesis genuinely supersaturates a densely planted system during the day —
# 110-130% of saturation is routinely reported, and short excursions higher do occur. Sustained
# multiples are not physical: public aquaponics data has been observed at 4.3x saturation while
# flagged "reliable", which is a dead probe, not a planted tank.
DO_SUPERSATURATION_TOLERANCE = Coefficient(
    name="do_supersaturation_tolerance",
    value=1.5, low=1.3, high=2.0, unit="dimensionless", source="LIT",
    note="Multiple of Benson-Krause saturation above which a DO reading is treated as "
         "instrument failure. Generous by design: rejecting a real afternoon oxygen peak is "
         "worse than admitting a mildly wrong one, because the peak is real information.",
)


# Greenhouse envelope defaults — the single-poly-tunnel numbers every season
# forecast actually runs on (production.py builds ProductionParams from
# C.GreenhouseParams() with no per-site override). Provenance for each value
# lives in its note here and in the GreenhouseParams docstring; dossier §6
# carries the full glazing table. The water-tau band is derived, not measured —
# the note says so, and a measured settling time for a 1-10 m3 tank under
# cover would replace the whole entry.
GREENHOUSE_TRANSMISSIVITY = Coefficient(
    name="greenhouse_transmissivity",
    value=0.70, low=0.45, high=0.85, unit="fraction of outside PAR",
    source="LIT (Roberts 1998 Rutgers CCEA; U. Arkansas glazing table)",
    note="PLACED. New single polyethylene film transmits ~0.85-0.90 (Roberts 1998, "
         "lab total transmittance; Arkansas table: single PE 85%). Measured whole-house "
         "winter PAR under real structures runs 0.45-0.67 (Roberts 1998 Table 1, "
         "institutional houses, condensation and overhead equipment included). 0.70 = "
         "new-film house with ordinary structure, dirt and condensation losses "
         "(0.85-0.90 x 0.8-0.9 = 0.68-0.81); a dirty or screened house falls toward "
         "the measured band's bottom.",
)
GREENHOUSE_UNHEATED_LIFT_C = Coefficient(
    name="greenhouse_unheated_lift_c",
    value=3.0, low=1.7, high=4.7, unit="C on the daily mean",
    source="LIT (McCarter & Ingwell 36-tunnel study; Penn State Extension)",
    note="PLACED mid-band; the band is measured. On-farm daily means across 36 US high "
         "tunnels averaged +1.7 C over outside (+3 F, McCarter & Ingwell, via Vegetable "
         "Growers News); Penn State Extension reports a yearly-average tunnel +8.4 F "
         "(+4.7 C) over outdoors (Sanchez 2023). Midday gains run much higher on sun, "
         "near zero at night; ventilation eats most of the midday gain.",
)
GREENHOUSE_WATER_TAU_DAYS = Coefficient(
    name="greenhouse_water_tau_days",
    value=2.0, low=0.6, high=3.8, unit="days",
    source="PLACED (lumped-capacitance derivation; no measured source yet)",
    note="PLACED. tau = rho*V*cp / (h*A): 1-10 m3 tanks under cover give 0.6-3.8 days "
         "at h = 5-15 W/m2K (free convection + radiation). No measured settling time "
         "for a 1-10 m3 water mass under cover was found; a measured value replaces "
         "this entry. Sun-exposed shallow beds settle faster than the band.",
)

# Nitrogen chemistry — well established.
N_FRACTION_OF_PROTEIN = Coefficient(
    name="n_fraction_of_protein",
    value=0.16, low=0.16, high=0.16, unit="g N / g protein", source="LIT",
    note="Protein is ~16% nitrogen by mass (Kjeldahl factor 6.25). Effectively exact.",
)

# The feed the feeding-rate ratio was measured with. A feeding-rate ratio is a nitrogen-supply
# rule in grams of feed: UVI derived it on tilapia eating 32%-protein feed, and FAO 589 states
# its figures assume the same. Applied unchanged to barramundi on 45%-protein feed, the same
# grams per m2 carried ~40% more nitrogen than the plants can take up, and the nitrogen check
# flagged the design 62% off (2026-10-04). Sizing now scales the ratio by
# reference / feed protein, which holds nitrogen per m2 at what the ratio was measured with.
FRR_REFERENCE_FEED_PROTEIN_PCT = Coefficient(
    name="frr_reference_feed_protein_pct",
    value=32.0, low=32.0, high=32.0, unit="% protein", source="FAO589/UVI",
    note="Feeding-rate ratios assume a standard 32%-protein feed (FAO 589; Rakocy & "
         "Hargreaves 1993, via Goddek et al. 2019). Scaling the ratio by 32 / feed protein % "
         "is a derived step that holds nitrogen constant, not a calibrated one; phosphorus "
         "and potassium do not scale with protein.",
)


def frr_protein_factor(feed_protein_pct: float) -> float:
    """Multiplier on a crop's feeding-rate ratio for feed of this protein content: 1.0 for
    the 32% feed the ratio was measured with, below 1 for richer feed, above 1 for leaner."""
    if feed_protein_pct <= 0:
        raise ValueError(f"feed protein must be positive, got {feed_protein_pct}")
    return FRR_REFERENCE_FEED_PROTEIN_PCT.value / feed_protein_pct


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

# Pump HEAD: the static lift (per method, see system_types) is raised by this fraction to
# cover pipe/fitting friction (dynamic head). A rough but standard allowance for the short,
# low-velocity plumbing of a small recirculating system.
FRICTION_HEAD_FRACTION = Coefficient(
    name="friction_head_fraction",
    value=0.30, low=0.20, high=0.50, unit="fraction of static lift", source="LIT",
    note="Dynamic (friction) head as a fraction of static lift; size real pipework per layout.",
)

# Wire-to-water efficiency of a small submersible/pond pump — electrical power in vs hydraulic
# power out. Low, as these pumps are inefficient; used only to estimate running power/energy.
PUMP_EFFICIENCY = Coefficient(
    name="pump_efficiency",
    value=0.35, low=0.20, high=0.50, unit="dimensionless (wire-to-water)", source="LIT",
    note="Small submersible pumps are inefficient; a power estimate, not a spec — verify the curve.",
)

# Biofilter: nitrification rate per m2 of media surface. HIGHLY media- and
# temperature-dependent; deliberately conservative (low) so we don't undersize.
NITRIFICATION_RATE = Coefficient(
    name="nitrification_rate",
    value=0.40, low=0.20, high=0.80, unit="g TAN / m2 media / day", source="LIT",
    note="Conservative. Tank/raft surfaces also nitrify but are NOT counted here (eng decision).",
)

# --- Hydroponics: nutrient-solution targets (no fish; salts dosed directly). ------------
# Target electrical conductivity (EC) of the nutrient solution, a standard proxy for total
# dissolved nutrient strength. Leafy crops run leaner than fruiting crops.
EC_TARGET_LEAFY = Coefficient(
    name="ec_target_leafy",
    value=1.5, low=1.2, high=1.8, unit="mS/cm", source="LIT",
    note="DWC/NFT leafy-green nutrient strength; climate- and stage-dependent.",
)
EC_TARGET_FRUITING = Coefficient(
    name="ec_target_fruiting",
    value=2.6, low=2.0, high=3.5, unit="mS/cm", source="LIT",
    note="Fruiting-crop nutrient strength (tomato/pepper/cucumber); raise as fruit sets.",
)


def registry() -> dict[str, Coefficient]:
    """All global coefficients by name (species/crop coefficients live in their own modules)."""
    return {
        c.name: c
        for c in (
            SAFETY_FACTOR,
            N_FRACTION_OF_PROTEIN,
            PLANT_N_UPTAKE_FRACTION,
            FRR_REFERENCE_FEED_PROTEIN_PCT,
            EVAPOTRANSPIRATION_RATE,
            TANK_EVAPORATION_RATE,
            RAFT_WATER_DEPTH,
            SUMP_FRACTION,
            PUMP_TURNOVER_RATE,
            FRICTION_HEAD_FRACTION,
            PUMP_EFFICIENCY,
            NITRIFICATION_RATE,
            EC_TARGET_LEAFY,
            EC_TARGET_FRUITING,
            GREENHOUSE_TRANSMISSIVITY,
            GREENHOUSE_UNHEATED_LIFT_C,
            GREENHOUSE_WATER_TAU_DAYS,
        )
    }
