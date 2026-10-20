"""Hydraulic conservation, operating constraints, and conditional runtime bounds."""

import json
from copy import deepcopy
from math import pi
from pathlib import Path

import pytest

from agro_model.hydraulics import GRAVITY_M_S2
from agronaut_agent.irrigation_io import evaluate_drip_request


def sample():
    return json.loads((Path(__file__).resolve().parents[2]/"examples/drip_design.json").read_text())


def test_volume_bounds_zone_runtime_and_material_quantities():
    out = evaluate_drip_request(sample())
    assert out["feasible"] is True
    assert out["area_m2"] == 500
    assert out["requested_water_l"] == 1000
    assert out["candidate_daily_upper_water_l"] == pytest.approx(1000*1.05/0.95 + 50)
    assert out["candidate_daily_hours"] == pytest.approx(2*500/950 + 0.25)
    assert sum(z["emitter_count"] for z in out["zones"]) == 1000
    assert out["materials"]["lateral_pipe_m"] == 500
    assert out["materials"]["main_pipe_m"] == 30
    assert out["materials"]["manifold_pipe_m"] == 10
    for z in out["zones"]:
        assert z["flow_min_lph"]*z["candidate_runtime_hours"] == pytest.approx(z["requested_water_l"])
        assert z["flow_max_lph"]*z["candidate_runtime_hours"] == pytest.approx(z["candidate_upper_water_l"])
    # Further zones have more trunk loss; this is not copied from the first zone.
    assert out["zones"][1]["emitter_min_head_m"] < out["zones"][0]["emitter_min_head_m"]
    json.dumps(out, allow_nan=False)


def test_exact_small_laminar_network_against_hand_integrated_flows():
    data = sample()
    data["layout"].update(row_count=2, emitters_per_row=2, rows_per_zone=2,
                          row_spacing_m=1, emitter_spacing_m=1, feed_length_m=5)
    data["emitter"].update(nominal_flow_lph=1, flow_tolerance_fraction=0, insertion_k=0)
    for key in ("main", "manifold", "lateral"):
        data[key].update(internal_diameter_mm=100, roughness_mm=0)
    data["headworks"].update(min_filter_loss_m=0, max_filter_loss_m=0, main_fittings_k=0,
                             manifold_through_k=0, lateral_takeoff_k=0)
    data["supply"].update(min_running_head_m=20, max_running_head_m=20)
    out = evaluate_drip_request(data)
    assert out["feasible"]
    # Main 5m*4L/h=20; header .5m*4 + 1m*2=4; lateral .5m*2 + 1m*1=2.
    # The closest emitter path is 20 + 2 + 1 = 23 (m L/h).
    head_per_m_lph = 128*1e-6/(3_600_000*pi*GRAVITY_M_S2*0.1**4)
    z = out["zones"][0]
    assert z["emitter_min_head_m"] == pytest.approx(20-26*head_per_m_lph, abs=1e-12)
    assert z["emitter_max_head_m"] == pytest.approx(20-23*head_per_m_lph, abs=1e-12)
    assert z["high_flow_path_losses"]["last_loss_m"] == pytest.approx(26*head_per_m_lph)


@pytest.mark.parametrize("record,key,value,constraint", [
    ("supply", "max_flow_lph", 100, "source_flow_lph"),
    ("supply", "daily_available_l", 100, "daily_source_water_l"),
    ("supply", "available_hours", 0.5, "daily_runtime_hours"),
    ("supply", "min_running_head_m", 10, "emitter_min_head_m"),
    ("emitter", "max_head_m", 19, "emitter_max_head_m"),
    ("main", "max_head_m", 24, "main_static_head_m"),
    ("headworks", "filter_microns", 130, "filter_microns"),
    ("headworks", "filter_max_flow_lph", 100, "filter_flow_lph"),
    ("limits", "max_velocity_m_s", 0.01, "velocity_m_s"),
    ("application", "max_local_depth_mm", 5, "local_depth_mm"),
    ("application", "max_local_rate_mm_h", 10, "local_rate_mm_h"),
])
def test_each_binding_constraint_suppresses_schedule(record, key, value, constraint):
    data = sample()
    data[record][key] = value
    out = evaluate_drip_request(data)
    assert not out["feasible"]
    assert out["schedule"] is None
    assert constraint in out["binding_constraints"]


def test_daily_volume_does_not_replace_instantaneous_capacity():
    data = sample()
    data["supply"]["daily_available_l"] = 1e8
    data["supply"]["max_flow_lph"] = 500
    out = evaluate_drip_request(data)
    assert "source_flow_lph" in out["binding_constraints"]
    assert "daily_source_water_l" not in out["binding_constraints"]
    data["layout"]["rows_per_zone"] = 2
    assert evaluate_drip_request(data)["feasible"]


def test_partial_last_zone_and_wetted_fraction_do_not_reduce_crop_target():
    data = sample()
    data["layout"]["row_count"] = 11
    out = evaluate_drip_request(data)
    assert [z["row_count"] for z in out["zones"]] == [5, 5, 1]
    assert sum(z["area_m2"] for z in out["zones"]) == out["area_m2"]
    assert out["materials"]["emitters"] == 1100
    before = deepcopy(out)
    data["application"]["wetted_fraction"] = 0.2
    out = evaluate_drip_request(data)
    assert out["requested_water_l"] == before["requested_water_l"]
    assert out["zones"][0]["candidate_runtime_hours"] == before["zones"][0]["candidate_runtime_hours"]
    assert out["zones"][0]["candidate_local_depth_mm"] == 2*before["zones"][0]["candidate_local_depth_mm"]
    assert "local_depth_mm" in out["binding_constraints"]


@pytest.mark.parametrize("record,key,value", [
    ("layout", "row_count", True), ("layout", "rows_per_zone", 0),
    ("layout", "row_count", 10000000), ("layout", "terrain", "sloped"),
    ("layout", "row_spacing_m", float("inf")),
    ("emitter", "pressure_compensating", False), ("emitter", "pressure_compensating", 1),
    ("emitter", "flow_tolerance_fraction", 1), ("emitter", "source", ""),
    ("application", "wetted_fraction", 0), ("application", "wetted_fraction", 1e-320),
    ("limits", "viscosity_m2_s", float("nan")), ("main", "internal_diameter_mm", -1),
])
def test_unsupported_geometry_and_invalid_physical_inputs_rejected(record, key, value):
    data = sample()
    data[record][key] = value
    with pytest.raises(ValueError):
        evaluate_drip_request(data)


def test_unknown_or_missing_inputs_cannot_silently_use_defaults():
    for change in (lambda d: d.update(schema_version=True),
                   lambda d: d["layout"].update(elevation_m=10),
                   lambda d: d["headworks"].pop("max_filter_loss_m"),
                   lambda d: d.pop("supply")):
        data = sample()
        change(data)
        with pytest.raises(ValueError):
            evaluate_drip_request(data)
