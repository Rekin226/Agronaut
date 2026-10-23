"""Surface-event unit conversion, exact spatial integrals and accounting safeguards."""

import json
from pathlib import Path

import pytest

from agro_model.surface import Infiltration, _clipped_mean, _low_quarter_mean
from agronaut_agent.surface_io import evaluate_surface_request


def sample():
    return json.loads((Path(__file__).resolve().parents[2]/"examples/surface_event.json").read_text())


def test_independent_linear_profile_and_exact_crossing_storage_integral():
    out = evaluate_surface_request(sample())
    # Depth decreases linearly from 60 to 30 mm; mean 45; lowest quarter 30..37.5.
    # Storage capped at 40: mean = 40 - triangle(area 10*100/3/2)/100 = 38 1/3 mm.
    assert out["water_balance_accepted"]
    assert out["volumes"] == dict(inflow_l=54000, tailwater_l=9000, modeled_infiltration_l=45000,
                                  unaccounted_l=0, unaccounted_fraction=0)
    p = out["performance"]
    assert p["low_quarter_mean_infiltration_mm"] == pytest.approx(33.75)
    assert p["low_quarter_distribution_uniformity"] == pytest.approx(0.75)
    assert p["modeled_root_zone_storage_l"] == pytest.approx(115000/3)
    assert p["modeled_deep_percolation_l"] == pytest.approx(20000/3)
    assert p["adequately_irrigated_area_fraction"] == pytest.approx(2/3)
    assert p["storage_efficiency"] == pytest.approx(23/24)
    assert p["application_efficiency"] == pytest.approx((115000/3)/54000)
    assert p["application_efficiency"]+p["deep_percolation_ratio"]+p["tailwater_ratio"] == pytest.approx(1)
    assert out["event_ended_at"] == "2026-07-06T10:00:00+00:00"
    assert out["cutoff_recommendation"] is None and out["feasible"] is None
    assert out["event_kind"] == "scenario" and out["evidence_status"] == "experimental"
    json.dumps(out, allow_nan=False)


def test_furrow_volume_per_length_is_not_mistaken_for_depth_or_multiplied_by_spacing():
    data = sample()
    data["geometry"].update(method="furrow", width_m=1)
    data["infiltration"].update(basis="volume_per_length_m2", steady_rate=0.0005)
    for interval in data["inflow"]+data["tailwater"]:
        interval["mean_lps"] /= 10
    out = evaluate_surface_request(data)
    assert out["water_balance_accepted"]
    assert out["volumes"]["modeled_infiltration_l"] == pytest.approx(4500)
    assert out["stations"][0]["modeled_infiltration_mm"] == 60
    data["geometry"]["width_m"] = 2
    wider = evaluate_surface_request(data)
    assert wider["volumes"]["modeled_infiltration_l"] == out["volumes"]["modeled_infiltration_l"]
    assert wider["stations"][0]["modeled_infiltration_mm"] == 30
    assert wider["area_m2"] == 2*out["area_m2"]


def test_station_density_does_not_bias_area_weighted_metrics():
    data = sample()
    before = evaluate_surface_request(data)
    data["stations"].insert(1, dict(distance_m=10, advance_min=6, recession_min=120, source="Synthetic extra station"))
    after = evaluate_surface_request(data)
    assert after["volumes"] == before["volumes"]
    for key in before["performance"]:
        assert after["performance"][key] == pytest.approx(before["performance"][key])


def test_nonlinear_intake_and_calibration_envelope():
    intake = Infiltration(k=2, exponent=0.5, steady_rate=0.25, basis="depth_mm",
                          min_opportunity_min=0, max_opportunity_min=100, source="Synthetic square-root example")
    assert intake.cumulative(0) == 0
    assert intake.cumulative(64) == 32
    with pytest.raises(ValueError):
        intake.cumulative(101)
    data = sample()
    data["infiltration"]["max_opportunity_min"] = 100
    with pytest.raises(ValueError, match="opportunity"):
        evaluate_surface_request(data)


@pytest.mark.parametrize("profile,expected", [
    ([(0, 40), (100, 40)], 40),
    ([(0, 60), (50, 30), (100, 60)], 33.75),
    ([(0, 0), (25, 0), (100, 100)], 0),
    ([(0, 10), (50, 10), (100, 30)], 10),
])
def test_low_quarter_handles_nonmonotonic_profiles_and_constant_ties(profile, expected):
    assert _low_quarter_mean(profile) == pytest.approx(expected, abs=1e-10)


