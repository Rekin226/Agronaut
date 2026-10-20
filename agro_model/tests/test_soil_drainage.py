"""Drainage curve checks and independent layered mass/flow cases."""

from dataclasses import replace
from fractions import Fraction
from math import exp, log
from random import Random

import pytest

from agro_model.soil_drainage import (
    DrainageParameters,
    drain_soil_profile,
    drainage_response,
    water_content_for_response,
)
from agro_model.soil_storage import (
    SoilCompartment,
    SoilPatch,
    SoilStorage,
    apply_downward_drainage,
)

SOURCE = "Synthetic drainage case; not regional calibration or field evidence"


def profile(contents=(0.45, 0.45), *, fractions=(1,), area=1):
    bounds = (0, 0.1, 1) if len(contents) == 2 else (0, 1)
    cells = tuple(SoilCompartment(str(i), bounds[i], bounds[i+1], 0.3, 0.2,
                                 0.45, 0.1, theta, SOURCE) for i, theta in enumerate(contents))
    return SoilStorage(area, tuple(SoilPatch(str(i), f, cells) for i, f in enumerate(fractions)), SOURCE)


def laws(state, *, tau=0.4, conductivity=100):
    return {(patch.id, cell.id): DrainageParameters(tau, conductivity, SOURCE)
            for patch in state.patches for cell in patch.compartments}


def cells(result, patch=0):
    return result["balance"].ending.patches[patch].compartments


def test_published_tau_point_four_loses_forty_percent_of_drainable_water_day_one():
    # Manual §3.7.3 explains tau=.4 as loss of 40% of SAT-FC on the first
    # day from saturation. Synthetic SAT=.45, FC=.30: theta ends at .39.
    state = profile((0.45,))
    result = drain_soil_profile(state, laws(state))
    assert cells(result)[0].water_content == Fraction(39, 100)
    assert result["balance"].external_output_l == 60
    assert result["balance"].balance_residual_l == 0


def test_uniform_profile_result_does_not_depend_on_compartment_thickness():
    state = profile()
    result = drain_soil_profile(state, laws(state))
    assert [cell.water_content for cell in cells(result)] == [Fraction(39, 100)] * 2
    assert [flow.actual_l for flow in result["balance"].fluxes] == [6, 60]
    assert result["balance"].external_output_l == 60
    assert result["balance"].ending.water_l == 390
    single = profile((0.45,))
    assert drain_soil_profile(single, laws(single))["balance"].ending.water_l == 390


def test_curve_and_inverse_against_independent_math_and_endpoints():
    cell = profile((0.4,)).patches[0].compartments[0]
    law = DrainageParameters(0.4, 100, SOURCE)
    expected = 0.4 * 0.15 * (exp(0.1) - 1) / (exp(0.15) - 1)
    response = drainage_response(cell, law)
    assert float(response) == pytest.approx(expected, rel=1e-13)
    assert float(water_content_for_response(cell, law, response)) == pytest.approx(0.4, abs=1e-15)
    assert float(water_content_for_response(cell, law, 0.03)) == pytest.approx(
        0.3 + log(1 + 0.03 * (exp(0.15) - 1) / 0.06), abs=1e-15)
    assert drainage_response(cell, law, 0.3) == 0
    assert drainage_response(cell, law, 0.2) == 0
    assert drainage_response(cell, law, 0.45) == Fraction(6, 100)
    assert water_content_for_response(cell, law, 0) == Fraction(3, 10)
    assert water_content_for_response(cell, law, 0.06) == Fraction(45, 100)
    assert water_content_for_response(cell, law, 0.061) is None


def test_stable_curve_close_to_field_capacity_and_impermeable_curve():
    cell = profile((0.3,)).patches[0].compartments[0]
    law = DrainageParameters(1, 1000, SOURCE)
    excess = Fraction(1, 10**14)
    response = drainage_response(cell, law, cell.field_capacity + excess)
    assert 0 < response <= excess
    assert float(response / excess) == pytest.approx(0.9268742972514642, rel=1e-12)
    assert water_content_for_response(cell, law, response) - cell.field_capacity == pytest.approx(float(excess), abs=1e-28)
    blocked = DrainageParameters(0, 0, SOURCE)
    assert drainage_response(cell, blocked, 0.45) == 0
    assert water_content_for_response(cell, blocked, 0.001) is None


