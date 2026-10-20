"""Shared-storage evaporation: stages, ponding, dry bounds and continuation."""

from dataclasses import replace
from datetime import date
from fractions import Fraction
from math import exp
from random import Random

import pytest

from agro_model.soil_evaporation import (
    PatchEvaporationCoefficients,
    SoilEvaporationMemory,
    SoilEvaporationParameters,
    evaporate_soil_profile,
    evaporation_reduction,
)
from agro_model.soil_infiltration import SurfaceWaterState
from agro_model.soil_storage import (
    SoilCompartment,
    SoilFlux,
    SoilPatch,
    SoilStorage,
    apply_soil_fluxes,
)
from agro_model.water import ReferenceETDay

SOURCE = "Synthetic evaporation case; not crop or site calibration"


def setup(*, theta=0.3, area=1, fractions=(1,), et0=5, pond=0, credit=9):
    cells = tuple(SoilCompartment(str(i), top, bottom, 0.3, 0.2, 0.45, 0.1, theta, SOURCE)
                  for i, (top, bottom) in enumerate(((0, 0.1), (0.1, 0.3), (0.3, 1))))
    state = SoilStorage(area, tuple(SoilPatch(str(i), f, cells) for i, f in enumerate(fractions)), SOURCE)
    surface = {p.id: SurfaceWaterState(pond, 10, SOURCE) for p in state.patches}
    parameters = {p.id: SoilEvaporationParameters(0.15, 0.3, 9, 4, SOURCE) for p in state.patches}
    memories = {p.id: SoilEvaporationMemory(credit, 0.15, None if credit else 0, SOURCE) for p in state.patches}
    weather = ReferenceETDay(date(2026, 7, 6), et0, 0, SOURCE, "scenario", "UTC", 24, SOURCE)
    coefficients = {p.id: PatchEvaporationCoefficients(1, 1, SOURCE) for p in state.patches}
    recharge = {p.id: 0 for p in state.patches}
    return state, surface, parameters, memories, weather, coefficients, recharge


def run(values):
    return evaporate_soil_profile(*values[:-1], infiltrated_mm=values[-1])


def test_stage_one_uses_shared_water_and_reduces_the_drying_counter():
    inputs = setup()
    result = run(inputs)
    assert result["totals"]["soil_evaporation_l"] == 5
    assert result["soil_balance"].ending.water_l == 295
    assert dict(result["ending_memories"])["0"].stage_one_remaining_mm == 4
    assert result["stage_two_trace"] == ()
    assert result["soil_balance"].ending.patches[0].compartments[0].water_content == Fraction(1, 4)
    assert inputs[0].water_l == 300
    assert inputs[3]["0"].stage_one_remaining_mm == 9


def test_exponential_reduction_matches_independent_half_wet_identity():
    assert evaporation_reduction(0, 4) == 0
    assert evaporation_reduction(1, 4) == 1
    assert float(evaporation_reduction(Fraction(1, 2), 4)) == pytest.approx(1 / (exp(2) + 1), rel=1e-14)
    assert evaporation_reduction(Fraction(1, 2), Fraction(1, 10**400)) == Fraction(1, 2)


def test_stage_transition_matches_independent_twenty_step_reference():
    inputs = setup(et0=12)
    result = run(inputs)
    # Initial minimum-depth storage=45 mm. Stage one removes 9, so stage two
    # starts at 36 mm; air dry is 15 mm. Integrate the remaining 3 mm demand.
    water, expected = 36.0, 0.0
    for _ in range(20):
        relative = (water - 15) / 21
        loss = (exp(4 * relative) - 1) / (exp(4) - 1) * 3 / 20
        expected += loss
        water -= loss
    # The initial withdrawals all fit inside the top 0.1 m, so no partial-cell
    # mixing or expanding evaporation depth enters this independent reference.
    assert result["totals"]["stage_one_evaporation_l"] == 9
    assert float(result["totals"]["stage_two_evaporation_l"]) == pytest.approx(expected, abs=1e-12)
    trace = result["stage_two_trace"]
    assert len(trace) == 20 and trace[0]["relative_water"] == 1
    assert trace[0]["requested_mm"] == Fraction(3, 20)
    assert dict(result["ending_memories"])["0"].stage_two_start_fraction == 0


