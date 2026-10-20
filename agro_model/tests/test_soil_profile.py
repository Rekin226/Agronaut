"""Depth integration, unknown coverage and published available-water examples."""

import copy
import json
from decimal import ROUND_UP, localcontext
from fractions import Fraction
from pathlib import Path

import pytest

from agro_model.soil_profile import evaluate_soil_profile


def example():
    return json.loads((Path(__file__).resolve().parents[2]/"examples/soil_profile.json").read_text())


def exact(result, name):
    return Fraction(result["root_zone"][name]["exact"])


def test_partial_last_layer_and_heterogeneous_storage_preserve_inputs():
    data = example()
    original = copy.deepcopy(data)
    result = evaluate_soil_profile(data)
    assert result["evidence_status"] == "experimental"
    assert exact(result, "field_capacity_storage_mm") == 220
    assert exact(result, "wilting_point_storage_mm") == 100
    assert exact(result, "total_available_water_mm") == 120
    assert exact(result, "water_storage_mm") == 180
    assert exact(result, "bounded_depletion_mm") == 40
    assert result["layers"][1]["rooted_thickness_m"]["exact"] == "2/5"
    assert result["complete_root_water_content"] and result["within_layer_water_bounds"]
    assert data == original and result["inputs"] == data
    data["layers"][0]["water_content"] = "0.1"
    assert result["inputs"] == original


@pytest.mark.parametrize("depth,expected_inches", [("0.3048", "2.52"), ("0.4572", "3.48"), ("1.524", "4.32")])
def test_umn_renshaw_published_available_water_totals(depth, expected_inches):
    # UMN Basics of irrigation scheduling, Renshaw table: 0–12 in AWC .21,
    # 12–18 in .16, 18–60 in .02. Synthetic WP/FC pairs differ by those AWC values.
    # This verifies depth integration, not recommended FC/WP for a regional soil.
    data = example()
    data["profile"]["root_depth_m"] = depth
    template = data["layers"][0]
    data["layers"] = [dict(template, id=str(i), top_m=top, bottom_m=bottom,
                          field_capacity=fc, wilting_point="0.1", water_content=None)
                      for i, (top, bottom, fc) in enumerate([
                          ("0", "0.3048", "0.31"), ("0.3048", "0.4572", "0.26"), ("0.4572", "1.524", "0.12")])]
    result = evaluate_soil_profile(data)
    assert exact(result, "total_available_water_mm") == Fraction(expected_inches)*Fraction("25.4")
    assert result["root_zone"]["bounded_depletion_mm"] is None


@pytest.mark.parametrize("change", ["missing", "rejected"])
def test_missing_rooted_reading_withholds_water_totals_but_not_soil_capacity(change):
    data = example()
    if change == "missing":
        data["layers"][1]["water_content"] = None
    else:
        data["layers"][1]["quality"] = "rejected"
    result = evaluate_soil_profile(data)
    assert not result["complete_root_water_content"]
    assert result["within_layer_water_bounds"] is None
    assert exact(result, "total_available_water_mm") == 120
    for key in ("water_storage_mm", "signed_deficit_mm", "bounded_depletion_mm", "above_field_capacity_mm", "below_wilting_point_mm"):
        assert result["root_zone"][key] is None
    assert result["layers"][0]["values"]["water_storage_mm"]["exact"] == "40/1"
    assert len(result["missing_inputs"]) == 1
    data["profile"]["root_depth_m"] = "0.2"
    result = evaluate_soil_profile(data)
    assert result["complete_root_water_content"] and not result["missing_inputs"]
    assert exact(result, "bounded_depletion_mm") == 20
    assert not result["layers"][1]["contributes_to_root_zone"]


def test_wet_and_dry_layer_cancellation_never_establishes_a_bounded_state():
    data = example()
    data["profile"]["root_depth_m"] = "0.4"
    data["layers"][0]["water_content"] = "0"
    data["layers"][1]["water_content"] = "0.7"
    result = evaluate_soil_profile(data)
    assert exact(result, "signed_deficit_mm") == 0
    assert exact(result, "above_field_capacity_mm") == 60
    assert exact(result, "below_wilting_point_mm") == 20
    assert result["complete_root_water_content"] and not result["within_layer_water_bounds"]
    assert result["root_zone"]["bounded_depletion_mm"] is None
    assert len(result["notes"]) == 2