def test_storage_crossing_integral_is_not_trapezoid_of_clipped_endpoints():
    assert _clipped_mean(0, 100, 50) == 37.5
    assert _clipped_mean(100, 0, 50) == 37.5


def test_unknown_initial_deficit_does_not_become_zero_or_hide_distribution():
    data = sample()
    data["assessment"]["root_zone_deficit_mm"] = None
    out = evaluate_surface_request(data)
    assert out["water_balance_accepted"]
    assert out["performance"]["low_quarter_distribution_uniformity"] == pytest.approx(0.75)
    assert out["performance"]["application_efficiency"] is None
    assert out["performance"]["modeled_deep_percolation_l"] is None
    assert out["missing_inputs"] == ["root_zone_deficit_mm"]


@pytest.mark.parametrize("flow,expected", [(20, "water_balance_mismatch"), (9.9, "modeled_outputs_exceed_inflow")])
def test_bad_water_accounting_withholds_performance_instead_of_fabricating_efficiency(flow, expected):
    data = sample()
    data["inflow"][0]["mean_lps"] = flow
    out = evaluate_surface_request(data)
    assert out["performance"] is None and not out["water_balance_accepted"]
    assert expected in out["binding_constraints"]
    assert len(out["stations"]) == 3
    assert out["cutoff_recommendation"] is None


@pytest.mark.parametrize("change", [
    lambda d: d["inflow"].pop(),
    lambda d: d["tailwater"][1].update(start_min=61),
    lambda d: d["tailwater"][1].update(start_min=59),
    lambda d: d["inflow"][1].update(mean_lps=1),
    lambda d: d["inflow"][0].update(end_min=100),
    lambda d: d["tailwater"][0].update(mean_lps=1),
    lambda d: d["stations"].pop(),
    lambda d: d["stations"][0].update(advance_min=1),
    lambda d: d["stations"][1].update(recession_min=10),
    lambda d: d["stations"][1].update(recession_min=121),
    lambda d: d["stations"][1].update(distance_m=0),
    lambda d: d["stations"][2].update(advance_min=20),
    lambda d: d["geometry"].update(method="furrow"),
    lambda d: d["event"].update(surface_drained=False),
    lambda d: d["event"].update(surface_drained=1),
    lambda d: d["event"].update(rainfall_mm=1),
    lambda d: d["event"].update(initial_surface_water_l=10),
    lambda d: d["event"].update(started_at="2026-07-06T08:00:00"),
    lambda d: d["infiltration"].update(exponent=True),
    lambda d: d["assessment"].update(balance_tolerance_fraction=1),
    lambda d: d["inflow"][0].update(mean_lps=None),
    lambda d: d["stations"][0].update(source=""),
    lambda d: d["site"].update(timezone="somewhere"),
    lambda d: d.update(schema_version=True),
    lambda d: d["infiltration"].update(default_soil="clay"),
])
def test_incomplete_unsupported_or_invalid_event_data_are_rejected(change):
    data = sample()
    change(data)
    with pytest.raises(ValueError):
        evaluate_surface_request(data)


def test_level_basin_and_explicit_cutback_keep_volume_accounting():
    data = sample()
    data["geometry"].update(method="basin", slope_m_m=0)
    data["inflow"] = [dict(start_min=0, end_min=30, mean_lps=20, source="Synthetic high inflow"),
                      dict(start_min=30, end_min=90, mean_lps=5, source="Synthetic cutback"),
                      data["inflow"][-1]]
    out = evaluate_surface_request(data)
    assert out["volumes"]["inflow_l"] == 54000
    assert out["water_balance_accepted"]


def test_no_infiltration_and_zero_deficit_have_defined_missing_ratios():
    data = sample()
    data["infiltration"].update(k=0, steady_rate=0)
    data["tailwater"][1]["mean_lps"] = 15
    data["assessment"]["root_zone_deficit_mm"] = 0
    out = evaluate_surface_request(data)
    assert out["water_balance_accepted"]
    assert out["performance"]["low_quarter_distribution_uniformity"] is None
    assert out["performance"]["storage_efficiency"] is None
    assert out["performance"]["tailwater_ratio"] == 1


def test_roundoff_allowance_does_not_hide_relative_mismatch_at_tiny_volumes():
    data = sample()
    data["infiltration"].update(k=0, steady_rate=0)
    data["inflow"][0]["mean_lps"] = 1e-14
    data["tailwater"][1]["mean_lps"] = 2e-14
    out = evaluate_surface_request(data)
    assert not out["water_balance_accepted"]
    assert "modeled_outputs_exceed_inflow" in out["binding_constraints"]