def test_soil_evaporation_remains_positive_at_root_wilting_without_root_extraction():
    inputs = setup(theta=0.2, credit=0)
    result = run(inputs)
    assert result["totals"]["soil_evaporation_l"] > 0
    assert result["soil_balance"].ending.water_l < 200
    roots = apply_soil_fluxes(result["soil_balance"].ending, [
        SoilFlux("root_uptake", "0", c.id, None, 100, SOURCE) for c in inputs[0].patches[0].compartments
    ])
    assert roots.external_output_l == 0
    assert roots.ending.water_l == result["soil_balance"].ending.water_l
    assert result["totals"]["combined_balance_residual_l"] == 0


def test_wetting_counter_never_adds_water_to_an_air_dry_profile():
    inputs = list(setup(theta=0.1, et0=10))
    inputs[-1]["0"] = 100
    result = run(inputs)
    assert result["soil_balance"].ending.water_l == inputs[0].water_l == 100
    assert result["totals"]["soil_evaporation_l"] == 0
    assert result["totals"]["stage_one_storage_shortfall_l"] == 9
    assert dict(result["ending_memories"])["0"].stage_one_remaining_mm == 0


def test_small_wetting_replenishes_counter_without_changing_storage_when_et_is_zero():
    inputs = list(setup(et0=0, credit=2))
    inputs[-1]["0"] = 1
    result = run(inputs)
    assert dict(result["ending_memories"])["0"].stage_one_remaining_mm == 3
    assert result["soil_balance"].ending.patches == inputs[0].patches
    assert result["totals"]["total_evaporation_l"] == 0


def test_pond_evaporation_leaves_only_the_remaining_day_for_soil_evaporation():
    inputs = list(setup(pond=2))
    inputs[5]["0"] = PatchEvaporationCoefficients(1, 2, SOURCE)
    result = run(inputs)
    assert result["totals"]["ponded_evaporation_l"] == 2
    assert result["patches"][0]["soil_time_fraction"] == Fraction(4, 5)
    assert result["totals"]["soil_evaporation_l"] == 4
    assert result["totals"]["total_evaporation_l"] == 6
    assert dict(result["ending_surface"])["0"].stored_mm == 0
    assert dict(result["ending_memories"])["0"].stage_one_remaining_mm == 5


@pytest.mark.parametrize("pond_coefficient,expected_evaporation", [(1, 5), (0, 0)])
def test_water_remaining_on_surface_prevents_double_soil_evaporation(pond_coefficient, expected_evaporation):
    inputs = list(setup(pond=10))
    inputs[5]["0"] = PatchEvaporationCoefficients(1, pond_coefficient, SOURCE)
    result = run(inputs)
    assert result["totals"]["total_evaporation_l"] == expected_evaporation
    assert result["totals"]["soil_evaporation_l"] == 0
    assert result["soil_balance"].ending.patches == inputs[0].patches


def test_active_depth_expands_into_wetter_soil_but_never_beyond_the_maximum_boundary():
    inputs = list(setup(theta=0.3, credit=0))
    p = inputs[0].patches[0]
    cells = (replace(p.compartments[0], bottom_m=0.15, water_content=0.1),
             replace(p.compartments[1], top_m=0.15), p.compartments[2])
    inputs[0] = replace(inputs[0], patches=(replace(p, compartments=cells),))
    result = run(inputs)
    memory = dict(result["ending_memories"])["0"]
    assert Fraction(15, 100) < memory.active_depth_m <= Fraction(3, 10)
    assert result["totals"]["soil_evaporation_l"] > 0
    assert result["soil_balance"].ending.patches[0].compartments[2] == cells[2]


def test_partial_depth_extraction_changes_cell_average_and_conserves_whole_profile():
    inputs = list(setup(et0=25, credit=25))
    inputs[2]["0"] = SoilEvaporationParameters(0.15, 0.3, 25, 4, SOURCE)
    result = run(inputs)
    cells = result["soil_balance"].ending.patches[0].compartments
    assert cells[0].water_content == Fraction(1, 10)
    assert cells[1].water_content == Fraction(275, 1000)
    assert cells[2].water_content == Fraction(3, 10)
    assert result["totals"]["soil_evaporation_l"] == 25
    assert result["soil_balance"].ending.water_l == 275


