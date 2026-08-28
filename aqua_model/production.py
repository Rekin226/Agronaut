"""The production twin: one system, one climate, one season — fish, nitrogen and crops coupled.

`twin.py` rolls the nitrogen cascade forward at constant temperature. This module supplies
what an operator plans a season with: the couplings. Weather sets water temperature and
light; water temperature sets appetite and fish growth; feed drives the nitrogen cascade;
nitrate and climate set crop growth; crop growth sets the nitrate drawdown. Close the loop
and the questions the project exists for become askable:

    "How many kilograms of fish and vegetables does THIS design produce at THIS site?"
    "What does a heater buy me in January — and what does it cost in degree-days?"
    "If I stock in March instead of May, when is my first harvest?"

Composition, not reinvention: the nitrogen physics is `twin.step` unchanged, fish growth is
`fishgrowth.grow`, crop response is `cropgrowth.factors`, climate is `climate.*`. Each keeps
its own cited parameters and its own NOT_MODELLED list; this module only wires them and adds
the honesty of the whole (`ProductionRun.not_modelled` is the union).

Feed discipline — the one subtle wire: `twin.step` gates feed by temperature internally, and
`fishgrowth.grow` gates appetite with the same factor. Both are therefore handed the SAME
ungated ration, and each applies the gate itself; the fish eat exactly the feed the nitrogen
model dissolves. The shared factor is `species.temperature_feed_factor`, and a test asserts
the two stay equal, because silent disagreement there would split the twin into two systems.

Pure and deterministic. Climate arrives as data (`climate.from_records`); nothing here fetches.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from . import climate as C
from . import cropgrowth as CG
from . import fishgrowth as FG
from .crops import Crop
from .species import FishSpecies, temperature_feed_factor
from .twin import (
    NOT_MODELLED as TWIN_NOT_MODELLED, StepResult, TwinParams, TwinState, mature_biofilter,
    step as nitrogen_step,
)

NOT_MODELLED = tuple(dict.fromkeys(
    TWIN_NOT_MODELLED + C.NOT_MODELLED + FG.NOT_MODELLED + CG.NOT_MODELLED + (
        "economics (feed cost, energy price, crop price — see economics KB)",
        "labour and operator error",
    )))


@dataclass(frozen=True)
class ProductionParams:
    """Everything uncertain, gathered for deliberate variation (same doctrine as TwinParams)."""

    twin: TwinParams = field(default_factory=TwinParams)
    greenhouse: C.GreenhouseParams = field(default_factory=C.GreenhouseParams)
    tgc_value: float | None = None       # calibration hook; None = species seed


@dataclass(frozen=True)
class ProductionState:
    """Everything the production twin carries forward."""

    nitrogen: TwinState
    fish: FG.Cohort
    water_temp_c: float
    day: int = 0
    harvested_fish_kg: float = 0.0
    restocked_fish_kg: float = 0.0       # fingerlings BOUGHT, not grown — see realized FCR
    harvested_crop_kg: float = 0.0
    feed_used_kg: float = 0.0
    heat_deficit_c_days: float = 0.0     # what a heater had to supply; 0 when unheated


@dataclass(frozen=True)
class ProductionDay:
    """One day's state plus why: crop factors and nitrogen flows are the explanation."""

    state: ProductionState
    crop_factors: CG.CropFactors
    nitrogen: StepResult
    fish_harvested_today_kg: float = 0.0
    warnings: tuple[str, ...] = ()


def start_state(*, volume_l: float, fish_count: int, start_weight_g: float,
                water_temp_c: float, species: FishSpecies, cycled: bool = True) -> ProductionState:
    """A defensible starting point. `cycled=True` seeds a mature biofilter for the standing
    ration (an established system); False starts the cycling transient (a new one)."""
    cohort = FG.Cohort(count=fish_count, mean_weight_g=start_weight_g)
    ration = FG.ration_g_day(cohort, species, water_temp_c)
    aob, nob = (mature_biofilter(species, ration) if cycled
                else (TwinState().aob_capacity_g_day, TwinState().nob_capacity_g_day))
    nitrogen = TwinState(volume_l=volume_l, fish_biomass_kg=cohort.biomass_kg(),
                         aob_capacity_g_day=aob, nob_capacity_g_day=nob)
    return ProductionState(nitrogen=nitrogen, fish=cohort, water_temp_c=water_temp_c)


