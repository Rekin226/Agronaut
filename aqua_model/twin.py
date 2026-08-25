"""A time-stepped nitrogen twin: the sizing model, rolled forward.

`massbalance.nitrogen_check` answers "where does the nitrogen end up, eventually". It is a
steady-state answer and it is the right one for sizing a system. It cannot answer the questions an
operator actually asks mid-season:

    "I doubled the feed on Tuesday — when does ammonia peak, and how high?"
    "My biofilter is three weeks old. Is it big enough yet?"
    "If I stock 200 more fingerlings, does nitrite go somewhere dangerous first?"

Those are transient questions. Steady state is silent on all of them, because the dangerous part of
a nitrogen event is the path, not the destination — a system can arrive at a perfectly healthy
equilibrium having killed its fish on the way.

**The twin must agree with the model it extends.** Run with constant feed and a mature biofilter,
this converges to exactly the flows `nitrogen_check` reports: the same coefficients, the same sink
split, the same numbers. That is asserted in the tests, and it is the sharpest check available —
if the stepped and static models disagree at equilibrium, one of them is wrong, and the
disagreement says which quantity to look at.

What the twin adds is the path between now and then.

SCOPE. This is the nitrogen cascade and the water balance. Dissolved oxygen, pH and alkalinity are
deliberately absent: DO lives on a minutes timescale rather than hours and needs aeration and
photosynthesis modelled to say anything true, and pH needs the alkalinity coupling. Modelling them
badly here would be worse than not modelling them, because a number in a trajectory reads as a
prediction. `NOT_MODELLED` states this in the output, the same way sizing states its own limits.

Pure and deterministic: no I/O, no network, no clock. Same trust-zone rules as the rest of
`aqua_model/`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from . import coefficients as C
from .massbalance import (
    DENITRIFICATION_FRACTION,
    SOLIDS_REMOVAL_FRACTION,
    WATER_EXCHANGE_FRACTION,
)
from .species import FishSpecies, temperature_feed_factor

# What a trajectory from this model does NOT account for. Surfaced in every result, because a
# plotted line reads as a promise.
NOT_MODELLED = (
    "dissolved oxygen (minutes timescale; needs aeration + photosynthesis)",
    "pH and alkalinity (nitrification consumes ~7.14 g CaCO3 per g NH4-N oxidised)",
    "un-ionised ammonia fraction (NH3 vs NH4+ — the toxic split is pH and temperature dependent)",
    "solids mineralisation returning N to solution",
    "per-cohort fish size structure (biomass is aggregate)",
    "disease, mortality events, and feeding-response behaviour",
)

# Nitrifier growth. A biofilter is a population, not a filter: it doubles at a rate, which is why
# a new system takes weeks to cycle and why a crashed one does not recover overnight.
_AOB_DOUBLING_DAYS = 1.0     # ammonia oxidisers
_NOB_DOUBLING_DAYS = 1.5     # nitrite oxidisers — slower, which is why nitrite peaks AFTER ammonia
_SEED_CAPACITY_G_DAY = 0.05  # trace population present at the start of cycling

# Fraction of excreted N reaching the water as TAN. The rest leaves as settleable solids before it
# dissolves — the same split `nitrogen_check` assumes, so the two models agree at equilibrium.
_TO_WATER = 1.0 - SOLIDS_REMOVAL_FRACTION

# How dissolved N leaves once nitrified, as FIRST-ORDER RATE CONSTANTS (fraction of the nitrate
# pool removed per day) rather than shares of a step's arrivals.
#
# The distinction is not cosmetic. Draining fixed shares of the pool each step removes all of it
# every step, so nitrate can never accumulate — an undersized grow bed would show 0 mg/L forever,
# which is the opposite of what it does. With rate constants the pool settles at
# inflow / sum(k), which is the standing concentration an operator actually measures.
#
# The RATIO is fixed by the steady-state model (0.40 : 0.20 : 0.05 of excreted N), so the split
# still matches `nitrogen_check` exactly. Only the magnitude is new, and it sets how fast the
# system equilibrates and therefore where nitrate sits.
_N_REMOVAL_PER_DAY = 0.10        # ~10% of the nitrate pool leaves per day; equilibrium ~10x daily load

_SINK_RATIO_TOTAL = (C.PLANT_N_UPTAKE_FRACTION.value
                     + WATER_EXCHANGE_FRACTION
                     + DENITRIFICATION_FRACTION)
_K_PLANT = _N_REMOVAL_PER_DAY * C.PLANT_N_UPTAKE_FRACTION.value / _SINK_RATIO_TOTAL
_K_EXCHANGE = _N_REMOVAL_PER_DAY * WATER_EXCHANGE_FRACTION / _SINK_RATIO_TOTAL
_K_DENITRIFICATION = _N_REMOVAL_PER_DAY * DENITRIFICATION_FRACTION / _SINK_RATIO_TOTAL


@dataclass(frozen=True)
class TwinState:
    """Everything the nitrogen twin carries forward. Concentrations are mg/L as N."""

    tan_mg_l: float = 0.0
    no2_mg_l: float = 0.0
    no3_mg_l: float = 0.0
    volume_l: float = 1000.0
    fish_biomass_kg: float = 0.0
    aob_capacity_g_day: float = _SEED_CAPACITY_G_DAY
    nob_capacity_g_day: float = _SEED_CAPACITY_G_DAY
    day: float = 0.0

    def total_n_g(self) -> float:
        """Dissolved nitrogen currently held in the water, grams."""
        return (self.tan_mg_l + self.no2_mg_l + self.no3_mg_l) * self.volume_l / 1000.0


@dataclass(frozen=True)
class StepResult:
    """One step's state plus the flows that produced it — the flows are the explanation."""

    state: TwinState
    n_excreted_g: float = 0.0
    n_to_solids_g: float = 0.0
    n_to_water_g: float = 0.0
    nitrified_tan_g: float = 0.0
    nitrified_no2_g: float = 0.0
    n_plant_g: float = 0.0
    n_water_exchange_g: float = 0.0
    n_denitrified_g: float = 0.0
    warnings: tuple[str, ...] = field(default_factory=tuple)


