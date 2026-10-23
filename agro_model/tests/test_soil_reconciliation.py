"""Exact adjustment accounting, immutable geometry and explicit drying history."""

from dataclasses import replace
from fractions import Fraction

import pytest

from agro_model.soil_reconciliation import CompartmentReading, reconcile_soil_water
from agro_model.soil_snapshots import dumps_soil_snapshot, loads_soil_snapshot
from agro_model.tests.test_soil_replay import setup


def test_adjustments_use_layer_volume_and_patch_fraction_without_changing_flux_stores():
    initial = setup()
    a, b = initial.soil.patches
    readings = [CompartmentReading(a.id, a.compartments[0].id, .3, "Synthetic wet sample"),
                CompartmentReading(b.id, b.compartments[-1].id, .2, "Synthetic dry sample")]
    result = reconcile_soil_water(initial, readings, initial.evaporation_memories, "Synthetic review")
    assert result["changes"][0]["storage_adjustment_l"] == Fraction(25, 2)
    assert result["changes"][1]["storage_adjustment_l"] == Fraction(-375, 2)
    assert result["storage_adjustment_l"] == -175 and result["adjustment_residual_l"] == 0
    ending = result["ending"]
    assert ending.surface == initial.surface and ending.surface_water_l == initial.surface_water_l
    assert ending.drainage_parameters == initial.drainage_parameters
    assert ending.evaporation_parameters == initial.evaporation_parameters
    assert ending.soil.patches[0].compartments[1:] == initial.soil.patches[0].compartments[1:]
    assert ending.as_of == initial.as_of and initial == setup()
    assert loads_soil_snapshot(dumps_soil_snapshot(ending), expected_kind="state") == ending


@pytest.mark.parametrize("value", [.09, .46, None, True, float("nan")])
def test_invalid_or_out_of_soil_bounds_readings_never_clip(value):
    initial = setup()
    with pytest.raises(ValueError):
        reading = CompartmentReading("a", initial.soil.patches[0].compartments[0].id, value, "Synthetic")
        reconcile_soil_water(initial, [reading], (initial.evaporation_memories[0],), "Synthetic")
    assert initial == setup()


@pytest.mark.parametrize("case", ["missing", "extra", "duplicate_memory", "invalid_memory", "duplicate_cell", "unknown_cell"])
def test_explicit_affected_patch_memory_and_unique_targets_are_required(case):
    initial = setup()
    reading = CompartmentReading("a", initial.soil.patches[0].compartments[0].id, .3, "Synthetic")
    readings, memories = [reading], (initial.evaporation_memories[0],)
    if case == "missing":
        memories = ()
    elif case == "extra":
        memories = initial.evaporation_memories
    elif case == "duplicate_memory":
        memories *= 2
    elif case == "invalid_memory":
        memories = (("a", replace(memories[0][1], stage_one_remaining_mm=10)),)
    elif case == "duplicate_cell":
        readings *= 2
    else:
        readings = [replace(reading, layer_id="absent")]
    with pytest.raises(ValueError):
        reconcile_soil_water(initial, readings, memories, "Synthetic")


def test_reviewed_memory_is_replaced_only_for_selected_patch():
    initial = setup()
    memory = replace(initial.evaporation_memories[0][1], stage_one_remaining_mm=0, active_depth_m=.3,
                     stage_two_start_fraction=Fraction(1, 3), source="Synthetic stage-two review")
    ending = reconcile_soil_water(initial, [CompartmentReading("a", initial.soil.patches[0].compartments[0].id, .2, "Synthetic")],
                                  (("a", memory),), "Synthetic")["ending"]
    assert dict(ending.evaporation_memories)["a"] == memory
    assert dict(ending.evaporation_memories)["b"] == dict(initial.evaporation_memories)["b"]
