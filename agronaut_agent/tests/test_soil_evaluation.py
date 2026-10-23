"""Owned soil diagnostics, known data reuse and offline CLI without state adoption."""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agro_model.farm import Observation, ProductionCycle
from agronaut_agent.field_workflow import execute_field_workflow
from agronaut_agent.observation_quality import ObservationReviews
from agronaut_agent.soil_evaluation import render_soil_comparison
from agronaut_agent.tests.test_crop_coefficient_workflow import series
from agronaut_agent.tests.test_field_workflow import inputs
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_root_growth_workflow import growing
from agronaut_agent.tests.test_soil_workflow import adoption, measurement


def entry(**changes):
    result = dict(observation_id="soil", as_of="2026-07-06", represented_depth_m=.8,
        representation_note="Synthetic full-depth and field mean", depletion_adjustment_mm=2,
        adjustment_source="Synthetic timing correction", expected_review=None)
    result.update(changes)
    return result


def compare(workflow, saved, entries=None, **changes):
    data = dict(calculation_id=saved["id"], comparisons=[entry()] if entries is None else entries,
        dataset_kind="synthetic", selection_note="Synthetic diagnostic fixture; no held-out field evidence")
    data.update(changes)
    return execute_field_workflow(workflow.db, "owner", "field", "compare_soil", data)


def test_read_only_report_uses_prediction_before_same_day_assimilation(setup):
    workflow, store = setup
    measurement(store)
    base = workflow.save("owner", "field", "root_zone_budget", series(), request_id="base", cycle_id="cycle")
    child = workflow.apply_soil_state("owner", "field", **adoption(base))
    before = store.export("owner")
    result = compare(workflow, child)
    assert store.export("owner") == before
    prediction = base["result"]["result"]["days"][0]["balance"]["values"]["depletion_mm"]
    row = result["comparisons"][0]
    assert row["same_day_state_update"] and row["included_in_metrics"]
    assert row["values"]["predicted_depletion_mm"] == prediction
    assert row["values"]["reference_depletion_mm"] == pytest.approx(58)
    assert result["metrics"]["bias_mm"] == pytest.approx(prediction-58)
    assert result["independence_status"] == "not_established"
    assert result["declaration"]["dataset_kind"] == "synthetic"
    assert result["calculation"] == child
    assert result["groups"][0]["quality"] == "unverified"
    assert "before the same-day state update" in render_soil_comparison(result)


def test_known_prior_target_reuse_is_visible_but_excluded_from_metrics(setup):
    workflow, store = setup
    stamp = datetime(2026, 7, 7, tzinfo=timezone.utc)
    store.record_observation("owner", Observation("soil", "owner", "field", "cycle", "soil_depletion", 40, "mm",
        stamp, stamp, "measured", "Synthetic midnight measurement"))
    base = workflow.save("owner", "field", "root_zone_budget", series(), request_id="base", cycle_id="cycle")
    child = workflow.apply_soil_state("owner", "field", **adoption(base, depletion_adjustment_mm=0))
    report = compare(workflow, child, [entry(as_of="2026-07-07", depletion_adjustment_mm=0)])
    assert report["excluded_count"] == 1
    assert report["comparisons"][0]["known_target_reuse_dates"] == ["2026-07-06"]
    assert report["comparisons"][0]["prior_state_update_dates"] == ["2026-07-06"]
    assert report["metrics"]["count"] == 0 and report["metrics"]["rmse_mm"] is None
    assert "No eligible comparisons" in render_soil_comparison(report)


def test_measurement_review_changes_are_not_silently_used(setup):
    workflow, store = setup
    measurement(store)
    base = workflow.save("owner", "field", "root_zone_budget", inputs(), request_id="base", cycle_id="cycle")
    reviews = ObservationReviews(workflow.db)
    review = reviews.review("owner", "field", "soil", "checked", "Inspected", "Synthetic", request_id="checked", expected_revision=None)
    with pytest.raises(ValueError, match="review changed"):
        compare(workflow, base)
    report = compare(workflow, base, [entry(expected_review=review["id"])])
    assert report["groups"][0]["quality"] == "checked"
    rejected = reviews.review("owner", "field", "soil", "rejected", "Calibration issue", "Synthetic", request_id="reject", expected_revision=review["id"])
    with pytest.raises(ValueError, match="quality"):
        compare(workflow, base, [entry(expected_review=rejected["id"])])


