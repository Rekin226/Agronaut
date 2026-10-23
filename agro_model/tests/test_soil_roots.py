"""Demand, sink capacity and physical soil limits are independently observable."""

from dataclasses import replace
from datetime import date
from fractions import Fraction
from math import exp
from random import Random

import pytest

from agro_model.soil_roots import (
    UPTAKE_DEPTH_QUANTUM_MM,
    CropTranspirationCoefficients,
    RootLayerActivity,
    RootUptakeParameters,
    stomatal_stress,
    transpire_soil_profile,
)
from agro_model.soil_storage import SoilCompartment, SoilPatch, SoilStorage
from agro_model.water import ReferenceETDay

SOURCE = "Synthetic root extraction case; no crop or site calibration"


def setup(theta=.3, *, area=1, fractions=(1,)):
    cells = tuple(SoilCompartment(str(i), Fraction(i, 4), Fraction(i+1, 4),
                  .3, .2, .45, .1, theta, SOURCE) for i in range(4))
    state = SoilStorage(area, tuple(SoilPatch(str(i), f, cells) for i, f in enumerate(fractions)), SOURCE)
    parameters = {p.id: RootUptakeParameters(1, .25, .5, 0, 1, SOURCE) for p in state.patches}
    # 30 mm/day capacity with a supplied 40/30/20/10 percent distribution.
    activities = {(p.id, c.id): RootLayerActivity(Fraction(12*(4-i), 1000), 1, SOURCE)
                  for p in state.patches for i, c in enumerate(p.compartments)}
    weather = ReferenceETDay(date(2026, 7, 6), 5, 0, SOURCE, "scenario", "UTC", 24, SOURCE)
    coefficients = {p.id: CropTranspirationCoefficients(.8, 1, SOURCE) for p in state.patches}
    return [state, parameters, activities, weather, coefficients]


def test_wet_root_zone_matches_supplied_extraction_pattern_and_preserves_inputs():
    inputs = setup()
    result = transpire_soil_profile(*inputs)
    assert result["totals"]["root_capacity_l"] == 30
    assert result["totals"]["potential_transpiration_l"] == 4
    assert result["totals"]["actual_transpiration_l"] == 4
    assert [r["actual_l"] for r in result["layers"]] == [Fraction(8, 5), Fraction(6, 5), Fraction(4, 5), Fraction(2, 5)]
    assert result["balance"].ending.water_l == 296
    assert inputs[0].water_l == 300
    assert result["totals"]["balance_residual_l"] == 0


def test_sufficient_roots_supply_stressed_demand_without_multiplying_stress_twice():
    inputs = setup(.225)
    result = transpire_soil_profile(*inputs)
    assert result["patches"][0]["drought_factor"] == Fraction(1, 2)
    assert result["totals"]["root_capacity_l"] == 15
    assert result["totals"]["soil_stress_limited_demand_l"] == 2
    assert result["totals"]["actual_transpiration_l"] == 2  # Not Ks² * 4 = 1.
    assert result["totals"]["soil_stress_reduction_l"] == 2


def test_local_curvature_changes_capacity_without_changing_linear_profile_demand():
    inputs = setup(.225)
    inputs[1]["0"] = replace(inputs[1]["0"], local_shape_factor=4)
    result = transpire_soil_profile(*inputs)
    assert float(result["layers"][0]["drought_factor"]) == pytest.approx(1-1/(exp(2)+1))
    assert result["totals"]["root_capacity_l"] > 15
    assert result["totals"]["actual_transpiration_l"] == 2


def test_wet_topsoil_can_relieve_demand_stress_but_only_active_wet_roots_supply_water():
    inputs = setup(.2)
    p = inputs[0].patches[0]
    inputs[0] = replace(inputs[0], patches=(replace(p, compartments=(replace(p.compartments[0], water_content=.3), *p.compartments[1:])),))
    result = transpire_soil_profile(*inputs)
    assert result["patches"][0]["selected_stress_volume"] == "topsoil"
    assert result["patches"][0]["root_zone_status"]["relative_depletion"] == Fraction(3, 4)
    assert [r["actual_l"] for r in result["layers"]] == [4, 0, 0, 0]
    inputs[1]["0"] = replace(inputs[1]["0"], topsoil_depth_m=1)
    root_only = transpire_soil_profile(*inputs)
    assert root_only["totals"]["actual_transpiration_l"] == 2


def test_heterogeneous_soils_use_integrated_capacity_for_relative_depletion():
    inputs = setup(.3)
    p = inputs[0].patches[0]
    cells = (replace(p.compartments[0], field_capacity=.4, wilting_point=.1, water_content=.25), *p.compartments[1:])
    inputs[0] = replace(inputs[0], patches=(replace(p, compartments=cells),))
    result = transpire_soil_profile(*inputs)
    status = result["patches"][0]["root_zone_status"]
    assert status["total_available_mm"] == 150
    assert status["depletion_mm"] == Fraction(75, 2)
    assert status["relative_depletion"] == Fraction(1, 4)


