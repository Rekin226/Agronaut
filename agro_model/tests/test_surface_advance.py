"""Published advance example, independent limits, units and water conservation."""

import json
from dataclasses import replace
from math import sqrt
from pathlib import Path

import pytest

from agro_model.surface import Geometry, Infiltration
from agro_model.surface_advance import (
    AdvanceConditions,
    AdvanceHydraulics,
    AdvanceLimits,
    SurfaceAdvance,
    _time,
    predict_surface_advance,
)


def spec():
    data = json.loads((Path(__file__).resolve().parents[2]/"examples/surface_advance.json").read_text())
    return SurfaceAdvance(**{name: cls(**data[name]) for name, cls in dict(
        geometry=Geometry, hydraulics=AdvanceHydraulics, infiltration=Infiltration,
        limits=AdvanceLimits, conditions=AdvanceConditions).items()})


def test_fao45_published_65_minute_example_and_first_iteration():
    case = spec()
    result = predict_surface_advance(case)
    area = result["inlet"]["flow_area_m2"]
    assert area == pytest.approx(.0219, rel=1e-4)
    # Published intermediate tL≈66.07 for the initial r=.6, then ≈65 at convergence.
    assert _time(.15, .77*area, .00324, .568, .000174, .6, 200, 120) == pytest.approx(66.07, abs=.1)
    assert result["prediction"]["full_length_advance_min"] == pytest.approx(65, abs=.5)
    assert result["prediction"]["half_length_advance_min"] < result["prediction"]["full_length_advance_min"]
    assert result["cutoff_recommendation"] is None and result["feasible"] is None
    assert not result["binding_constraints"]
    for row in result["prediction"]["anchors"]:
        assert row["inflow_l"] == pytest.approx(row["surface_storage_l"]+row["infiltration_l"], rel=1e-12)
        assert abs(row["balance_residual_l"]) < 1e-8
    json.dumps(result, allow_nan=False)


def test_no_intake_reduces_to_linear_filling_of_surface_storage():
    case = spec()
    case = replace(case, infiltration=replace(case.infiltration, k=0, steady_rate=0))
    result = predict_surface_advance(case)
    p = result["prediction"]
    expected = .77*result["inlet"]["flow_area_m2"]*200/.15
    assert p["full_length_advance_min"] == pytest.approx(expected)
    assert p["half_length_advance_min"] == pytest.approx(expected/2)
    assert p["advance_exponent"] == pytest.approx(1)
    assert all(row["infiltration_l"] == 0 for row in p["anchors"])


@pytest.mark.parametrize("linear_term", ["k", "steady_rate"])
def test_independently_constructed_constant_intake_has_known_exponent(linear_term):
    case = spec()
    r = .75
    ratio = 2**(-1/r)
    b = (1-2*ratio)/(1-ratio)
    rate = b*(1+r)*.15/200
    intake = replace(case.infiltration, k=0, steady_rate=0, exponent=1)
    case = replace(case, infiltration=replace(intake, **{linear_term: rate}))
    out = predict_surface_advance(case)
    p = out["prediction"]
    expected = .77*out["inlet"]["flow_area_m2"]*200/(.15-rate*200/(1+r))
    assert p["advance_exponent"] == pytest.approx(r, abs=1e-9)
    assert p["full_length_advance_min"] == pytest.approx(expected, rel=1e-9)
    assert p["half_length_advance_min"] == pytest.approx(ratio*expected, rel=1e-9)


def test_depth_and_whole_strip_volume_bases_are_equivalent():
    case = spec()
    case = replace(case, geometry=replace(case.geometry, width_m=1.5))
    volume = predict_surface_advance(case)
    depth = replace(case.infiltration, basis="depth_mm", k=case.infiltration.k*1000/1.5,
                    steady_rate=case.infiltration.steady_rate*1000/1.5)
    converted = predict_surface_advance(replace(case, infiltration=depth))
    assert converted["prediction"] == volume["prediction"]
    # Represented furrow spacing must not multiply already integrated volume-per-length intake.
    wider = predict_surface_advance(replace(case, geometry=replace(case.geometry, width_m=3)))
    assert wider["prediction"] == volume["prediction"]


