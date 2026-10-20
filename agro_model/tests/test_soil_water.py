"""Combined soil/surface accounting with actual wetting and drying memory."""

from dataclasses import replace
from datetime import date, timedelta
from fractions import Fraction
from random import Random

from agro_model.soil_drainage import DrainageParameters
from agro_model.soil_evaporation import (
    PatchEvaporationCoefficients,
    SoilEvaporationMemory,
    SoilEvaporationParameters,
)
from agro_model.soil_infiltration import SurfaceWaterState, SurfaceWaterSupply
from agro_model.soil_roots import (
    CropTranspirationCoefficients,
    RootLayerActivity,
    RootUptakeParameters,
)
from agro_model.soil_storage import SoilCompartment, SoilPatch, SoilStorage
from agro_model.soil_water import advance_soil_water_day, wet_and_evaporate_soil_profile
from agro_model.water import ReferenceETDay

SOURCE = "Synthetic combined water test; not a farm observation"


def setup(*, area=1, fractions=(1,), conductivity=100, et0=5):
    cells = tuple(SoilCompartment(str(i), top, bottom, .3, .2, .45, .1, .2, SOURCE)
                  for i, (top, bottom) in enumerate(((0, .1), (.1, .3), (.3, 1))))
    state = SoilStorage(area, tuple(SoilPatch(str(i), f, cells) for i, f in enumerate(fractions)), SOURCE)
    drainage = {(p.id, c.id): DrainageParameters(.4, conductivity, SOURCE)
                for p in state.patches for c in p.compartments}
    surface = {p.id: SurfaceWaterState(0, 10, SOURCE) for p in state.patches}
    supplies = {p.id: SurfaceWaterSupply(0, 0, 0, SOURCE) for p in state.patches}
    params = {p.id: SoilEvaporationParameters(.15, .3, 9, 4, SOURCE) for p in state.patches}
    memory = {p.id: SoilEvaporationMemory(0, .15, 0, SOURCE) for p in state.patches}
    weather = ReferenceETDay(date(2026, 7, 6), et0, 99, SOURCE, "scenario", "UTC", 24, SOURCE)
    coeff = {p.id: PatchEvaporationCoefficients(1, 1, SOURCE) for p in state.patches}
    return [state, drainage, surface, supplies, params, memory, weather, coeff]


def test_rain_runoff_irrigation_and_evaporation_are_each_counted_once():
    inputs = setup()
    inputs[3]["0"] = SurfaceWaterSupply(10, 3, 5, SOURCE)
    result = wet_and_evaporate_soil_profile(*inputs)
    totals = result["totals"]
    assert totals["initial_soil_l"] == 200
    assert totals["rainfall_l"] == 10  # The 99 mm weather value is replaced.
    assert totals["irrigation_at_surface_l"] == 5
    assert totals["infiltrated_l"] == 12
    assert totals["total_runoff_l"] == 3
    assert totals["total_deep_drainage_l"] == 0
    assert totals["soil_evaporation_l"] == 5
    assert totals["final_soil_l"] == 207
    assert totals["combined_balance_residual_l"] == 0
    assert dict(result["ending_memories"])["0"].stage_one_remaining_mm == 4
    assert result["evaporation"]["soil_balance"].initial == result["wetting"]["infiltration"].ending
    assert result["evaporation"]["weather"] == inputs[6]
    assert result["phase_order"] == ("drainage", "infiltration", "evaporation")
    assert inputs[0].water_l == 200 and inputs[5]["0"].stage_one_remaining_mm == 0


def test_localized_wetting_recharges_memory_in_local_depth_not_field_volume():
    inputs = setup(area=100, fractions=(.25, .75), et0=0)
    inputs[3]["0"] = SurfaceWaterSupply(0, 0, 2, SOURCE)
    result = wet_and_evaporate_soil_profile(*inputs)
    assert result["totals"]["infiltrated_l"] == 50
    assert result["ending"].water_l == 20050
    assert dict(result["ending_memories"])["0"].stage_one_remaining_mm == 2
    assert dict(result["ending_memories"])["1"].stage_one_remaining_mm == 0
    assert dict(result["evaporation"]["infiltrated_mm"]) == {"0": 2, "1": 0}