def test_cold_and_aeration_are_explicit_limits_without_scaling_actual_uptake_twice():
    inputs = setup()
    inputs[1]["0"] = replace(inputs[1]["0"], root_zone_aeration_factor=.4)
    inputs[4]["0"] = replace(inputs[4]["0"], cold_stress_factor=.5)
    for key, activity in inputs[2].items():
        inputs[2][key] = replace(activity, aeration_factor=.8)
    result = transpire_soil_profile(*inputs)
    assert result["totals"]["cold_limited_demand_l"] == 2
    assert result["totals"]["soil_stress_limited_demand_l"] == Fraction(4, 5)
    assert result["totals"]["actual_transpiration_l"] == Fraction(4, 5)
    assert result["totals"]["root_capacity_l"] == 24


def test_local_aeration_can_prevent_extraction_from_a_wet_compartment():
    inputs = setup()
    inputs[2][("0", "0")] = replace(inputs[2][("0", "0")], aeration_factor=0)
    result = transpire_soil_profile(*inputs)
    assert result["layers"][0]["actual_l"] == 0
    assert result["totals"]["actual_transpiration_l"] == 4


@pytest.mark.parametrize("theta", [.1, .2])
def test_at_or_below_wilting_roots_leave_all_existing_water_untouched(theta):
    inputs = setup(theta)
    result = transpire_soil_profile(*inputs)
    assert result["totals"]["actual_transpiration_l"] == 0
    assert result["balance"].ending.patches == inputs[0].patches


@pytest.mark.parametrize("rate,expected", [(0, 0), (.001, 1)])
def test_limited_root_volume_reports_capacity_shortfall_even_in_wet_soil(rate, expected):
    inputs = setup()
    inputs[2] = {k: replace(v, maximum_rate_per_day=rate) for k, v in inputs[2].items()}
    result = transpire_soil_profile(*inputs)
    assert result["totals"]["actual_transpiration_l"] == expected
    assert result["totals"]["capacity_shortfall_l"] == 4-expected
    assert result["totals"]["storage_shortfall_l"] == 0


def test_finite_water_limit_is_separate_from_root_capacity_and_never_crosses_wilting():
    inputs = setup(.201)
    inputs[1]["0"] = replace(inputs[1]["0"], stomatal_threshold=.999)
    result = transpire_soil_profile(*inputs)
    assert result["totals"]["allocated_uptake_l"] == 4
    assert result["totals"]["actual_transpiration_l"] == 1
    assert result["totals"]["storage_shortfall_l"] == 3
    assert result["totals"]["capacity_shortfall_l"] == 0
    assert all(c.water_content == c.wilting_point for c in result["balance"].ending.patches[0].compartments)


def test_root_boundary_prevents_drawing_from_deeper_water_during_repeated_extraction():
    inputs = setup()
    inputs[1]["0"] = replace(inputs[1]["0"], root_depth_m=.5)
    for key in (("0", "2"), ("0", "3")):
        inputs[2][key] = replace(inputs[2][key], maximum_rate_per_day=0)
    initial = inputs[0]
    total = Fraction(0)
    for _ in range(100):
        result = transpire_soil_profile(*inputs)
        total += result["totals"]["actual_transpiration_l"]
        inputs[0] = result["balance"].ending
    assert total <= 50
    assert initial.water_l-inputs[0].water_l == total
    assert inputs[0].patches[0].compartments[2:] == initial.patches[0].compartments[2:]
    assert max(c.water_content.denominator.bit_length() for c in inputs[0].patches[0].compartments) < 128


def test_subquantum_capacity_is_reported_as_numerical_shortfall_without_inventing_water():
    inputs = setup()
    inputs[2] = {k: replace(v, maximum_rate_per_day=Fraction(1, 10**16)) for k, v in inputs[2].items()}
    result = transpire_soil_profile(*inputs)
    tiny = Fraction(1, 10**13)
    assert result["totals"]["root_capacity_l"] == tiny
    assert result["totals"]["capacity_shortfall_l"] == 4-tiny
    assert result["totals"]["precision_shortfall_l"] == tiny
    assert result["totals"]["storage_shortfall_l"] == 0
    assert result["totals"]["actual_transpiration_l"] == 0
    assert result["balance"].ending.patches == inputs[0].patches


