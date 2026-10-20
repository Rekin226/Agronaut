"""Independent laminar solutions, bounded nonlinear solves and surveyed extrema."""

import json
from math import pi
from pathlib import Path

import pytest

from agro_model.hydraulics import GRAVITY_M_S2, Pipe, pipe_loss
from agronaut_agent.drip_lateral_io import evaluate_drip_lateral_request


def inputs():
    return json.loads((Path(__file__).resolve().parents[2]/"examples/drip_lateral.json").read_text())


def evaluate(data):
    return evaluate_drip_lateral_request(data)


def test_two_linear_emitters_match_independent_laminar_algebra():
    data = inputs()
    data["pipe"].update(internal_diameter_mm=100, roughness_mm=0)
    data["emitter"].update(reference_flow_lph=1, reference_head_m=10, exponent=1)
    data["stations"] = data["stations"][:2]
    for row in data["stations"]:
        row["upstream_minor_k"] = 0
    # R in metres of head per L/h, equal 5m segments. q = c*h.
    resistance = 128*5*1e-6/(3_600_000*pi*GRAVITY_M_S2*.1**4)
    c, inlet, z1, z2 = .1, 10, .2, .5
    h2 = (inlet-z1-(1+resistance*c)*(z2-z1))/((1+resistance*c)**2+resistance*c)
    h1 = (1+resistance*c)*h2+z2-z1
    result = evaluate(data)
    assert result["feasible"]
    for row, head in zip(result["prediction"]["nodes"], (h1, h2)):
        assert row["pressure_head_m"] == pytest.approx(head, abs=3e-9)
        assert row["emitter_flow_lph"] == pytest.approx(c*head, abs=3e-10)
    assert result["prediction"]["inlet_flow_lph"] == pytest.approx(c*(h1+h2), abs=6e-10)


@pytest.mark.parametrize("exponent", [0, .5, 1])
def test_mass_head_and_curve_balance_at_every_station(exponent):
    data = inputs()
    data["emitter"]["exponent"] = exponent
    result = evaluate(data)
    prediction = result["prediction"]
    assert prediction is not None
    assert abs(prediction["mass_balance_residual_lph"]) < 1e-12
    previous_head, previous_z = data["inlet"]["running_head_m"], 0
    for index, row in enumerate(prediction["nodes"]):
        assert row["upstream_segment_flow_lph"] == pytest.approx(sum(n["emitter_flow_lph"] for n in prediction["nodes"][index:]))
        assert row["emitter_flow_lph"] == pytest.approx(2*(row["pressure_head_m"]/10)**exponent)
        assert previous_head+previous_z-row["pressure_head_m"]-row["elevation_m"] == pytest.approx(
            row["upstream_segment_loss"]["total_head_m"], abs=3e-9)
        assert abs(row["head_balance_residual_m"]) <= prediction["head_tolerance_m"]*1.01
        previous_head, previous_z = row["pressure_head_m"], row["elevation_m"]
    assert result["schedule"] is None and result["event_kind"] == "scenario"
    json.dumps(result, allow_nan=False)


def test_constant_flow_matches_hand_integrated_laminar_loss_and_elevation():
    data = inputs()
    data["emitter"]["exponent"] = 0
    data["pipe"].update(internal_diameter_mm=100, roughness_mm=0)
    for row in data["stations"]:
        row["upstream_minor_k"] = 0
    coefficient = 128*1e-6/(3_600_000*pi*GRAVITY_M_S2*.1**4)
    result = evaluate(data)
    loss = 0
    for i, row in enumerate(result["prediction"]["nodes"]):
        loss += coefficient*5*(5-i)*2
        assert row["pressure_head_m"] == pytest.approx(10-row["elevation_m"]-loss, abs=3e-9)
    assert result["prediction"]["flow_variation_fraction"] == 0


