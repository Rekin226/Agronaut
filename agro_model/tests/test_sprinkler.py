"""Independent nozzle/network arithmetic, conservation and sprinkler design constraints."""

import json
from math import pi, sqrt
from pathlib import Path

import pytest

from agro_model.hydraulics import GRAVITY_M_S2
from agronaut_agent.irrigation_io import evaluate_sprinkler_request


def sample():
    return json.loads((Path(__file__).resolve().parents[2]/"examples/sprinkler_design.json").read_text())


def test_flow_bounds_conservation_partial_zone_runtime_and_materials():
    out = evaluate_sprinkler_request(sample())
    assert out["feasible"]
    assert out["area_m2"] == 1728
    assert out["requested_net_water_l"] == 8640
    assert out["requested_gross_water_l"] == 11520
    q_low = 1000 * sqrt(20/30) * 0.97
    q_high = 1000 * sqrt(40/30) * 1.03
    assert [z["lateral_count"] for z in out["zones"]] == [2, 1]
    assert out["candidate_daily_upper_water_l"] == pytest.approx(11520*q_high/q_low+100)
    assert out["candidate_daily_hours"] == pytest.approx(2*(5/0.75*144/q_low)+0.1)
    assert out["materials"]["sprinklers"] == 12
    assert out["materials"]["riser_pipe_m"] == 12
    assert out["materials"]["lateral_pipe_m"] == 144
    for zone in out["zones"]:
        assert zone["flow_min_lph"] * zone["candidate_runtime_hours"] == pytest.approx(zone["requested_gross_water_l"])
        assert zone["flow_max_lph"] * zone["candidate_runtime_hours"] == pytest.approx(zone["candidate_upper_water_l"])
        assert zone["local_rate_upper_mm_h"] == pytest.approx(q_high/144*1.5)
    assert out["zones"][0]["candidate_runtime_hours"] == out["zones"][1]["candidate_runtime_hours"]
    json.dumps(out, allow_nan=False)


def test_interval_contains_independent_exact_single_nozzle_operating_point():
    data = sample()
    data["layout"].update(lateral_count=1, sprinklers_per_lateral=1, laterals_per_zone=1,
                          lateral_spacing_m=1, sprinkler_spacing_m=1, feed_length_m=5, riser_height_m=1)
    data["sprinkler"].update(reference_flow_lph=1, flow_tolerance_fraction=0,
                             lateral_through_k=0, riser_fittings_k=0)
    for name in ("main", "manifold", "lateral", "riser"):
        data[name].update(internal_diameter_mm=10, roughness_mm=0)
    data["headworks"].update(min_filter_loss_m=0, max_filter_loss_m=0,
                             main_fittings_k=0, manifold_through_k=0, lateral_takeoff_k=0)
    data["supply"].update(min_running_head_m=32, max_running_head_m=32)
    data["application"].update(net_depth_mm=0.1)
    out = evaluate_sprinkler_request(data)
    assert out["feasible"]
    # Independent Poiseuille resistance: 5m feed + .5m header + .5m lateral + 1m riser.
    # H_nozzle = 31 - R*q; q = C*sqrt(H_nozzle). Solve x²+R*C*x-31=0 analytically.
    resistance = 128*1e-6*7 / (3_600_000*pi*GRAVITY_M_S2*0.01**4)
    coefficient = 1/sqrt(30)
    x = 62/(sqrt((resistance*coefficient)**2+124)+resistance*coefficient)
    actual_head, actual_flow = x*x, coefficient*x
    z = out["zones"][0]
    assert z["sprinkler_min_head_m"] <= actual_head <= z["sprinkler_max_head_m"]
    assert z["flow_min_lph"] <= actual_flow <= z["flow_max_lph"]
    assert actual_head + resistance*actual_flow == pytest.approx(31)
    assert z["sprinkler_min_head_m"] == pytest.approx(31-resistance*sqrt(40/30))
    assert z["sprinkler_max_head_m"] == pytest.approx(31-resistance*sqrt(20/30))


def test_distributed_laminar_losses_include_every_segment_and_riser_height():
    data = sample()
    data["layout"].update(lateral_count=2, sprinklers_per_lateral=2, laterals_per_zone=2,
                          lateral_spacing_m=1, sprinkler_spacing_m=1, feed_length_m=5, riser_height_m=1)
    data["sprinkler"].update(reference_flow_lph=1, flow_tolerance_fraction=0,
                             lateral_through_k=0, riser_fittings_k=0)
    for name in ("main", "manifold", "lateral", "riser"):
        data[name].update(internal_diameter_mm=100, roughness_mm=0)
    data["headworks"].update(min_filter_loss_m=0, max_filter_loss_m=0,
                             main_fittings_k=0, manifold_through_k=0, lateral_takeoff_k=0)
    data["application"].update(net_depth_mm=0.1)
    out = evaluate_sprinkler_request(data)
    assert out["feasible"]
    # Last: main 5*4 + header .5*4+1*2 + lateral .5*2+1*1 + riser 1*1 = 27 q-m.
    # First: 20 + 2 + 1 + 1 = 24 q-m; pressure elevation subtracts another 1 metre.
    scale = 128*1e-6 / (3_600_000*pi*GRAVITY_M_S2*0.1**4)
    z = out["zones"][0]
    assert z["sprinkler_min_head_m"] == pytest.approx(31-27*sqrt(40/30)*scale)
    assert z["sprinkler_max_head_m"] == pytest.approx(32-24*sqrt(20/30)*scale)


