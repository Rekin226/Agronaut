"""Patch input, ponding, runoff and shared daily soil-flow balance cases."""

from dataclasses import replace
from fractions import Fraction
from random import Random

import pytest

from agro_model.soil_drainage import DrainageParameters
from agro_model.soil_infiltration import (
    SurfaceWaterState,
    SurfaceWaterSupply,
    drain_and_infiltrate,
)
from agro_model.soil_storage import (
    SoilCompartment,
    SoilPatch,
    SoilStorage,
    apply_downward_infiltration,
)

SOURCE = "Synthetic wetting scenario; no pilot data or regional coefficient claim"


def setup(*, theta=0.2, depth=1, area=1, fractions=(1,), conductivity=100, stored=0, capacity=0):
    cell = SoilCompartment("soil", 0, depth, 0.3, 0.2, 0.45, 0.1, theta, SOURCE)
    state = SoilStorage(area, tuple(SoilPatch(str(i), f, (cell,)) for i, f in enumerate(fractions)), SOURCE)
    parameters = {(p.id, c.id): DrainageParameters(0.4, conductivity, SOURCE) for p in state.patches for c in p.compartments}
    surface = {p.id: SurfaceWaterState(stored, capacity, SOURCE) for p in state.patches}
    supplies = {p.id: SurfaceWaterSupply(0, 0, 0, SOURCE) for p in state.patches}
    return state, parameters, surface, supplies


def supply(rain=0, runoff=0, irrigation=0):
    return SurfaceWaterSupply(rain, runoff, irrigation, SOURCE)


def test_small_rain_fills_dry_soil_without_inventing_drainage():
    state, parameters, surface, supplies = setup(depth=0.1)
    supplies["0"] = supply(rain=5)
    result = drain_and_infiltrate(state, parameters, surface, supplies)
    assert result["infiltration"].ending.water_l == 25
    assert result["totals"]["total_deep_drainage_l"] == 0
    assert result["totals"]["total_runoff_l"] == 0
    assert result["totals"]["infiltrated_l"] == 5
    assert result["totals"]["combined_balance_residual_l"] == 0
    assert result["phase_order"] == ("drainage", "infiltration")


def test_supplied_rain_runoff_is_subtracted_once_and_irrigation_is_surface_water():
    state, parameters, surface, supplies = setup(depth=0.1)
    supplies["0"] = supply(rain=10, runoff=3, irrigation=5)
    result = drain_and_infiltrate(state, parameters, surface, supplies)
    assert result["infiltration"].ending.water_l == 32
    assert result["totals"]["rainfall_l"] == 10
    assert result["totals"]["irrigation_at_surface_l"] == 5
    assert result["totals"]["infiltrated_l"] == 12
    assert result["totals"]["total_runoff_l"] == 3
    assert result["totals"]["modeled_surface_overflow_l"] == 0
    assert dict(result["supplies"])["0"] == supplies["0"]
    assert SOURCE in result["sources"]


def test_surface_entry_limit_partitions_water_between_soil_ponding_and_overflow():
    state, parameters, surface, supplies = setup(theta=0.3, capacity=20)
    parameters[("0", "soil")] = DrainageParameters(1, 100, SOURCE)
    supplies["0"] = supply(rain=200)
    result = drain_and_infiltrate(state, parameters, surface, supplies)
    assert result["infiltration"].ending.water_l == 400
    assert result["totals"]["infiltrated_l"] == 100
    assert result["totals"]["final_surface_l"] == 20
    assert result["totals"]["modeled_surface_overflow_l"] == 80
    assert result["totals"]["surface_entry_rejected_l"] == 100
    assert result["totals"]["total_deep_drainage_l"] == 0


def test_saturated_profile_shares_the_flow_allowance_between_both_phases():
    state, parameters, surface, supplies = setup(theta=0.45)
    supplies["0"] = supply(rain=100)
    result = drain_and_infiltrate(state, parameters, surface, supplies)
    assert result["drainage"]["balance"].external_output_l == 60
    assert result["infiltration"].external_output_l == 40
    assert result["totals"]["total_deep_drainage_l"] == 100
    assert result["infiltration"].ending.water_l == state.water_l == 450
    assert result["diagnostics"][0]["remaining_daily_limit_l"] == 40
    assert result["totals"]["total_runoff_l"] == 0
    assert result["infiltration"].application_mode == "simultaneous_downward_infiltration"