def test_dry_receiving_layer_stores_surface_drainage_without_profile_water_loss():
    state = profile((0.45, 0.2))
    result = drain_soil_profile(state, laws(state))
    assert cells(result)[0].water_content == Fraction(39, 100)
    assert cells(result)[1].water_content == Fraction(186, 900)
    assert result["balance"].external_output_l == 0
    assert result["balance"].internal_transfer_l == 6
    assert result["balance"].ending.water_l == state.water_l


def test_receiving_layer_can_reach_an_intermediate_drainability_before_passing_water():
    original = profile((0.45, 0.35))
    patch = original.patches[0]
    state = replace(original, patches=(replace(patch, compartments=(patch.compartments[0],
                    replace(patch.compartments[1], bottom_m=0.2))),))
    parameters = laws(state)
    parameters[("0", "1")] = DrainageParameters(1, 100, SOURCE)
    result = drain_soil_profile(state, parameters)
    target = 0.3 + log(1 + 0.4 * (exp(0.15) - 1))
    expected_lower = target - 0.06
    assert float(cells(result)[1].water_content) == pytest.approx(expected_lower, abs=1e-15)
    assert float(result["balance"].external_output_l) == pytest.approx(80 - 39 - 100 * expected_lower, abs=1e-12)


def test_unattainable_upstream_drainability_fills_receiver_then_passes_excess():
    original = profile((0.45, 0.44))
    patch = original.patches[0]
    state = replace(original, patches=(replace(patch, compartments=(patch.compartments[0],
                    replace(patch.compartments[1], bottom_m=0.2))),))
    parameters = laws(state)
    parameters[("0", "1")] = DrainageParameters(0.1, 100, SOURCE)
    result = drain_soil_profile(state, parameters)
    assert [c.water_content for c in cells(result)] == [Fraction(39, 100), Fraction(435, 1000)]
    assert result["balance"].external_output_l == Fraction(65, 10)


def test_restrictive_bottom_returns_water_above_and_limits_all_interfaces():
    state = profile()
    parameters = laws(state)
    parameters[("0", "1")] = DrainageParameters(0.4, 1, SOURCE)
    result = drain_soil_profile(state, parameters)
    assert [cell.water_content for cell in cells(result)] == [Fraction(44, 100), Fraction(45, 100)]
    assert [row.actual_l for row in result["balance"].fluxes] == [1, 1]
    assert result["balance"].external_output_l == 1
    assert result["diagnostics"][1]["retained_by_conductivity_l"] == 59
    assert result["diagnostics"][1]["returned_above_l"] == 5


def test_impermeable_saturated_bottom_retains_all_water():
    state = profile()
    parameters = laws(state)
    parameters[("0", "1")] = DrainageParameters(0, 0, SOURCE)
    result = drain_soil_profile(state, parameters)
    assert result["balance"].ending.patches == state.patches
    assert result["balance"].external_output_l == result["balance"].internal_transfer_l == 0
    assert result["diagnostics"][1]["returned_above_l"] == 6


def test_slow_surface_does_not_restrict_water_already_in_lower_layer():
    state = profile()
    parameters = laws(state)
    parameters[("0", "0")] = DrainageParameters(0.4, 1, SOURCE)
    result = drain_soil_profile(state, parameters)
    assert [cell.water_content for cell in cells(result)] == [Fraction(44, 100), Fraction(39, 100)]
    assert [row.actual_l for row in result["balance"].fluxes] == [1, 55]
    assert result["balance"].external_output_l == 55


def test_patches_scale_volume_but_keep_local_water_contents_independent():
    state = profile(fractions=(Fraction(1, 3), Fraction(2, 3)), area=37)
    parameters = laws(state)
    parameters[("0", "1")] = DrainageParameters(0, 0, SOURCE)
    result = drain_soil_profile(state, parameters)
    assert [c.water_content for c in cells(result)] == [Fraction(45, 100)] * 2
    assert [c.water_content for c in cells(result, 1)] == [Fraction(39, 100)] * 2
    assert result["balance"].external_output_l == 60 * 37 * Fraction(2, 3)
    assert result["balance"].balance_residual_l == 0
    assert result["parameters"][1][2] == parameters[("0", "1")]