def test_very_thin_layer_violation_is_not_lost_to_display_rounding_or_decimal_context():
    data = example()
    data["profile"]["root_depth_m"] = "0.000001"
    data["layers"][0].update(water_content="0.300001")
    result = evaluate_soil_profile(data)
    assert result["root_zone"]["above_field_capacity_mm"]["value"] == "0"
    assert exact(result, "above_field_capacity_mm") == Fraction(1, 1_000_000_000)
    assert result["root_zone"]["bounded_depletion_mm"] is None
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_UP
        assert evaluate_soil_profile(data) == result


@pytest.mark.parametrize("section,key,value", [
    ("profile", "root_depth_m", "0"), ("profile", "root_depth_m", "0.9"),
    ("profile", "root_depth_m", 0.6), ("profile", "representation_note", " "),
    ("profile", "kind", "forecast"), ("profile", "observed_at", "2026-07-06T18:00:00"),
    ("profile", "observed_at", "2026-07-06T18:00:00+08:00"),
    ("site", "timezone", "invalid/zone"),
    ("layer", "top_m", "0.1"), ("layer", "bottom_m", "0"),
    ("layer", "bottom_m", "0.3"), ("layer", "field_capacity", "0.1"),
    ("layer", "water_content", "-0.1"), ("layer", "water_content", "1.01"),
    ("layer", "water_content", float("nan")), ("layer", "water_content", True),
    ("layer", "water_content", "0.1234567"), ("layer", "soil_source", ""),
    ("layer", "provenance", "forecast"), ("layer", "quality", "validated"),
    ("layer", "id", "lower"),
])
def test_invalid_or_inconsistent_inputs_rejected(section, key, value):
    data = example()
    target = data["layers"][0] if section == "layer" else data[section]
    target[key] = value
    with pytest.raises(ValueError):
        evaluate_soil_profile(data)


def test_future_observed_rejected_but_scenario_keeps_its_label_and_dst_offsets_checked():
    data = example()
    data["profile"].update(kind="observed", observed_at="2099-07-06T18:00:00Z")
    with pytest.raises(ValueError, match="future"):
        evaluate_soil_profile(data)
    data["profile"]["kind"] = "scenario"
    assert evaluate_soil_profile(data)["inputs"]["profile"]["kind"] == "scenario"
    data["site"]["timezone"] = "America/New_York"
    data["profile"]["observed_at"] = "2026-11-01T01:00:00-05:00"
    assert evaluate_soil_profile(data)["observed_at_local"].endswith("-05:00")
    data["profile"]["observed_at"] = "2026-03-08T02:00:00-05:00"
    with pytest.raises(ValueError, match="offset"):
        evaluate_soil_profile(data)


def test_homogeneous_layer_splitting_preserves_totals_and_boundary_states():
    data = example()
    data["layers"] = [dict(data["layers"][0], bottom_m="0.6")]
    for theta in ("0.1", "0.2", "0.3"):
        data["layers"][0]["water_content"] = theta
        single = evaluate_soil_profile(data)
        split = copy.deepcopy(data)
        split["layers"] = [dict(data["layers"][0], id=str(i), top_m=str(i/10), bottom_m=str((i+1)/10)) for i in range(6)]
        assert evaluate_soil_profile(split)["root_zone"] == single["root_zone"]


@pytest.mark.parametrize("kind", ["empty", "extra", "too_many", "schema"])
def test_structural_limits(kind):
    data = example()
    if kind == "empty":
        data["layers"] = []
    elif kind == "extra":
        data["layers"][0]["bulk_density"] = "1.4"
    elif kind == "too_many":
        data["layers"] *= 51
    else:
        data["schema_version"] = True
    with pytest.raises(ValueError):
        evaluate_soil_profile(data)