def test_exhausted_flow_allowance_still_allows_filling_storage_released_by_drainage():
    state, parameters, surface, supplies = setup(theta=0.45, conductivity=1)
    supplies["0"] = supply(rain=10)
    result = drain_and_infiltrate(state, parameters, surface, supplies)
    assert result["totals"]["prior_deep_drainage_l"] == 1
    assert result["totals"]["additional_deep_drainage_l"] == 0
    assert result["totals"]["infiltrated_l"] == 1
    assert result["totals"]["total_runoff_l"] == 9
    assert result["infiltration"].ending.water_l == 450


def test_partial_curve_transmission_can_limit_flow_before_daily_allowance_is_exhausted():
    state, parameters, surface, supplies = setup(theta=0.31, depth=0.1, conductivity=1)
    supplies["0"] = supply(rain=0.1)
    result = drain_and_infiltrate(state, parameters, surface, supplies)
    # Incoming volumetric demand=.1/100=.001; peak=.4*(.45-.3)=.06.
    # Ksat * response/peak = 1/60 mm/day. The rest (1/12 mm) stays in soil.
    assert result["diagnostics"][0]["curve_transmission_limit_l"] == Fraction(1, 60)
    assert result["diagnostics"][0]["remaining_daily_limit_l"] > Fraction(1, 60)
    assert result["infiltration"].external_output_l == Fraction(1, 60)
    assert result["infiltration"].ending.water_l - result["infiltration"].initial.water_l == Fraction(1, 12)
    assert result["totals"]["modeled_surface_overflow_l"] == 0


@pytest.mark.parametrize("top,expected_infiltration,expected_ponding,expected_overflow", [
    (0.45, 0, 5, 30), (0.3, 15, 5, 15),
])
def test_impermeable_bottom_returns_unstorable_water_to_the_surface(top, expected_infiltration, expected_ponding, expected_overflow):
    state, parameters, surface, supplies = setup(theta=0.45, capacity=5)
    patch = state.patches[0]
    first = replace(patch.compartments[0], id="top", bottom_m=0.1, water_content=top)
    last = replace(patch.compartments[0], top_m=0.1)
    state = replace(state, patches=(replace(patch, compartments=(first, last)),))
    parameters[("0", "top")] = DrainageParameters(0.4, 100, SOURCE)
    parameters[("0", "soil")] = DrainageParameters(0, 0, SOURCE)
    supplies["0"] = supply(rain=35)
    result = drain_and_infiltrate(state, parameters, surface, supplies)
    assert result["totals"]["infiltrated_l"] == expected_infiltration
    assert result["totals"]["final_surface_l"] == expected_ponding
    assert result["totals"]["modeled_surface_overflow_l"] == expected_overflow
    assert result["totals"]["total_deep_drainage_l"] == 0
    assert result["infiltration"].ending.water_l == state.water_l + expected_infiltration
    assert result["totals"]["returned_from_profile_l"] == 35 - expected_infiltration


def test_irrigation_patch_depth_is_not_mistaken_for_a_field_average():
    state, parameters, surface, supplies = setup(area=100, fractions=(0.25, 0.75))
    supplies["0"] = supply(irrigation=20)
    result = drain_and_infiltrate(state, parameters, surface, supplies)
    assert result["totals"]["irrigation_at_surface_l"] == 500
    assert result["totals"]["irrigation_at_surface_l"] / state.area_m2 == 5
    assert result["infiltration"].ending.patches[0].compartments[0].water_content == Fraction(22, 100)
    assert result["infiltration"].ending.patches[1].compartments[0].water_content == Fraction(2, 10)


def test_carried_surface_water_infiltrates_once_and_continues_without_new_rain():
    state, parameters, surface, supplies = setup(conductivity=3, stored=10, capacity=20)
    first = drain_and_infiltrate(state, parameters, surface, supplies)
    second = drain_and_infiltrate(first["infiltration"].ending, parameters, dict(first["ending_surface"]), supplies)
    assert first["infiltration"].ending.water_l == 203
    assert first["totals"]["final_surface_l"] == 7
    assert second["infiltration"].ending.water_l == 206
    assert second["totals"]["final_surface_l"] == 4
    assert first["totals"]["rainfall_l"] == second["totals"]["rainfall_l"] == 0
    assert second["totals"]["combined_balance_residual_l"] == 0
    assert surface["0"].stored_mm == 10
    assert state.water_l == 200