@pytest.mark.parametrize("record,key,value,constraint", [
    ("supply", "max_flow_lph", 5000, "source_flow_lph"),
    ("supply", "daily_available_l", 1000, "daily_source_water_l"),
    ("supply", "available_hours", 1, "daily_runtime_hours"),
    ("supply", "min_running_head_m", 21, "sprinkler_min_head_m"),
    ("supply", "max_running_head_m", 50, "sprinkler_max_head_m"),
    ("main", "max_head_m", 39, "main_static_head_m"),
    ("riser", "max_head_m", 39, "riser_static_head_m"),
    ("sprinkler", "max_static_head_m", 40, "sprinkler_static_head_m"),
    ("headworks", "filter_microns", 600, "filter_microns"),
    ("headworks", "filter_max_flow_lph", 5000, "filter_flow_lph"),
    ("limits", "max_velocity_m_s", 0.01, "velocity_m_s"),
    ("application", "max_local_depth_mm", 10, "local_depth_mm"),
    ("application", "max_local_rate_mm_h", 10, "local_rate_mm_h"),
    ("application", "wind_m_s", 4, "wind_m_s"),
    ("layout", "lateral_spacing_m", 13, "lateral_spacing_m"),
    ("layout", "sprinkler_spacing_m", 13, "sprinkler_spacing_m"),
])
def test_failing_constraints_never_produce_a_schedule(record, key, value, constraint):
    data = sample()
    data[record][key] = value
    if key in ("max_running_head_m", "max_static_head_m"):
        data["supply"]["max_static_head_m"] = 60
    out = evaluate_sprinkler_request(data)
    assert not out["feasible"] and out["schedule"] is None
    assert constraint in out["binding_constraints"]


def test_peak_pattern_and_efficiency_do_not_disappear_into_average_rate():
    data = sample()
    before = evaluate_sprinkler_request(data)
    data["application"]["peak_to_average_factor"] = 3
    after = evaluate_sprinkler_request(data)
    assert after["requested_gross_water_l"] == before["requested_gross_water_l"]
    assert after["zones"][0]["local_rate_upper_mm_h"] == 2*before["zones"][0]["local_rate_upper_mm_h"]
    assert "local_rate_mm_h" in after["binding_constraints"]
    data = sample()
    data["application"]["efficiency"] = 0.375
    after = evaluate_sprinkler_request(data)
    assert after["requested_net_water_l"] == before["requested_net_water_l"]
    assert after["requested_gross_water_l"] == 2*before["requested_gross_water_l"]
    assert after["zones"][0]["candidate_runtime_hours"] == 2*before["zones"][0]["candidate_runtime_hours"]


def test_circular_coverage_checks_cell_corners_even_with_permissive_spacing_ratio():
    data = sample()
    data["sprinkler"]["max_spacing_fraction"] = 1
    data["layout"].update(lateral_spacing_m=18, sprinkler_spacing_m=18)
    out = evaluate_sprinkler_request(data)
    assert "lateral_spacing_m" not in out["binding_constraints"]
    assert "sprinkler_spacing_m" not in out["binding_constraints"]
    assert "cell_diagonal_m" in out["binding_constraints"]
    assert out["schedule"] is None


@pytest.mark.parametrize("record,key,value", [
    ("layout", "lateral_count", True), ("layout", "laterals_per_zone", 4),
    ("layout", "terrain", "sloped"), ("layout", "riser_height_m", -1),
    ("sprinkler", "pattern", "half_circle"), ("sprinkler", "flow_tolerance_fraction", 1),
    ("sprinkler", "reference_head_m", 15), ("sprinkler", "min_head_m", 0),
    ("sprinkler", "source", ""), ("sprinkler", "min_wetted_diameter_m", float("nan")),
    ("application", "efficiency", 0), ("application", "peak_to_average_factor", 0.5),
    ("application", "wind_m_s", True), ("application", "net_depth_mm", None),
])
def test_invalid_or_unsupported_inputs_do_not_use_defaults(record, key, value):
    data = sample()
    data[record][key] = value
    with pytest.raises(ValueError):
        evaluate_sprinkler_request(data)


def test_missing_evidence_and_unknown_fields_are_rejected():
    for change in (lambda d: d.update(schema_version=True),
                   lambda d: d["application"].pop("peak_to_average_factor"),
                   lambda d: d["sprinkler"].update(pressure_compensating=True),
                   lambda d: d.pop("riser")):
        data = sample()
        change(data)
        with pytest.raises(ValueError):
            evaluate_sprinkler_request(data)