def excreted_n_g(feed_g: float, species: FishSpecies) -> float:
    """Nitrogen excreted from one feeding, grams.

    Identical arithmetic to `massbalance.nitrogen_check` — fed N minus the N locked into new
    tissue. Shared deliberately: if this drifts from the steady-state model, the twin stops being
    the same model rolled forward and becomes a second, competing one.
    """
    n_frac = C.N_FRACTION_OF_PROTEIN.value
    n_fed = feed_g * (species.feed_protein_pct / 100.0) * n_frac
    growth_g = feed_g / species.fcr if species.fcr > 0 else 0.0
    n_retained = growth_g * (species.body_protein_pct / 100.0) * n_frac
    return max(0.0, n_fed - n_retained)


def _grow(capacity_g_day: float, load_g_day: float, doubling_days: float, dt_days: float) -> float:
    """Nitrifier capacity after `dt_days`, growing toward the load it is being asked to process.

    Logistic-ish: the population expands while there is substrate it cannot yet handle, and decays
    slowly when starved. Capped at twice the load so an idle filter does not grow without bound.
    """
    if dt_days <= 0:
        return capacity_g_day
    rate = 0.693 / doubling_days                      # ln(2)/t_double
    target = max(load_g_day, _SEED_CAPACITY_G_DAY)
    if capacity_g_day < target:
        grown = capacity_g_day + capacity_g_day * rate * dt_days
        return min(grown, target * 2.0, capacity_g_day + target * rate * dt_days * 2)
    decay = 0.10 * dt_days                            # starved biofilm sloughs slowly
    return max(_SEED_CAPACITY_G_DAY, capacity_g_day * (1.0 - decay))


