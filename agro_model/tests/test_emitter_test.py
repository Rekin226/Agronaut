"""Independent timed-volume arithmetic and evidence boundaries for drip tests."""

import json
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

import pytest

from agro_model.emitter_test import evaluate_emitter_test


def example():
    return json.loads((Path(__file__).resolve().parents[2]/"examples/drip_emitters.json").read_text())


def test_different_collection_times_are_normalized_before_low_quarter_selection():
    data = example()
    before = deepcopy(data)
    result = evaluate_emitter_test(data)
    assert [r["flow_l_h"] for r in result["samples"]] == [1.2, 1.2, 3.6, 2.4, 6, 3.6, 8.4, 4.8]
    assert result["statistics"] == dict(mean_flow_l_h=3.9, low_quarter_mean_flow_l_h=1.2,
        low_quarter_du_pct=pytest.approx(400/13), minimum_flow_l_h=1.2, maximum_flow_l_h=8.4)
    assert Fraction(result["exact_statistics"]["low_quarter_du_pct"]) == Fraction(400,13)
    assert result["low_quarter_sample_ids"] == ["emitter-1", "emitter-2"]
    assert result["pressure_summary"] == dict(known_samples=8, unknown_samples=0, minimum_kpa=150, maximum_kpa=150)
    assert data == before and result["inputs"] == before
    assert result["feasible"] is None
    result["inputs"]["samples"][0]["volume_ml"] = 99
    assert data == before


def test_uniform_zero_missing_and_unknown_pressure_remain_distinct():
    data = example()
    for row in data["samples"]:
        row.update(volume_ml=20, duration_seconds=60, pressure_kpa=None)
    result = evaluate_emitter_test(data)
    assert result["statistics"]["low_quarter_du_pct"] == 100
    assert result["pressure_summary"]["unknown_samples"] == 8
    assert result["pressure_summary"]["minimum_kpa"] is None
    for row in data["samples"]:
        row["volume_ml"] = 0
    result = evaluate_emitter_test(data)
    assert result["statistics"]["mean_flow_l_h"] == 0 and result["statistics"]["low_quarter_du_pct"] is None
    assert not result["missing_inputs"]
    data["samples"][0]["volume_ml"] = None
    result = evaluate_emitter_test(data)
    assert all(value is None for value in result["statistics"].values())
    assert result["samples"][0]["flow_l_h"] is None and result["samples"][1]["flow_l_h"] == 0
    assert result["missing_inputs"] == ["volume_ml for emitter-1"]


@pytest.mark.parametrize("section,key,value", [
    ("site", "timezone", "unknown-zone"), ("event", "stable_operation", False),
    ("event", "kind", "forecast"), ("event", "started_at", "2026-07-07T09:00:00"),
    ("event", "ended_at", "2026-07-07T08:30:00Z"), ("event", "ended_at", "2026-07-09T09:30:00Z"),
    ("sampling", "expected_count", True), ("sampling", "expected_count", 7), ("sampling", "expected_count", 2504),
    ("sampling", "same_emitter_type", False), ("sampling", "equal_representation", "true"), ("sampling", "source", ""),
    ("sample", "volume_ml", -1), ("sample", "volume_ml", float("nan")), ("sample", "volume_ml", float("inf")),
    ("sample", "volume_ml", "20"), ("sample", "duration_seconds", 0), ("sample", "duration_seconds", 1e-300),
    ("sample", "duration_seconds", 2000), ("sample", "pressure_kpa", -1), ("sample", "pressure_kpa", True),
    ("sample", "source", ""), ("sample", "lateral", " "), ("sample", "id", "emitter-2"),
])
def test_incompatible_or_invalid_input_fails(section, key, value):
    data = example()
    target = data["samples"][0] if section == "sample" else data[section]
    target[key] = value
    with pytest.raises(ValueError):
        evaluate_emitter_test(data)


def test_repeated_locations_missing_rows_extra_fields_and_future_actuals_fail():
    for kind in ("location", "missing", "extra", "future"):
        data = example()
        if kind == "location":
            data["samples"][0].update(lateral=" l1 ", position="p2")
        elif kind == "missing":
            data["samples"].pop()
        elif kind == "extra":
            data["efficiency"] = 0.8
        else:
            data["event"].update(kind="observed", started_at="9998-01-01T09:00:00Z", ended_at="9998-01-01T09:30:00Z")
        with pytest.raises(ValueError):
            evaluate_emitter_test(data)


def test_dst_elapsed_window_and_input_order_preserve_flows_and_low_quarter():
    data = example()
    expected = evaluate_emitter_test(data)
    data["site"]["timezone"] = "America/New_York"
    data["event"].update(started_at="2026-11-01T01:30:00-04:00", ended_at="2026-11-01T01:30:00-05:00")
    data["samples"].reverse()
    result = evaluate_emitter_test(data)
    assert result["statistics"] == expected["statistics"]
    assert result["low_quarter_sample_ids"] == expected["low_quarter_sample_ids"]
    assert result["event_ended_at"] == "2026-11-01T01:30:00-05:00"