def start_state_from_design(out, species: FishSpecies, *, water_temp_c: float,
                            start_weight_g: float = 20.0,
                            cycled: bool = False) -> ProductionState:
    """The agreed design, stocked on day one — the bridge from "here is your system"
    to "here is what it will do".

    Numbers come from the DesignOutput, not from the operator retyping them: fish count
    and system volume are the sizing model's own, so the twin simulates exactly the
    system that was designed. Fish start at fingerling weight (a new build stocks
    fingerlings, not the harvest-size standing crop sizing plans around), and the
    biofilter starts UNCYCLED by default, because a new build's does — the cycling
    transient, nitrite spike included, is part of an honest first season. Pass
    `cycled=True` only for a design being applied to an already-running system."""
    if out.fish_count <= 0 or out.system_volume_l <= 0:
        raise ValueError("design has no stocked fish or no volume — was it feasible?")
    return start_state(
        volume_l=out.system_volume_l, fish_count=out.fish_count,
        start_weight_g=start_weight_g, water_temp_c=water_temp_c,
        species=species, cycled=cycled)


def step_production(
    state: ProductionState,
    day_weather: C.DailyClimate,
    species: FishSpecies,
    species_key: str,
    crop: Crop,
    grow_area_m2: float,
    *,
    params: ProductionParams | None = None,
    harvest_at_g: float | None = None,
    restock_weight_g: float = 20.0,
) -> ProductionDay:
    """Advance one day. Order mirrors causality: weather -> water -> fish eat and grow ->
    nitrogen moves -> crops grow on the nitrate and the light."""
    params = params or ProductionParams()
    gh = params.greenhouse

    # 1. climate -> growing conditions
    air_c = C.inside_air_mean_c(day_weather, gh)
    water_c, deficit = C.water_temp_next_c(state.water_temp_c, air_c, gh)
    dli = C.par_inside_mol_m2(day_weather, gh)

    # 2. fish eat and grow at the water temperature.
    # The UNGATED ration goes to both the fish model and the nitrogen model; each applies
    # the same temperature gate itself (see module docstring).
    gate = temperature_feed_factor(species, water_c)
    ration_ungated = (FG.ration_g_day(state.fish, species, water_c) / gate) if gate > 0 else 0.0
    growth = FG.grow(state.fish, species, species_key, temperature_c=water_c,
                     feed_offered_g=ration_ungated, tgc_value=params.tgc_value)

    # 3. crops respond to today's conditions and yesterday's nitrate
    fac = CG.factors(crop, dli_mol_m2=dli, temperature_c=air_c,
                     no3_mg_l=state.nitrogen.no3_mg_l)
    uptake_g_day = CG.n_uptake_g_day(crop, fac, grow_area_m2)

    # 4. nitrogen cascade on the actual feed
    nstep = nitrogen_step(
        state.nitrogen, species, feed_g_per_day=ration_ungated, temperature_c=water_c,
        plant_uptake_capacity_g_day=uptake_g_day, params=params.twin)

    # 5. harvests
    crop_kg = CG.harvest_rate_kg_m2_day(crop, fac) * grow_area_m2
    fish = growth.cohort
    fish_harvested = 0.0
    restocked_kg = 0.0
    target = harvest_at_g if harvest_at_g is not None else species.harvest_weight_kg * 1000.0
    warnings = list(nstep.warnings)
    if fish.mean_weight_g >= target > 0:
        fish_harvested = fish.biomass_kg()
        fish = FG.Cohort(count=fish.count, mean_weight_g=restock_weight_g)
        restocked_kg = fish.biomass_kg()
        warnings.append(
            f"day {state.day + 1}: cohort harvested at {target:.0f} g and restocked at "
            f"{restock_weight_g:.0f} g — batch culture; staggered cohorts are not modelled")

    # The cohort is the single source of truth for biomass; the nitrogen state mirrors it.
    nitrogen = replace(nstep.state, fish_biomass_kg=fish.biomass_kg())

    new = ProductionState(
        nitrogen=nitrogen,
        fish=fish,
        water_temp_c=water_c,
        day=state.day + 1,
        harvested_fish_kg=state.harvested_fish_kg + fish_harvested,
        restocked_fish_kg=state.restocked_fish_kg + restocked_kg,
        harvested_crop_kg=state.harvested_crop_kg + crop_kg,
        feed_used_kg=state.feed_used_kg + growth.feed_eaten_g / 1000.0,
        heat_deficit_c_days=state.heat_deficit_c_days + deficit,
    )
    return ProductionDay(state=new, crop_factors=fac, nitrogen=nstep,
                         fish_harvested_today_kg=fish_harvested, warnings=tuple(warnings))