@pytest.mark.parametrize("zero", ["weather", "coefficient", "cold"])
def test_no_demand_causes_no_extraction(zero):
    inputs = setup()
    if zero == "weather":
        inputs[3] = replace(inputs[3], et0_mm_day=0)
    elif zero == "coefficient":
        inputs[4]["0"] = replace(inputs[4]["0"], transpiration_coefficient=0)
    else:
        inputs[4]["0"] = replace(inputs[4]["0"], cold_stress_factor=0)
    result = transpire_soil_profile(*inputs)
    assert result["balance"].ending.patches == inputs[0].patches
    assert result["totals"]["actual_transpiration_l"] == 0


def test_patch_area_scales_uptake_and_does_not_transfer_demand_to_another_patch():
    inputs = setup(area=100, fractions=(.25, .75))
    inputs[4]["1"] = replace(inputs[4]["1"], transpiration_coefficient=0)
    result = transpire_soil_profile(*inputs)
    assert result["totals"]["actual_transpiration_l"] == 100
    assert result["balance"].ending.patches[1] == inputs[0].patches[1]


def test_stress_curve_exact_boundaries_and_continuous_linear_limit():
    assert stomatal_stress(0, .5, 4) == 1
    assert stomatal_stress(.5, .5, 4) == 1
    assert stomatal_stress(1, .5, 4) == 0
    assert stomatal_stress(.75, .5, 0) == Fraction(1, 2)
    assert stomatal_stress(.75, .5, Fraction(1, 10**400)) == Fraction(1, 2)
    assert stomatal_stress(.5, 0, 0) == Fraction(1, 2)


def test_seeded_profiles_close_storage_and_demand_loss_budgets():
    rng = Random(7310)
    for _ in range(80):
        inputs = setup(Fraction(rng.randrange(10, 46), 100), area=17, fractions=(.3, .7))
        for key in inputs[1]:
            inputs[1][key] = replace(inputs[1][key], stomatal_threshold=Fraction(rng.randrange(10), 10),
                                     local_shape_factor=rng.randrange(10), root_zone_aeration_factor=Fraction(rng.randrange(11), 10))
        for key, value in inputs[2].items():
            inputs[2][key] = replace(value, maximum_rate_per_day=Fraction(rng.randrange(101), 1000),
                                     aeration_factor=Fraction(rng.randrange(11), 10))
        result = transpire_soil_profile(*inputs)
        t = result["totals"]
        assert t["actual_transpiration_l"] == inputs[0].water_l-result["balance"].ending.water_l
        assert t["potential_transpiration_l"] == sum(t[k] for k in ("actual_transpiration_l", "cold_reduction_l",
            "soil_stress_reduction_l", "capacity_shortfall_l", "precision_shortfall_l", "storage_shortfall_l"))
        assert all(v >= 0 for v in t.values())
        assert t["precision_shortfall_l"] < 8 * UPTAKE_DEPTH_QUANTUM_MM * inputs[0].area_m2
        for row in result["layers"]:
            area = inputs[0].area_m2 * next(p.area_fraction for p in inputs[0].patches if p.id == row["patch_id"])
            assert abs(row["ideal_requested_l"]-row["requested_l"]) < area*UPTAKE_DEPTH_QUANTUM_MM
            assert row["requested_l"] <= row["root_capacity_l"]
        for original, ending in zip(inputs[0].patches, result["balance"].ending.patches):
            for a, b in zip(original.compartments, ending.compartments):
                assert min(a.water_content, a.wilting_point) <= b.water_content <= a.water_content


@pytest.mark.parametrize("name,value", [("root_depth_m", 0), ("topsoil_depth_m", 2),
    ("stomatal_threshold", 1), ("stomatal_threshold", True), ("local_shape_factor", -1),
    ("root_zone_aeration_factor", None), ("source", "")])
def test_invalid_root_parameters(name, value):
    with pytest.raises(ValueError):
        replace(setup()[1]["0"], **{name: value})


@pytest.mark.parametrize("case", ["missing_patch", "extra_patch", "missing_activity", "extra_activity", "untyped_activity", "boundary", "deep_activity"])
def test_inconsistent_geometry_and_incomplete_configuration_are_rejected(case):
    inputs = setup()
    if case == "missing_patch":
        inputs[1] = {}
    elif case == "extra_patch":
        inputs[4]["extra"] = inputs[4]["0"]
    elif case == "missing_activity":
        del inputs[2][("0", "0")]
    elif case == "extra_activity":
        inputs[2][("0", "extra")] = inputs[2][("0", "0")]
    elif case == "untyped_activity":
        inputs[2][("0", "0")] = {}
    elif case == "boundary":
        inputs[1]["0"] = replace(inputs[1]["0"], root_depth_m=.8)
    else:
        inputs[1]["0"] = replace(inputs[1]["0"], root_depth_m=.5)
    with pytest.raises(ValueError):
        transpire_soil_profile(*inputs)