def test_daily_continuation_converges_to_field_capacity_without_clipping_water_away():
    initial = state = profile()
    parameters = laws(state)
    drainage = Fraction(0)
    for _ in range(120):
        result = drain_soil_profile(state, parameters)
        drainage += result["balance"].external_output_l
        state = result["balance"].ending
        assert all(c.water_content >= c.field_capacity for c in state.patches[0].compartments)
    assert initial.water_l - state.water_l == drainage
    assert float(state.water_l) == pytest.approx(300, abs=1e-10)


def test_seeded_heterogeneous_layers_preserve_water_and_conductivity_bounds():
    rng = Random(731)
    for _ in range(100):
        layers = []
        parameters = {}
        for index in range(8):
            fc = Fraction(rng.randrange(20, 36), 100)
            sat = fc + Fraction(rng.randrange(5, 21), 100)
            theta = Fraction(1, 10) + Fraction(rng.randrange(101), 100) * (sat - Fraction(1, 10))
            layers.append(SoilCompartment(str(index), Fraction(index, 10), Fraction(index+1, 10),
                                         fc, 0.15, sat, 0.1, theta, SOURCE))
            parameters[("p", str(index))] = DrainageParameters(Fraction(rng.randrange(11), 10), rng.randrange(40), SOURCE)
        state = SoilStorage(7, (SoilPatch("p", 1, tuple(layers)),), SOURCE)
        result = drain_soil_profile(state, parameters)
        balance = result["balance"]
        assert balance.ending.water_l + balance.external_output_l == state.water_l
        assert all(residual == 0 for _, _, residual in balance.compartment_residuals_l)
        for original, ending, flow in zip(layers, cells(result), balance.fluxes):
            assert min(original.water_content, original.field_capacity) <= ending.water_content <= ending.saturation
            assert flow.actual_l <= parameters[("p", original.id)].saturated_conductivity_mm_day * 7


def test_simultaneous_drainage_allows_saturated_throughflow_and_rejects_invalid_solution():
    state = profile()
    balance = apply_downward_drainage(state, {("0", "0"): 6, ("0", "1"): 60}, SOURCE)
    assert [c.water_content for c in balance.ending.patches[0].compartments] == [Fraction(39, 100)] * 2
    assert balance.application_mode == "simultaneous_downward_drainage"
    for interfaces in ({("0", "0"): 6, ("0", "1"): 0},  # would overfill saturated receiver
                       {("0", "0"): 16, ("0", "1"): 60},  # would drain top below FC
                       {("0", "0"): -1, ("0", "1"): 0},
                       {("0", "0"): 0}):
        with pytest.raises(ValueError):
            apply_downward_drainage(state, interfaces, SOURCE)
    assert state == profile()


@pytest.mark.parametrize("changes", [{"tau": -1}, {"tau": 1.01}, {"tau": True},
    {"tau": float("nan")}, {"saturated_conductivity_mm_day": -1},
    {"saturated_conductivity_mm_day": None}, {"source": ""}])
def test_invalid_parameters(changes):
    with pytest.raises(ValueError):
        replace(DrainageParameters(0.4, 100, SOURCE), **changes)


def test_missing_extra_or_unvalidated_parameters_are_rejected():
    state = profile()
    parameters = laws(state)
    for invalid in ({}, {**parameters, ("other", "0"): parameters[("0", "0")]},
                    {**parameters, ("0", "1"): {}}, None):
        with pytest.raises(ValueError):
            drain_soil_profile(state, invalid)


def test_saturation_equals_field_capacity_has_no_drainable_storage():
    cell = SoilCompartment("soil", 0, 1, 0.3, 0.2, 0.3, 0.1, 0.3, SOURCE)
    law = DrainageParameters(0.4, 100, SOURCE)
    assert drainage_response(cell, law) == 0
    assert water_content_for_response(cell, law, 0.001) is None


def test_volume_limits_cover_the_declared_area_and_depth_envelope():
    cell = SoilCompartment("soil", 0, 10, 0.2, 0.1, 0.9, 0.05, 0.9, SOURCE)
    state = SoilStorage(10**12, (SoilPatch("field", 1, (cell,)),), SOURCE)
    result = drain_soil_profile(state, {("field", "soil"): DrainageParameters(1, 10**6, SOURCE)})
    assert result["balance"].external_output_l == 7 * 10**15
    assert result["balance"].ending.water_l == 2 * 10**15
    assert result["balance"].balance_residual_l == 0
