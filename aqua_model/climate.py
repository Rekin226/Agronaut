"""Site climate to growing conditions: the outside world, reduced to what the twin needs.

A design is sized once, but it runs through seasons. This module turns a daily weather series
(fetched by `scripts/fetch_climate.py`, NEVER by this module — trust-zone rules) into the two
signals the production twin is forced with:

    water temperature   — what the fish feel; drives feeding, growth, and the nitrogen cascade
    light inside (PAR)  — what the plants feel; drives crop growth

The greenhouse model is deliberately coarse: a daily mean lift over outside air plus a
first-order water-thermal lag. That is honest for the questions asked of it ("will tilapia
grow here in January", "how much does a heater change the harvest"), and no finer than the
daily weather driving it. An hourly energy balance would add precision the inputs do not have.

Parameters carry sources like `coefficients.py`. What is NOT modelled is declared, because a
temperature trajectory reads as a promise.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp

NOT_MODELLED = (
    "hourly dynamics (day/night swings, dawn cold dips — daily means only)",
    "humidity and VPD (transpiration uses a fixed ET coefficient)",
    "CO2 (assumed ambient; enrichment not represented)",
    "shading, thermal screens and evaporative cooling as controls",
    "greenhouse orientation and site shading",
)

# PAR conversion: ~45-50% of global shortwave is PAR (400-700 nm), at ~4.57 umol/J.
# McCree (1972); Thimijan & Heins (1983) give 1 MJ global ~ 2.04-2.3 mol PAR; we use 2.1.
PAR_MOL_PER_MJ_GLOBAL = 2.1


@dataclass(frozen=True)
class DailyClimate:
    """One day of site weather, in physical units (the fetch script guarantees them)."""

    t_mean_c: float
    t_min_c: float
    t_max_c: float
    solar_mj_m2: float


def from_records(records: list[dict]) -> tuple[DailyClimate, ...]:
    """Parse already-loaded climate records (the `days` list of a data/climate/*.json file).

    Pure: takes data, not a path. Rejects records that would silently corrupt a simulation."""
    out = []
    for i, r in enumerate(records):
        try:
            d = DailyClimate(
                t_mean_c=float(r["t_mean_c"]), t_min_c=float(r["t_min_c"]),
                t_max_c=float(r["t_max_c"]), solar_mj_m2=float(r["solar_mj_m2"]))
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"climate record {i} is unusable: {e}") from e
        if not (-60.0 <= d.t_mean_c <= 60.0) or not (0.0 <= d.solar_mj_m2 <= 45.0):
            raise ValueError(
                f"climate record {i} is outside physical bounds "
                f"(t_mean={d.t_mean_c}, solar={d.solar_mj_m2}) — wrong units?")
        out.append(d)
    if not out:
        raise ValueError("empty climate series")
    return tuple(out)


@dataclass(frozen=True)
class GreenhouseParams:
    """The envelope, as the twin sees it. Defaults describe a single-poly tunnel.

    transmissivity: fraction of outside light reaching the crop. New single PE film passes
      ~0.85-0.90 of PAR; structure, dirt and condensation take it to ~0.60-0.75 in practice
      (FAO plasticulture guidance; Giacomelli & Roberts 1993). Default 0.70.
    unheated_lift_c: how much warmer the DAILY MEAN inside air runs than outside in a closed,
      unheated tunnel. Measured passive tunnels run ~1-5 C on the daily mean (much higher at
      midday, near zero at night); ventilation eats most of the midday gain. Default 3.0.
    water_tau_days: first-order time constant of the water mass toward inside air temperature.
      Tanks of 1-10 m3 under cover settle in roughly 1-3 days; sun-exposed shallow beds are
      faster. Default 2.0.
    heat_setpoint_c: if set, a heater holds water at or above this temperature — the model
      then reports the implied heating load as degree-days rather than pretending it is free.
    shade_to_ambient: True for an outdoor/shade-net system: no lift, full outside swing.
    """

    transmissivity: float = 0.70
    unheated_lift_c: float = 3.0
    water_tau_days: float = 2.0
    heat_setpoint_c: float | None = None
    shade_to_ambient: bool = False


def inside_air_mean_c(day: DailyClimate, gh: GreenhouseParams) -> float:
    if gh.shade_to_ambient:
        return day.t_mean_c
    return day.t_mean_c + gh.unheated_lift_c


def par_inside_mol_m2(day: DailyClimate, gh: GreenhouseParams) -> float:
    tr = 1.0 if gh.shade_to_ambient else gh.transmissivity
    return day.solar_mj_m2 * PAR_MOL_PER_MJ_GLOBAL * tr


def water_temp_next_c(water_c: float, air_mean_c: float, gh: GreenhouseParams,
                      dt_days: float = 1.0) -> tuple[float, float]:
    """Water temperature after one step, plus the heating shortfall in degree-days.

    First-order relaxation toward inside air. If a heat setpoint is declared, the water is
    held there and the DEFICIT (setpoint minus unheated temperature, in C·days) is returned
    so the caller can report what the heater had to supply. Unheated systems return 0."""
    if dt_days <= 0:
        raise ValueError("dt_days must be positive")
    f = 1.0 - exp(-dt_days / gh.water_tau_days)
    unheated = water_c + (air_mean_c - water_c) * f
    if gh.heat_setpoint_c is not None and unheated < gh.heat_setpoint_c:
        return gh.heat_setpoint_c, (gh.heat_setpoint_c - unheated) * dt_days
    return unheated, 0.0