def test_border_width_scales_volumes_not_advance_at_equal_unit_flow():
    case = spec()
    case = replace(case, geometry=replace(case.geometry, method="border", width_m=10),
        hydraulics=replace(case.hydraulics, inflow_lps=25, section_p1=1, section_p2=5/3),
        infiltration=replace(case.infiltration, basis="depth_mm", k=3.24, steady_rate=.174),
        limits=replace(case.limits, max_inlet_flow_area_m2=2, max_inlet_velocity_m_s=1))
    result = predict_surface_advance(case)
    depth = result["inlet"]["border_depth_m"]
    assert 25/1000 == pytest.approx(10*depth*depth**(2/3)*sqrt(.001)/.04)
    doubled = predict_surface_advance(replace(case,
        geometry=replace(case.geometry, width_m=20), hydraulics=replace(case.hydraulics, inflow_lps=50)))
    assert doubled["prediction"]["full_length_advance_min"] == result["prediction"]["full_length_advance_min"]
    assert doubled["inlet"]["border_depth_m"] == result["inlet"]["border_depth_m"]
    assert doubled["prediction"]["anchors"][-1]["inflow_l"] == 2*result["prediction"]["anchors"][-1]["inflow_l"]


@pytest.mark.parametrize("limit,value,reason", [
    ("max_advance_min", 10, "time_envelope"),
    ("max_inlet_velocity_m_s", .01, "velocity"),
    ("max_inlet_flow_area_m2", .001, "flow_area"),
])
def test_limits_withhold_a_prediction_instead_of_reporting_an_operating_plan(limit, value, reason):
    case = spec()
    result = predict_surface_advance(replace(case, limits=replace(case.limits, **{limit: value})))
    assert result["prediction"] is None
    assert any(reason in item for item in result["binding_constraints"])
    assert result["feasible"] is None


def test_intake_envelope_is_not_silently_extrapolated():
    case = spec()
    result = predict_surface_advance(replace(case, infiltration=replace(case.infiltration, max_opportunity_min=50)))
    assert result["prediction"] is None and result["time_envelope_min"] == 50
    near = predict_surface_advance(replace(case, limits=replace(case.limits, max_advance_min=65.1)))
    assert near["prediction"] is not None  # viable r is above the initial trial's range


@pytest.mark.parametrize("linear_term", ["k", "steady_rate"])
def test_constant_intake_capacity_cannot_exceed_the_inflow(linear_term):
    case = spec()
    intake = replace(case.infiltration, k=0, steady_rate=0, exponent=1)
    result = predict_surface_advance(replace(case, infiltration=replace(intake, **{linear_term: .15/200})))
    assert result["prediction"] is None
    assert result["binding_constraints"] == ["constant_intake_at_full_length_not_below_inflow"]


@pytest.mark.parametrize("name,changes", [
    ("geometry", {"method": "basin", "slope_m_m": 0}),
    ("geometry", {"slope_m_m": 0}),
    ("geometry", {"method": "border"}),
    ("hydraulics", {"surface_shape_factor": True}),
    ("hydraulics", {"inflow_lps": float("nan")}),
    ("hydraulics", {"manning_n": 0}),
    ("hydraulics", {"section_p2": 2}),
    ("infiltration", {"min_opportunity_min": 1}),
    ("infiltration", {"max_opportunity_min": 0}),
    ("limits", {"max_advance_min": 1441}),
    ("limits", {"source": ""}),
    ("conditions", {"constant_inflow": False}),
    ("conditions", {"normal_inlet_flow": 1}),
])
def test_unsupported_or_unstated_physics_is_rejected(name, changes):
    case = spec()
    with pytest.raises(ValueError):
        replace(case, **{name: replace(getattr(case, name), **changes)})