def test_rejected_wetting_evaporates_from_pond_and_carries_remaining_water():
    inputs = setup(conductivity=0)
    inputs[3]["0"] = SurfaceWaterSupply(20, 3, 0, SOURCE)
    result = wet_and_evaporate_soil_profile(*inputs)
    assert result["totals"]["infiltrated_l"] == 0
    assert result["totals"]["modeled_surface_overflow_l"] == 7
    assert result["totals"]["total_runoff_l"] == 10
    assert result["totals"]["ponded_evaporation_l"] == 5
    assert result["totals"]["soil_evaporation_l"] == 0
    assert result["totals"]["final_surface_l"] == 5
    inputs[0] = result["ending"]
    inputs[2] = dict(result["ending_surface"])
    inputs[3]["0"] = SurfaceWaterSupply(0, 0, 0, SOURCE)
    inputs[5] = dict(result["ending_memories"])
    inputs[6] = replace(inputs[6], day=inputs[6].day + timedelta(days=1))
    second = wet_and_evaporate_soil_profile(*inputs)
    assert second["totals"]["initial_surface_l"] == 5
    assert second["totals"]["final_surface_l"] == 0
    assert second["totals"]["total_evaporation_l"] == 5
    assert second["ending"].water_l == 200


def test_admitted_rain_not_offered_rain_sets_the_drying_counter():
    inputs = setup(conductivity=1, et0=0)
    inputs[2]["0"] = SurfaceWaterState(0, 0, SOURCE)
    inputs[3]["0"] = SurfaceWaterSupply(20, 0, 0, SOURCE)
    result = wet_and_evaporate_soil_profile(*inputs)
    assert result["totals"]["infiltrated_l"] == 1
    assert result["totals"]["total_runoff_l"] == 19
    assert dict(result["ending_memories"])["0"].stage_one_remaining_mm == 1


def test_seeded_multiday_wetting_and_drying_preserve_total_water_and_layer_bounds():
    rng = Random(7391)
    inputs = setup(area=17, fractions=(.25, .75))
    for key in inputs[1]:
        inputs[1][key] = DrainageParameters(Fraction(rng.randrange(1, 9), 10), rng.randrange(1, 30), SOURCE)
    initial = inputs[0].water_l
    inflow = outflow = Fraction(0)
    for day in range(40):
        for key in inputs[3]:
            rain = rng.randrange(30)
            inputs[3][key] = SurfaceWaterSupply(rain, rng.randrange(rain+1), rng.randrange(12), SOURCE)
        inputs[6] = replace(inputs[6], day=date(2026, 7, 6)+timedelta(days=day), et0_mm_day=rng.randrange(13))
        result = wet_and_evaporate_soil_profile(*inputs)
        totals = result["totals"]
        inflow += totals["rainfall_l"] + totals["irrigation_at_surface_l"]
        outflow += totals["total_runoff_l"] + totals["total_deep_drainage_l"] + totals["total_evaporation_l"]
        assert initial + inflow == totals["final_soil_l"] + totals["final_surface_l"] + outflow
        assert totals["combined_balance_residual_l"] == 0
        for patch in result["ending"].patches:
            for cell in patch.compartments:
                assert cell.evaporation_floor <= cell.water_content <= cell.saturation
        inputs[0] = result["ending"]
        inputs[2] = dict(result["ending_surface"])
        inputs[5] = dict(result["ending_memories"])


def root_inputs(storage):
    params = {p.id: RootUptakeParameters(1, 1, .5, 0, 1, SOURCE) for p in storage.patches}
    activity = {(p.id, c.id): RootLayerActivity(.03, 1, SOURCE) for p in storage.patches for c in p.compartments}
    coeff = {p.id: CropTranspirationCoefficients(.8, 1, SOURCE) for p in storage.patches}
    return params, activity, coeff