@dataclass(frozen=True)
class ProductionSummary:
    days: int
    fish_harvested_kg: float
    fish_standing_kg: float
    crop_harvested_kg: float
    feed_used_kg: float
    realized_fcr: float
    heat_deficit_c_days: float
    water_temp_min_c: float
    water_temp_max_c: float
    peak_tan_mg_l: float
    peak_no2_mg_l: float
    peak_no3_mg_l: float
    mean_f_light: float
    mean_f_temp: float
    mean_f_nitrogen: float
    limiting_factor: str
    warnings: tuple[str, ...]
    not_modelled: tuple[str, ...] = NOT_MODELLED


@dataclass(frozen=True)
class ProductionRun:
    trajectory: list[ProductionDay]
    summary: ProductionSummary


def simulate_production(
    initial: ProductionState,
    weather: tuple[C.DailyClimate, ...],
    species: FishSpecies,
    species_key: str,
    crop: Crop,
    grow_area_m2: float,
    *,
    params: ProductionParams | None = None,
    harvest_at_g: float | None = None,
    restock_weight_g: float = 20.0,
) -> ProductionRun:
    """Roll a system through a weather series and account for everything that came out.

    The summary names the LIMITING FACTOR — the modulation that cost the crop most — because
    "you are light-limited, a heater will not help" is the decision-grade sentence."""
    if not weather:
        raise ValueError("weather series is empty")
    traj: list[ProductionDay] = []
    cur = initial
    for day_weather in weather:
        d = step_production(cur, day_weather, species, species_key, crop, grow_area_m2,
                            params=params, harvest_at_g=harvest_at_g,
                            restock_weight_g=restock_weight_g)
        traj.append(d)
        cur = d.state

    n = len(traj)
    # Growth is what the FEED produced. Restocked fingerlings are bought biomass sitting in
    # the standing crop, so counting them as growth flatters the realized FCR — and the
    # business case reads that FCR to price feed per kilogram of fish.
    fish_growth_kg = (cur.harvested_fish_kg + cur.fish.biomass_kg()
                      - initial.fish.biomass_kg() - cur.restocked_fish_kg)
    means = {
        "light": sum(d.crop_factors.f_light for d in traj) / n,
        "temperature": sum(d.crop_factors.f_temp for d in traj) / n,
        "nitrogen": sum(d.crop_factors.f_nitrogen for d in traj) / n,
    }
    limiting = min(means, key=means.get)

    # Aggregate per-day model warnings into season facts. 200 lines of "25.53 C is outside
    # the optimum" is noise; "feeding was suppressed for 143 days" is a finding.
    suppressed_days = sum(1 for d in traj if any("optimum" in w for w in d.warnings))
    capacity_days = sum(1 for d in traj if any("capacity-limited" in w for w in d.warnings))
    lethal_days = sum(1 for d in traj
                      if d.state.water_temp_c > species.temp_max_c
                      or d.state.water_temp_c < species.temp_min_c)
    warnings: list[str] = []
    if lethal_days:
        warnings.append(
            f"water temperature was OUTSIDE {species.name}'s survivable range "
            f"({species.temp_min_c:.0f}-{species.temp_max_c:.0f} C) on {lethal_days} days — "
            "this projection assumes the fish survive; in reality they may not")
    if suppressed_days:
        warnings.append(
            f"feed intake was temperature-suppressed on {suppressed_days} of {n} days "
            f"(water ran {min(d.state.water_temp_c for d in traj):.1f}-"
            f"{max(d.state.water_temp_c for d in traj):.1f} C against an optimum of "
            f"{species.temp_opt_low_c:.0f}-{species.temp_opt_high_c:.0f} C)")
    if capacity_days:
        warnings.append(
            f"plant uptake was capacity-limited on {capacity_days} days — nitrate "
            "accumulates until crop area, water exchange or harvest cadence changes")
    warnings += [w for d in traj for w in d.warnings
                 if "optimum" not in w and "capacity-limited" not in w]
    warnings = list(dict.fromkeys(warnings))
    summary = ProductionSummary(
        days=n,
        fish_harvested_kg=round(cur.harvested_fish_kg, 2),
        fish_standing_kg=round(cur.fish.biomass_kg(), 2),
        crop_harvested_kg=round(cur.harvested_crop_kg, 2),
        feed_used_kg=round(cur.feed_used_kg, 2),
        realized_fcr=round(cur.feed_used_kg / fish_growth_kg, 2) if fish_growth_kg > 0 else 0.0,
        heat_deficit_c_days=round(cur.heat_deficit_c_days, 1),
        water_temp_min_c=round(min(d.state.water_temp_c for d in traj), 1),
        water_temp_max_c=round(max(d.state.water_temp_c for d in traj), 1),
        peak_tan_mg_l=round(max(d.state.nitrogen.tan_mg_l for d in traj), 3),
        peak_no2_mg_l=round(max(d.state.nitrogen.no2_mg_l for d in traj), 3),
        peak_no3_mg_l=round(max(d.state.nitrogen.no3_mg_l for d in traj), 1),
        mean_f_light=round(means["light"], 2),
        mean_f_temp=round(means["temperature"], 2),
        mean_f_nitrogen=round(means["nitrogen"], 2),
        limiting_factor=limiting,
        warnings=tuple(warnings),
    )
    return ProductionRun(trajectory=traj, summary=summary)