def test_internal_high_and_low_stations_control_pressure_and_static_rating():
    data = inputs()
    for row, z in zip(data["stations"], [0, 2, -2, 0, 0]):
        row["elevation_m"] = z
    data["pipe"]["max_head_m"] = 21
    data["emitter"].update(max_head_m=20, max_static_head_m=21)
    result = evaluate(data)
    nodes = result["prediction"]["nodes"]
    assert min(nodes, key=lambda row: row["pressure_head_m"])["identifier"] == "E2"
    assert max(nodes, key=lambda row: row["pressure_head_m"])["identifier"] == "E3"
    failures = {(r["code"], r["identifier"]) for r in result["constraints"]}
    assert ("pipe_static_head_m", "E3") in failures
    assert ("emitter_static_head_m", "E3") in failures
    assert not result["feasible"]


@pytest.mark.parametrize("head", [.01, 40])
def test_out_of_envelope_solution_is_withheld_not_extrapolated(head):
    data = inputs()
    data["inlet"].update(running_head_m=head, max_static_head_m=max(40, head))
    result = evaluate(data)
    assert result["prediction"] is None and result["feasible"] is None
    assert "no_solution_within_emitter_curve_envelope" in result["binding_constraints"]


def test_incompatible_interior_pressure_bounds_do_not_produce_a_false_solution():
    data = inputs()
    data["emitter"].update(min_head_m=9, max_head_m=11)
    data["stations"][1]["elevation_m"] = 4
    data["stations"][2]["elevation_m"] = -1
    result = evaluate(data)
    assert result["prediction"] is None


@pytest.mark.parametrize("head", [2, 30])
def test_solution_exactly_at_supported_curve_endpoint_is_retained(head):
    data = inputs()
    data["stations"] = data["stations"][:1]
    flow = 2*(head/10)**.5
    loss = pipe_loss(Pipe(**data["pipe"]), flow_lph=flow, length_m=5,
                     viscosity_m2_s=1e-6, minor_k=.1)["total_head_m"]
    data["inlet"].update(running_head_m=head+.2+loss, max_static_head_m=40)
    result = evaluate(data)
    assert result["prediction"]["nodes"][0]["pressure_head_m"] == head


def test_higher_inlet_head_increases_every_noncompensating_outflow():
    data = inputs()
    before = evaluate(data)["prediction"]
    data["inlet"]["running_head_m"] = 12
    after = evaluate(data)["prediction"]
    assert all(a["emitter_flow_lph"] > b["emitter_flow_lph"] for a, b in zip(after["nodes"], before["nodes"]))
    assert after["inlet_flow_lph"] > before["inlet_flow_lph"]


@pytest.mark.parametrize("record,key,value,code", [
    ("inlet", "max_flow_lph", 1, "inlet_flow_lph"),
    ("limits", "max_velocity_m_s", .0001, "velocity_m_s"),
    ("limits", "max_flow_variation_fraction", .0001, "flow_variation_fraction"),
    ("pipe", "max_head_m", 15, "pipe_static_head_m"),
])
def test_supplied_limits_fail_without_hiding_the_conditional_scenario(record, key, value, code):
    data = inputs()
    data[record][key] = value
    result = evaluate(data)
    assert result["prediction"] is not None and not result["feasible"]
    assert code in result["binding_constraints"] and result["schedule"] is None


@pytest.mark.parametrize("change", [
    lambda d: d["emitter"].update(exponent=-.1),
    lambda d: d["emitter"].update(exponent=1.1),
    lambda d: d["emitter"].update(reference_head_m=1),
    lambda d: d["emitter"].update(source=""),
    lambda d: d["conditions"].update(closed_tail=1),
    lambda d: d["stations"][1].update(identifier="E1"),
    lambda d: d["stations"][0].update(identifier="inlet"),
    lambda d: d["stations"][1].update(distance_m=5),
    lambda d: d["stations"][0].update(elevation_m=6),
    lambda d: d["stations"][0].update(elevation_m=float("nan")),
    lambda d: d.update(stations=[]),
    lambda d: d.update(stations=d["stations"]*401),
])
def test_invalid_curve_geometry_or_missing_evidence_is_rejected(change):
    data = inputs()
    change(data)
    with pytest.raises(ValueError):
        evaluate(data)