def test_long_drydown_and_saved_memory_continuation_are_exact_and_depth_bounded():
    initial = setup()
    def replay(values, days):
        data = list(values)
        total = Fraction(0)
        for _ in range(days):
            result = run(data)
            total += result["totals"]["total_evaporation_l"]
            data[0] = result["soil_balance"].ending
            data[1] = dict(result["ending_surface"])
            data[3] = dict(result["ending_memories"])
        return data, total
    full, total = replay(initial, 30)
    first, a = replay(initial, 13)
    continued, b = replay(first, 17)
    assert full == continued and total == a+b
    assert initial[0].water_l - full[0].water_l == total
    assert 0 < total <= 60  # .3 m * (FC .3 - air dry .1) * 1000
    assert full[0].patches[0].compartments[2] == initial[0].patches[0].compartments[2]


def test_patch_area_scales_extraction_without_a_second_wetting_fraction_multiplier():
    inputs = list(setup(area=100, fractions=(0.25, 0.75)))
    inputs[5]["1"] = PatchEvaporationCoefficients(0, 0, SOURCE)
    result = run(inputs)
    assert result["totals"]["soil_evaporation_l"] == 125
    assert result["soil_balance"].ending.patches[1].compartments == inputs[0].patches[1].compartments


def test_seeded_states_preserve_soil_and_surface_water_without_crossing_dry_floors():
    rng = Random(739)
    for _ in range(40):
        inputs = list(setup(theta=Fraction(rng.randrange(10, 46), 100), area=17,
                            fractions=(0.3, 0.7), et0=rng.randrange(13), pond=rng.randrange(6), credit=rng.choice((0, 9))))
        for key in inputs[5]:
            inputs[5][key] = PatchEvaporationCoefficients(Fraction(rng.randrange(12), 10), 1.1, SOURCE)
            inputs[-1][key] = rng.randrange(51)
        result = run(inputs)
        original_surface = sum(inputs[0].area_m2*p.area_fraction*inputs[1][p.id].stored_mm for p in inputs[0].patches)
        final_surface = sum(inputs[0].area_m2*p.area_fraction*dict(result["ending_surface"])[p.id].stored_mm for p in inputs[0].patches)
        assert inputs[0].water_l + original_surface == result["soil_balance"].ending.water_l + final_surface + result["totals"]["total_evaporation_l"]
        for patch in result["soil_balance"].ending.patches:
            for cell in patch.compartments:
                assert cell.water_content >= cell.evaporation_floor
        assert result["totals"]["soil_potential_not_evaporated_l"] >= 0


@pytest.mark.parametrize("field,value", [("minimum_depth_m", 0), ("maximum_depth_m", 0.6),
    ("readily_evaporable_mm", -1), ("decline_factor", 0), ("decline_factor", True), ("source", "")])
def test_invalid_parameters(field, value):
    with pytest.raises(ValueError):
        replace(SoilEvaporationParameters(0.15, 0.3, 9, 4, SOURCE), **{field: value})


def test_inconsistent_memory_geometry_and_dry_floor_are_rejected():
    for change in ("missing_reference", "extra_reference", "depth", "credit", "grid", "floor", "rew"):
        inputs = list(setup())
        with pytest.raises(ValueError):
            if change == "missing_reference":
                inputs[3]["0"] = SoilEvaporationMemory(0, 0.15, None, SOURCE)
            elif change == "extra_reference":
                inputs[3]["0"] = SoilEvaporationMemory(9, 0.15, 0, SOURCE)
            elif change == "depth":
                inputs[3]["0"] = SoilEvaporationMemory(9, 0.2, None, SOURCE)
            elif change == "credit":
                inputs[3]["0"] = SoilEvaporationMemory(10, 0.15, None, SOURCE)
            elif change == "grid":
                inputs[2]["0"] = SoilEvaporationParameters(0.15, 0.25, 9, 4, SOURCE)
            elif change == "floor":
                p = inputs[0].patches[0]
                inputs[0] = replace(inputs[0], patches=(replace(p, compartments=(replace(p.compartments[0], evaporation_floor=0.09), *p.compartments[1:])),))
            else:
                inputs[2]["0"] = SoilEvaporationParameters(0.15, 0.3, 30, 4, SOURCE)
            run(inputs)