def format_summary(run: ProductionRun, *, site_label: str = "") -> str:
    """Operator-facing season report. States its own limits, as every projection here must."""
    s = run.summary
    lines = [
        f"Season projection{' — ' + site_label if site_label else ''} ({s.days} days)",
        "",
        f"  fish harvested   {s.fish_harvested_kg:.1f} kg "
        f"(+ {s.fish_standing_kg:.1f} kg still in the tank)",
        f"  crop harvested   {s.crop_harvested_kg:.1f} kg",
        f"  feed used        {s.feed_used_kg:.1f} kg (realized FCR {s.realized_fcr:.2f})",
        f"  water temp       {s.water_temp_min_c:.1f}..{s.water_temp_max_c:.1f} C",
        f"  nitrogen peaks   TAN {s.peak_tan_mg_l:.2f} / NO2 {s.peak_no2_mg_l:.2f} / "
        f"NO3 {s.peak_no3_mg_l:.0f} mg/L",
        f"  crop factors     light {s.mean_f_light:.2f} · temp {s.mean_f_temp:.2f} · "
        f"nitrogen {s.mean_f_nitrogen:.2f}  -> most limiting: {s.limiting_factor}",
    ]
    if s.heat_deficit_c_days > 0:
        lines.append(f"  heating          {s.heat_deficit_c_days:.0f} C·days held by the heater")
    if s.warnings:
        lines.append("")
        lines += [f"  ! {w}" for w in s.warnings[:6]]
    from .validation_status import validation_lines
    lines += ["", *validation_lines(),
              "NOT modelled: " + "; ".join(s.not_modelled[:4]) + "; ..."]
    return "\n".join(lines)
