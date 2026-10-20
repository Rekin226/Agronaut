"""Internal fixed-order daily soil-water composition with shared physical storage."""

from .soil_evaporation import evaporate_soil_profile
from .soil_infiltration import drain_and_infiltrate
from .soil_roots import transpire_soil_profile

MODEL_VERSION = "soil-water-1-wetting-evaporation"
COUPLED_MODEL_VERSION = "soil-water-2-wetting-evaporation-transpiration"


def wet_and_evaporate_soil_profile(storage, drainage_parameters, surface_states, supplies,
                                   evaporation_parameters, memories, weather, coefficients):
    """Advance wetting then evaporation without adding incoming water twice.

    Explicit per-patch supplies determine rainfall, runoff and irrigation. Rain in
    the ET weather record is retained as evidence but does not enter this balance;
    callers must select the appropriate rainfall source before constructing supplies.
    This is a fixed-order daily approximation, not a subdaily event simulation or
    a complete crop water balance. It does not infer irrigation from crop demand.
    """
    wetting = drain_and_infiltrate(storage, drainage_parameters, surface_states, supplies)
    patch_areas = {p.id: storage.area_m2 * p.area_fraction for p in storage.patches}
    infiltrated_mm = {row["patch_id"]: row["infiltrated_l"] / patch_areas[row["patch_id"]]
                     for row in wetting["patches"]}
    evaporation = evaporate_soil_profile(wetting["infiltration"].ending, dict(wetting["ending_surface"]),
        evaporation_parameters, memories, weather, coefficients, infiltrated_mm=infiltrated_mm)
    ending = evaporation["soil_balance"].ending
    final_surface = dict(evaporation["ending_surface"])
    wet_totals, evap_totals = wetting["totals"], evaporation["totals"]
    totals = dict(initial_soil_l=storage.water_l, initial_surface_l=wet_totals["initial_surface_l"],
        rainfall_l=wet_totals["rainfall_l"], irrigation_at_surface_l=wet_totals["irrigation_at_surface_l"],
        infiltrated_l=wet_totals["infiltrated_l"],
        supplied_rainfall_runoff_l=wet_totals["supplied_rainfall_runoff_l"],
        modeled_surface_overflow_l=wet_totals["modeled_surface_overflow_l"],
        total_runoff_l=wet_totals["total_runoff_l"], total_deep_drainage_l=wet_totals["total_deep_drainage_l"],
        soil_evaporation_l=evap_totals["soil_evaporation_l"], ponded_evaporation_l=evap_totals["ponded_evaporation_l"],
        total_evaporation_l=evap_totals["total_evaporation_l"], final_soil_l=ending.water_l,
        final_surface_l=sum(patch_areas[key] * value.stored_mm for key, value in final_surface.items()))
    totals["combined_balance_residual_l"] = (
        totals["initial_soil_l"] + totals["initial_surface_l"] + totals["rainfall_l"]
        + totals["irrigation_at_surface_l"] - totals["final_soil_l"] - totals["final_surface_l"]
        - totals["total_runoff_l"] - totals["total_deep_drainage_l"] - totals["total_evaporation_l"])
    if totals["combined_balance_residual_l"]:
        raise ArithmeticError("wetting and evaporation failed combined water conservation")
    return dict(model_version=MODEL_VERSION, evidence_status="experimental", day=weather.day,
        timezone=weather.timezone, time_step_days=1, phase_order=("drainage", "infiltration", "evaporation"),
        initial=storage, ending=ending, ending_surface=evaporation["ending_surface"],
        ending_memories=evaporation["ending_memories"], wetting=wetting, evaporation=evaporation,
        totals=totals, units={key: "L" for key in totals},
        rainfall_selection="explicit_patch_supplies_replace_weather_rainfall",
        sources=tuple(sorted(set(wetting["sources"]) | set(evaporation["sources"]))),
        assumptions=(*wetting["assumptions"], *evaporation["assumptions"],
            "Explicit patch supplies replace rainfall in the ET weather record; weather rainfall is not added separately.",
            "Actual admitted infiltration recharges drying memory in local patch millimeters; it is already present in the soil state."),
        limitations=(*wetting["limitations"], *evaporation["limitations"],
            "The fixed daily ordering puts all wetting before evaporation; event timing and within-day feedback are unresolved.",
            "No crop transpiration or root stress is included; this result is not a complete crop water balance or irrigation recommendation."))


def advance_soil_water_day(storage, drainage_parameters, surface_states, supplies,
                           evaporation_parameters, memories, weather, evaporation_coefficients,
                           root_parameters, root_activities, transpiration_coefficients):
    """Drain, wet, evaporate, then extract crop water from one shared inventory.

    Drought stress acts on transpiration demand and root capacity, never on soil
    evaporation or a combined ET total. All coefficients refer to this day's fixed
    patch geometry. This function does not validate dates against a prior saved
    state; a dated replay must enforce that separately before adopting its result.
    """
    wet_evap = wet_and_evaporate_soil_profile(storage, drainage_parameters, surface_states, supplies,
        evaporation_parameters, memories, weather, evaporation_coefficients)
    roots = transpire_soil_profile(wet_evap["ending"], root_parameters, root_activities, weather,
                                  transpiration_coefficients)
    ending = roots["balance"].ending
    totals = dict(wet_evap["totals"])
    totals.update(final_soil_l=ending.water_l, actual_transpiration_l=roots["totals"]["actual_transpiration_l"],
                  potential_transpiration_l=roots["totals"]["potential_transpiration_l"],
                  unmet_transpiration_potential_l=roots["totals"]["total_unmet_potential_l"])
    totals["actual_evapotranspiration_l"] = totals["total_evaporation_l"]+totals["actual_transpiration_l"]
    totals["combined_balance_residual_l"] = (
        totals["initial_soil_l"] + totals["initial_surface_l"] + totals["rainfall_l"]
        + totals["irrigation_at_surface_l"] - totals["final_soil_l"] - totals["final_surface_l"]
        - totals["total_runoff_l"] - totals["total_deep_drainage_l"] - totals["actual_evapotranspiration_l"])
    if totals["combined_balance_residual_l"]:
        raise ArithmeticError("coupled soil and surface water balance failed conservation")
    return dict(model_version=COUPLED_MODEL_VERSION, evidence_status="experimental", day=weather.day,
        timezone=weather.timezone, time_step_days=1,
        phase_order=("drainage", "infiltration", "evaporation", "root_uptake"),
        initial=storage, ending=ending, ending_surface=wet_evap["ending_surface"],
        ending_memories=wet_evap["ending_memories"], wetting=wet_evap["wetting"],
        evaporation=wet_evap["evaporation"], roots=roots, totals=totals,
        units={key: "L" for key in totals}, rainfall_selection=wet_evap["rainfall_selection"],
        sources=tuple(sorted(set(wet_evap["sources"]) | set(roots["sources"]))),
        assumptions=(*wet_evap["assumptions"], *roots["assumptions"],
            "Root stress is evaluated after wetting and evaporation; only root uptake is limited by wilting point. Evaporation can use water below wilting.",
            "Evaporation and transpiration coefficients must form a consistent daily crop/energy parameterization; no automatic conversion from single or basal crop coefficients."),
        limitations=(*roots["limitations"],
            "This fixed-order daily balance does not resolve rainfall or irrigation timing, within-day competition for water, lateral exchange or capillary rise.",
            "Evaporation uses uniformly mixed compartments, explicit drying memory and supplied coefficients; its maximum depth and the rooting depth must be cell boundaries.",
            "No operational state adoption, independent pilot validation or irrigation prescription is provided by this calculation."))