def test_zero_surface_water_has_no_additional_effect_after_drainage():
    state, parameters, surface, supplies = setup(theta=0.4)
    result = drain_and_infiltrate(state, parameters, surface, supplies)
    assert result["infiltration"].ending.patches == result["drainage"]["balance"].ending.patches
    assert result["infiltration"].external_input_l == result["infiltration"].external_output_l == 0
    assert result["totals"]["total_deep_drainage_l"] > 0
    assert result["totals"]["total_runoff_l"] == 0


def test_seeded_profiles_preserve_complete_soil_surface_balance_and_daily_limits():
    rng = Random(735)
    for _ in range(80):
        cells, parameters = [], {}
        for index in range(5):
            fc = Fraction(rng.randrange(20, 36), 100)
            sat = fc + Fraction(rng.randrange(5, 21), 100)
            theta = Fraction(1, 10) + Fraction(rng.randrange(101), 100) * (sat - Fraction(1, 10))
            cells.append(SoilCompartment(str(index), Fraction(index, 10), Fraction(index+1, 10), fc, 0.15, sat, 0.1, theta, SOURCE))
            parameters[("p", str(index))] = DrainageParameters(Fraction(rng.randrange(11), 10), rng.randrange(100), SOURCE)
        state = SoilStorage(13, (SoilPatch("p", 1, tuple(cells)),), SOURCE)
        surface = {"p": SurfaceWaterState(rng.randrange(31), 30, SOURCE)}
        rain = rng.randrange(301)
        supplies = {"p": supply(rain, rng.randrange(rain+1), rng.randrange(201))}
        result = drain_and_infiltrate(state, parameters, surface, supplies)
        t = result["totals"]
        before = state.water_l + 13 * surface["p"].stored_mm
        incoming = 13 * (supplies["p"].rainfall_mm + supplies["p"].irrigation_at_surface_mm)
        after = result["infiltration"].ending.water_l + 13 * dict(result["ending_surface"])["p"].stored_mm
        assert before + incoming == after + t["total_runoff_l"] + t["total_deep_drainage_l"]
        assert t["combined_balance_residual_l"] == 0
        assert all(r == 0 for _, _, r in result["infiltration"].compartment_residuals_l)
        prior = {r.request.from_layer_id: r.actual_l for r in result["drainage"]["balance"].fluxes}
        wet = {r.request.from_layer_id: r.actual_l for r in result["infiltration"].fluxes if r.request.from_layer_id is not None}
        for cell in cells:
            assert prior[cell.id] + wet[cell.id] <= 13 * parameters[("p", cell.id)].saturated_conductivity_mm_day


def test_simultaneous_infiltration_validates_net_storage_and_explicit_inputs():
    state, _, _, _ = setup(theta=0.45)
    balance = apply_downward_infiltration(state, {"0": 5}, {("0", "soil"): 5}, SOURCE)
    assert balance.ending.water_l == state.water_l
    assert balance.external_input_l == balance.external_output_l == 5
    for inflows, outflows in ((None, {("0", "soil"): 0}), ({}, {("0", "soil"): 0}),
                              ({"0": 5}, {("0", "soil"): 0}), ({"0": 5}, {("0", "soil"): 6})):
        with pytest.raises(ValueError):
            apply_downward_infiltration(state, inflows, outflows, SOURCE)


@pytest.mark.parametrize("changes", [{"rainfall_mm": -1}, {"rainfall_mm": None},
    {"rainfall_runoff_mm": 1}, {"irrigation_at_surface_mm": True},
    {"irrigation_at_surface_mm": float("nan")}, {"source": ""}])
def test_invalid_supplies(changes):
    with pytest.raises(ValueError):
        replace(supply(), **changes)


@pytest.mark.parametrize("stored,capacity", [(1, 0), (-1, 10), (0, -1), (True, 1), (None, 1)])
def test_invalid_surface_storage(stored, capacity):
    with pytest.raises(ValueError):
        SurfaceWaterState(stored, capacity, SOURCE)


def test_missing_extra_or_unvalidated_patch_inputs_are_rejected():
    state, parameters, surface, supplies = setup()
    for values in ({}, {"other": supply()}, {"0": {}}, None):
        with pytest.raises(ValueError):
            drain_and_infiltrate(state, parameters, surface, values)
    for values in ({}, {"other": surface["0"]}, {"0": {}}, None):
        with pytest.raises(ValueError):
            drain_and_infiltrate(state, parameters, values, supplies)
