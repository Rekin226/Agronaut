"""Independent operating-point algebra and bounded source/lateral intersection."""

import json
from copy import deepcopy
from math import pi
from pathlib import Path

import pytest

from agro_model.hydraulics import GRAVITY_M_S2
from agronaut_agent.drip_lateral_io import evaluate_drip_lateral_request as evaluate


def inputs():
    return json.loads((Path(__file__).resolve().parents[2]/"examples/drip_lateral_curve.json").read_text())


def test_linear_emitter_pipe_and_source_match_independent_closed_form():
    data = inputs()
    data["stations"] = data["stations"][:1]
    data["stations"][0]["upstream_minor_k"] = 0
    data["pipe"].update(internal_diameter_mm=100, roughness_mm=0)
    data["emitter"].update(reference_flow_lph=1, reference_head_m=10, exponent=1)
    data["inlet"]["curve"] = [dict(flow_lph=0, pressure_head_m=14), dict(flow_lph=5, pressure_head_m=4)]
    # q=c*h, H_source=a-b*q, H_source=h+z+R*q (laminar).
    resistance = 128*5*1e-6/(3_600_000*pi*GRAVITY_M_S2*.1**4)
    c, a, b, z = .1, 14, 2, .2
    head = (a-z)/(1+c*(resistance+b))
    result = evaluate(data)
    prediction = result["prediction"]
    assert result["feasible"] is True
    assert prediction["nodes"][0]["pressure_head_m"] == pytest.approx(head, abs=4e-9)
    assert prediction["inlet_flow_lph"] == pytest.approx(c*head, abs=4e-10)
    assert prediction["inlet_pressure_head_m"] == pytest.approx(a-b*c*head, abs=4e-9)
    assert prediction["nodes"][0]["upstream_segment_loss"]["reynolds"] < 2000


def test_piecewise_intersection_satisfies_source_each_pipe_and_each_emitter():
    data = inputs()
    result = evaluate(data)
    p = result["prediction"]
    q = p["inlet_flow_lph"]
    assert 5 < q < 15  # second supplied segment
    assert p["inlet_pressure_head_m"] == pytest.approx(12-.6*(q-5), abs=1e-12)
    head, elevation = p["inlet_pressure_head_m"], 0
    for node in p["nodes"]:
        assert head+elevation == pytest.approx(node["pressure_head_m"]+node["elevation_m"]+
            node["upstream_segment_loss"]["total_head_m"], abs=4e-9)
        assert node["emitter_flow_lph"] == pytest.approx(2*(node["pressure_head_m"]/10)**.5)
        head, elevation = node["pressure_head_m"], node["elevation_m"]
    assert q == pytest.approx(sum(n["emitter_flow_lph"] for n in p["nodes"]), abs=1e-12)
    assert abs(p["inlet_head_residual_m"]) <= p["head_tolerance_m"]
    assert result["schedule"] is None and result["event_kind"] == "scenario"


@pytest.mark.parametrize("flows", [(0, 10), (10, 20)])
def test_pressure_compensating_outflow_at_either_source_endpoint_is_retained(flows):
    data = inputs()
    data["emitter"]["exponent"] = 0
    data["inlet"]["curve"] = [dict(flow_lph=q, pressure_head_m=12) for q in flows]
    p = evaluate(data)["prediction"]
    assert p is not None
    assert p["inlet_flow_lph"] == 10
    assert p["inlet_pressure_head_m"] == 12


@pytest.mark.parametrize("flows", [(0, 9), (11, 20)])
def test_no_extrapolation_when_fixed_emitter_flow_is_outside_source_range(flows):
    data = inputs()
    data["emitter"]["exponent"] = 0
    data["inlet"]["curve"] = [dict(flow_lph=q, pressure_head_m=12) for q in flows]
    result = evaluate(data)
    assert result["prediction"] is None and result["feasible"] is None
    assert result["schedule"] is None
    assert "no_solution_within_inlet_and_emitter_curves" in result["binding_constraints"]


def test_source_head_cannot_force_emitters_outside_their_supported_envelope():
    data = inputs()
    data["inlet"]["curve"] = [dict(flow_lph=0, pressure_head_m=.02), dict(flow_lph=20, pressure_head_m=.01)]
    assert evaluate(data)["prediction"] is None


def test_source_flow_limit_remains_a_constraint_on_a_solved_scenario():
    data = inputs()
    data["inlet"]["max_flow_lph"] = 1
    result = evaluate(data)
    assert result["prediction"] is not None and result["feasible"] is False
    assert "inlet_flow_lph" in result["binding_constraints"]


@pytest.mark.parametrize("mutate", [
    lambda d: d["inlet"].update(curve=[]),
    lambda d: d["inlet"].update(curve=d["inlet"]["curve"][:1]),
    lambda d: d["inlet"]["curve"][1].update(flow_lph=0),
    lambda d: d["inlet"]["curve"][1].update(pressure_head_m=15),
    lambda d: d["inlet"]["curve"][0].update(flow_lph=True),
    lambda d: d["inlet"]["curve"][0].update(pressure_head_m=float("nan")),
    lambda d: d["inlet"]["curve"][0].update(extra=1),
    lambda d: d["inlet"].update(head_reference="pump_head_gain"),
    lambda d: d["inlet"].update(configuration=""),
    lambda d: d["inlet"].update(source=""),
    lambda d: d["inlet"].update(max_static_head_m=10),
    lambda d: d["inlet"].update(running_head_m=10),
    lambda d: d.update(schema_version=1),
])
def test_unsupported_curves_references_and_mixed_versions_are_rejected(mutate):
    data = inputs()
    mutate(data)
    with pytest.raises((ValueError, TypeError)):
        evaluate(data)


def test_result_is_reproducible_json_and_does_not_mutate_inputs():
    data = inputs()
    before = deepcopy(data)
    first = evaluate(data)
    assert first == evaluate(data)
    assert data == before
    assert first["model_version"] == "drip-lateral-2-inlet-curve"
    assert first["inputs"] == data
    json.dumps(first, allow_nan=False)