def test_coupled_counterexample_stresses_transpiration_without_stressing_evaporation():
    inputs = setup(conductivity=0)
    p = inputs[0].patches[0]
    inputs[0] = replace(inputs[0], patches=(replace(p, compartments=tuple(replace(c, water_content=.227) for c in p.compartments)),))
    inputs[5]["0"] = SoilEvaporationMemory(9, .15, None, SOURCE)
    inputs[7]["0"] = PatchEvaporationCoefficients(.4, .4, SOURCE)
    result = advance_soil_water_day(*inputs, *root_inputs(inputs[0]))
    assert result["totals"]["soil_evaporation_l"] == 2
    assert result["roots"]["patches"][0]["drought_factor"] == Fraction(1, 2)
    assert result["totals"]["potential_transpiration_l"] == 4
    assert result["totals"]["actual_transpiration_l"] == 2
    assert result["totals"]["actual_evapotranspiration_l"] == 4
    assert result["ending"].water_l == 223
    assert result["totals"]["combined_balance_residual_l"] == 0
    assert result["roots"]["balance"].initial == result["evaporation"]["soil_balance"].ending


def test_at_wilting_evaporation_changes_storage_while_roots_extract_nothing():
    inputs = setup(conductivity=0)
    result = advance_soil_water_day(*inputs, *root_inputs(inputs[0]))
    assert result["totals"]["soil_evaporation_l"] > 0
    assert result["totals"]["actual_transpiration_l"] == 0
    assert result["ending"].water_l == 200-result["totals"]["soil_evaporation_l"]
    assert result["totals"]["combined_balance_residual_l"] == 0


def test_root_extraction_carries_soil_loss_into_the_next_days_evaporation():
    inputs = setup(conductivity=0)
    p = inputs[0].patches[0]
    inputs[0] = replace(inputs[0], patches=(replace(p, compartments=tuple(replace(c, water_content=.3) for c in p.compartments)),))
    inputs[5]["0"] = SoilEvaporationMemory(9, .15, None, SOURCE)
    first = advance_soil_water_day(*inputs, *root_inputs(inputs[0]))
    inputs[0] = first["ending"]
    inputs[2] = dict(first["ending_surface"])
    inputs[5] = dict(first["ending_memories"])
    inputs[6] = replace(inputs[6], day=inputs[6].day+timedelta(days=1))
    second = advance_soil_water_day(*inputs, *root_inputs(inputs[0]))
    assert second["wetting"]["drainage"]["balance"].initial == first["ending"]
    assert first["initial"].water_l-second["ending"].water_l == (
        first["totals"]["actual_evapotranspiration_l"] + second["totals"]["actual_evapotranspiration_l"])


def test_coupled_seeded_replay_closes_cumulative_balance_with_unequal_patch_wetting():
    rng = Random(73101)
    inputs = setup(area=17, fractions=(.25, .75), conductivity=10)
    roots = root_inputs(inputs[0])
    inflow = outflow = Fraction(0)
    initial = inputs[0].water_l
    for day in range(60):
        for key in inputs[3]:
            rain = rng.randrange(30)
            inputs[3][key] = SurfaceWaterSupply(rain, rng.randrange(rain+1), rng.randrange(10), SOURCE)
        inputs[6] = replace(inputs[6], day=date(2026, 7, 6)+timedelta(days=day), et0_mm_day=rng.randrange(10))
        result = advance_soil_water_day(*inputs, *roots)
        t = result["totals"]
        inflow += t["rainfall_l"] + t["irrigation_at_surface_l"]
        outflow += t["total_runoff_l"] + t["total_deep_drainage_l"] + t["actual_evapotranspiration_l"]
        assert initial+inflow == result["ending"].water_l+t["final_surface_l"]+outflow
        assert t["combined_balance_residual_l"] == 0
        assert t["actual_evapotranspiration_l"] == t["total_evaporation_l"]+t["actual_transpiration_l"]
        inputs[0] = result["ending"]
        inputs[2] = dict(result["ending_surface"])
        inputs[5] = dict(result["ending_memories"])