def step(
    state: TwinState,
    species: FishSpecies,
    *,
    feed_g_per_day: float,
    temperature_c: float,
    dt_days: float = 1.0,
    plant_uptake_capacity_g_day: float | None = None,
) -> StepResult:
    """Advance the twin by `dt_days`.

    Order matters and mirrors the physical sequence: fish excrete, solids are captured, the
    remainder dissolves as TAN, nitrifiers convert what their current population can handle, and
    the resulting nitrate is drawn down by plants, water exchange and denitrification.

    Nitrification is CAPACITY-LIMITED, and that single fact produces most of the behaviour worth
    having. A biofilter that cannot keep up leaves TAN in the water, so ammonia climbs; the
    nitrite oxidisers double more slowly than the ammonia oxidisers, so nitrite peaks later and
    outlasts it. That lag is the classic new-system nitrite spike, and it falls out of the rates
    rather than being scripted.
    """
    if dt_days <= 0:
        raise ValueError("dt_days must be positive")
    if state.volume_l <= 0:
        raise ValueError("volume_l must be positive")

    warnings: list[str] = []
    vol = state.volume_l

    # Temperature gates feeding, and therefore the whole cascade.
    # feed is a RATE, so it scales with the step. Treating it as a per-step amount makes every
    # result depend on dt — a quarter-day step would deliver a full day's feed four times over,
    # and equilibrium nitrate would come out 4x too high.
    temp_factor = temperature_feed_factor(species, temperature_c)
    effective_feed = feed_g_per_day * dt_days * temp_factor

    excreted = excreted_n_g(effective_feed, species)
    to_solids = excreted * SOLIDS_REMOVAL_FRACTION
    to_water = excreted - to_solids

    tan_g = state.tan_mg_l * vol / 1000.0 + to_water
    no2_g = state.no2_mg_l * vol / 1000.0
    no3_g = state.no3_mg_l * vol / 1000.0

    # --- nitrification, limited by the population that exists right now ---
    tan_demand_g_day = tan_g / dt_days
    aob_cap = state.aob_capacity_g_day
    oxidised_tan = min(tan_g, aob_cap * dt_days)
    tan_g -= oxidised_tan
    no2_g += oxidised_tan

    no2_demand_g_day = no2_g / dt_days
    nob_cap = state.nob_capacity_g_day
    oxidised_no2 = min(no2_g, nob_cap * dt_days)
    no2_g -= oxidised_no2
    no3_g += oxidised_no2

    # --- nitrate drawdown: plants, water exchange, anoxic loss ---
    # First-order in the pool, so nitrate settles at inflow / sum(k) instead of being drained
    # to nothing. Plant uptake is additionally capped by what the standing crop can actually
    # take up — an undersized bed is the usual reason nitrate climbs.
    # All three draw on the SAME pool concurrently rather than in sequence: applying them one
    # after another lets the first sink shrink the pool the next one sees, which biases the split
    # toward whichever happens to be evaluated first (~3% toward plants, in practice).
    pool = no3_g
    plant_demand = pool * _K_PLANT * dt_days
    if plant_uptake_capacity_g_day is not None:
        capped = min(plant_demand, plant_uptake_capacity_g_day * dt_days)
        if capped < plant_demand - 1e-9:
            warnings.append(
                "plant uptake is capacity-limited — nitrate will accumulate until the crop, "
                "water exchange or bed area changes")
        plant_demand = capped
    exch_demand = pool * _K_EXCHANGE * dt_days
    denit_demand = pool * _K_DENITRIFICATION * dt_days

    total_demand = plant_demand + exch_demand + denit_demand
    if total_demand > pool > 0:                 # a long step cannot remove more than exists
        scale = pool / total_demand
        plant_demand *= scale
        exch_demand *= scale
        denit_demand *= scale
    n_plant, n_exchange, n_denit = plant_demand, exch_demand, denit_demand
    no3_g = max(0.0, pool - (n_plant + n_exchange + n_denit))

    # --- nitrifier populations respond to the load they just saw ---
    aob_next = _grow(aob_cap, tan_demand_g_day, _AOB_DOUBLING_DAYS, dt_days)
    nob_next = _grow(nob_cap, no2_demand_g_day, _NOB_DOUBLING_DAYS, dt_days)

    growth_kg = (effective_feed / species.fcr / 1000.0) if species.fcr > 0 else 0.0

    new = replace(
        state,
        tan_mg_l=tan_g * 1000.0 / vol,
        no2_mg_l=no2_g * 1000.0 / vol,
        no3_mg_l=no3_g * 1000.0 / vol,
        fish_biomass_kg=state.fish_biomass_kg + growth_kg,
        aob_capacity_g_day=aob_next,
        nob_capacity_g_day=nob_next,
        day=state.day + dt_days,
    )
    if temp_factor < 1.0:
        warnings.append(
            f"temperature {temperature_c}C is outside {species.name}'s optimum — "
            f"feed intake scaled to {temp_factor:.0%}")
    # Flows are reported as g/DAY, not g/step, so they are comparable across step sizes and
    # directly against `nitrogen_check`.
    per_day = 1.0 / dt_days
    return StepResult(
        state=new,
        n_excreted_g=excreted * per_day,
        n_to_solids_g=to_solids * per_day,
        n_to_water_g=to_water * per_day,
        nitrified_tan_g=oxidised_tan * per_day,
        nitrified_no2_g=oxidised_no2 * per_day,
        n_plant_g=n_plant * per_day,
        n_water_exchange_g=n_exchange * per_day,
        n_denitrified_g=n_denit * per_day,
        warnings=tuple(warnings),
    )


def simulate(
    state: TwinState,
    species: FishSpecies,
    *,
    days: int,
    feed_g_per_day: float,
    temperature_c: float,
    dt_days: float = 1.0,
    plant_uptake_capacity_g_day: float | None = None,
) -> list[StepResult]:
    """Roll the twin forward and return every step, so the PATH is inspectable — not just where
    it ended up. The whole point is the transient."""
    steps = max(1, int(round(days / dt_days)))
    out: list[StepResult] = []
    cur = state
    for _ in range(steps):
        r = step(cur, species, feed_g_per_day=feed_g_per_day, temperature_c=temperature_c,
                 dt_days=dt_days, plant_uptake_capacity_g_day=plant_uptake_capacity_g_day)
        out.append(r)
        cur = r.state
    return out


def mature_biofilter(species: FishSpecies, feed_g_per_day: float) -> tuple[float, float]:
    """AOB and NOB capacities for a system already cycled at this feed rate.

    Starting a simulation here skips the cycling transient — the right starting point for "what
    happens if I change something on an established system", as opposed to "what happens when I
    start a new one".
    """
    excreted = excreted_n_g(feed_g_per_day, species)
    tan_load = excreted * _TO_WATER
    return tan_load, tan_load