@pytest.mark.parametrize("case", ["foreign", "cycle", "forecast", "depth", "date", "duplicate", "no_prediction", "timezone", "undeclared"])
def test_invalid_comparisons_never_mutate_the_farm(setup, case):
    workflow, store = setup
    if case == "cycle":
        store.create_cycle("owner", ProductionCycle("other-cycle", "field", "tomato", "2026-07-01"))
    measurement(store, unit="field2" if case == "foreign" else "field", cycle="cycle2" if case == "foreign" else "other-cycle" if case == "cycle" else "cycle",
                provenance="forecast" if case == "forecast" else "measured")
    data = inputs()
    if case == "no_prediction":
        data.pop("initial_state")
    base = workflow.save("owner", "field", "root_zone_budget", data, request_id="base", cycle_id="cycle")
    if case == "no_prediction":
        base = workflow.apply_soil_state("owner", "field", **adoption(base))
    entries, kwargs = [entry()], {}
    if case == "depth":
        entries[0]["represented_depth_m"] = .2
    elif case == "date":
        entries[0]["as_of"] = "2026-07-09"
    elif case == "duplicate":
        entries *= 2
    elif case == "timezone":
        workflow.db.execute("UPDATE farms SET timezone='Asia/Taipei' WHERE id='farm'")
    elif case == "undeclared":
        kwargs["dataset_kind"] = None
    before = store.export("owner")
    with pytest.raises(ValueError):
        compare(workflow, base, entries, **kwargs)
    assert store.export("owner") == before
    with pytest.raises(ValueError):
        workflow.compare_soil("other", "field", calculation_id=base["id"], comparisons=entries, dataset_kind="synthetic", selection_note="Synthetic")


def test_each_prediction_uses_its_own_root_depth(setup):
    workflow, store = setup
    stamp = datetime(2026, 7, 7, 18, tzinfo=timezone.utc)
    store.record_observation("owner", Observation("soil", "owner", "field", "cycle", "soil_water_content", .25, "m3/m3",
        stamp, stamp, "measured", "Synthetic complete expanded root zone"))
    base = workflow.save("owner", "field", "root_zone_budget", growing(), request_id="base", cycle_id="cycle")
    with pytest.raises(ValueError, match="depth"):
        compare(workflow, base, [entry(as_of="2026-07-07")])
    result = compare(workflow, base, [entry(as_of="2026-07-07", represented_depth_m=.9)])
    assert result["comparisons"][0]["values"]["reference_depletion_mm"] == pytest.approx(65)


def test_provenance_quality_and_forecast_groups_remain_distinct(setup):
    workflow, store = setup
    measurement(store)
    stamp = datetime(2026, 7, 7, 18, tzinfo=timezone.utc)
    store.record_observation("owner", Observation("second", "owner", "field", "cycle", "soil_depletion", 40, "mm",
        stamp, stamp, "reported", "Synthetic second reading", "checked"))
    data = series()
    data["weather"][1].update(kind="forecast", issued_at="2026-07-05T12:00:00Z")
    base = workflow.save("owner", "field", "root_zone_budget", data, request_id="base", cycle_id="cycle")
    report = compare(workflow, base, [entry(), entry(observation_id="second", as_of="2026-07-07")])
    assert report["metrics"]["count"] == 2
    assert {(group["provenance"], group["quality"], group["weather_kind"], group["metrics"]["count"]) for group in report["groups"]} == {
        ("measured", "unverified", "observed", 1), ("reported", "checked", "forecast", 1)}
    assert report["comparisons"][1]["values"]["reference_depletion_mm"] == 42
    assert report["independence_status"] == "not_established"


def test_stdlib_cli_generates_diagnostic_without_saving_a_calculation(tmp_path):
    root = Path(__file__).resolve().parents[2]
    db, path = tmp_path/"pilot.sqlite3", tmp_path/"request.json"
    def command(group, operation, data):
        path.write_text(json.dumps(data))
        result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", group, "--db", str(db), operation, "--input", str(path)],
            cwd=root, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout+result.stderr
        return json.loads(result.stdout)
    command("farm", "create", json.loads((root/"examples/soil_unit.json").read_text()))
    data = inputs()
    data["site"]["timezone"] = "Africa/Accra"
    saved = command("field", "save", dict(task="root_zone_budget", inputs=data, request_id="base"))
    reading = command("crop", "record_measurement", dict(request_id="reading", data=dict(quantity="soil_water_content", value=.25, unit="m3/m3",
        observed_at="2026-07-06T18:00:00+00:00", provenance="measured", source="Synthetic CLI observation")))
    result = command("field", "compare_soil", dict(calculation_id=saved["id"], comparisons=[entry(observation_id=reading["id"])],
        dataset_kind="synthetic", selection_note="Synthetic CLI fixture"))
    assert result["metrics"]["count"] == 1 and result["independence_status"] == "not_established"
    assert len(command("field", "list", {})["calculations"]) == 1
