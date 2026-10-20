"""Pair immutable owned predictions/events without selection by model fit."""

import json

import pytest

from agronaut_agent.field_workflow import execute_field_workflow
from agronaut_agent.tests.test_field_workflow import (
    setup,  # noqa: F401 — owned unit/cycle fixtures
)
from agronaut_agent.tests.test_surface_advance_workflow import inputs


def event_inputs(day="2026-07-07", half=25, full=70, kind="observed"):
    scenario = inputs()
    return dict(schema_version=1, site=scenario["site"], geometry=scenario["geometry"],
        event=dict(started_at=day+"T08:00:00+00:00", cutoff_min=100, monitoring_end_min=160,
                   rainfall_mm=0, initial_surface_water_l=0, surface_drained=True, kind=kind,
                   source="Synthetic event fixture, not a field measurement"),
        infiltration=scenario["infiltration"],
        inflow=[dict(start_min=0, end_min=100, mean_lps=2.5, source="Synthetic inflow"),
                dict(start_min=100, end_min=160, mean_lps=0, source="Synthetic cutoff")],
        tailwater=[dict(start_min=0, end_min=160, mean_lps=0, source="Synthetic no tailwater")],
        stations=[dict(distance_m=x, advance_min=t, recession_min=recession, source="Synthetic station timing")
                  for x, t, recession in [(0, 0, 110), (100, half, 120), (200, full, 150)]],
        assessment=dict(root_zone_deficit_mm=None, balance_tolerance_fraction=.1, source="Synthetic assessment"))


def save_pair(workflow, event=None):
    prediction = workflow.save("owner", "field", "surface_advance", inputs(), cycle_id="cycle", request_id="predict")
    reference = workflow.save("owner", "field", "surface_evaluation", event or event_inputs(), cycle_id="cycle", request_id="event")
    return prediction, reference


def request(prediction, *references, independence="held_out", dataset_kind="synthetic"):
    return dict(calculation_id=prediction["id"], dataset_kind=dataset_kind,
                selection_note="Synthetic comparison selection, not a validation study",
                comparisons=[dict(event_calculation_id=ref["id"], independence=independence,
                                  representation_note="Synthetic identical strip and advance conditions") for ref in references])


def compare(workflow, data):
    return execute_field_workflow(workflow.db, "owner", "field", "compare_surface_advance", data)


def test_owned_read_only_comparison_retains_full_evidence(setup):  # noqa: F811
    workflow, store = setup
    pred, event = save_pair(workflow)
    before = store.export("owner")
    report = compare(workflow, request(pred, event))
    assert store.export("owner") == before
    assert report["calculation"] == pred and report["comparisons"][0]["event_calculation"] == event
    expected = pred["result"]["result"]["prediction"]["full_length_advance_min"]-70
    assert report["metrics_by_anchor"]["full_length"]["bias_min"] == expected
    assert report["metrics_by_anchor"]["half_length"]["count"] == 1
    assert report["independence_status"] == "not_established"
    assert report["acceptance_status"] == "not_assessed" and report["evidence_status"] == "diagnostic"
    json.dumps(report, allow_nan=False)


def test_bad_post_event_model_balance_does_not_filter_arrival_observations(setup):  # noqa: F811
    workflow, _ = setup
    data = event_inputs()
    data["infiltration"]["k"] = 1
    pred, event = save_pair(workflow, data)
    assert not event["result"]["result"]["water_balance_accepted"]
    report = compare(workflow, request(pred, event))
    assert report["metrics_by_anchor"]["full_length"]["count"] == 1
    assert report["comparisons"][0]["reference_water_balance_accepted"] is False


@pytest.mark.parametrize("position", [None, 100.0001])
def test_missing_exact_half_station_does_not_change_full_length_weight(setup, position):  # noqa: F811
    workflow, _ = setup
    data = event_inputs()
    if position is None:
        data["stations"].pop(1)
    else:
        data["stations"][1]["distance_m"] = position
    pred, event = save_pair(workflow, data)
    report = compare(workflow, request(pred, event))
    assert report["metrics_by_anchor"]["half_length"]["count"] == 0
    assert report["metrics_by_anchor"]["half_length"]["rmse_min"] is None
    assert report["metrics_by_anchor"]["full_length"]["count"] == 1
    assert report["comparisons"][0]["anchors"][0]["status"] == "missing_exact_station"


@pytest.mark.parametrize("independence", ["unknown", "used_for_calibration"])
def test_nonheldout_events_are_visible_but_excluded_from_metrics(setup, independence):  # noqa: F811
    workflow, _ = setup
    pred, event = save_pair(workflow)
    report = compare(workflow, request(pred, event, independence=independence))
    assert report["included_event_count"] == 0
    assert report["comparisons"][0]["anchors"][1]["error_min"] is not None
    assert report["comparisons"][0]["exclusion_reason"] == independence
    assert report["metrics_by_anchor"]["full_length"]["count"] == 0


def test_slower_prediction_is_not_excluded_when_actual_cutoff_precedes_it(setup):  # noqa: F811
    workflow, _ = setup
    data = event_inputs(half=20, full=60)
    data["event"]["cutoff_min"] = 60
    data["inflow"][0]["end_min"] = data["inflow"][1]["start_min"] = 60
    pred, event = save_pair(workflow, data)
    report = compare(workflow, request(pred, event))
    assert report["metrics_by_anchor"]["full_length"]["bias_min"] > 5
    assert report["metrics_by_anchor"]["full_length"]["count"] == 1


@pytest.mark.parametrize("change", [
    lambda e: e["geometry"].update(slope_m_m=.002),
    lambda e: e["geometry"].update(width_m=2),
    lambda e: e["inflow"][0].update(mean_lps=2.4),
    lambda e: e["stations"][-1].update(advance_min=105),
])
def test_incompatible_geometry_or_inflow_is_not_scored(setup, change):  # noqa: F811
    workflow, _ = setup
    data = event_inputs()
    change(data)
    pred, event = save_pair(workflow, data)
    with pytest.raises(ValueError):
        compare(workflow, request(pred, event))


def test_scenario_reference_cannot_be_promoted_to_a_field_observation(setup):  # noqa: F811
    workflow, _ = setup
    pred, event = save_pair(workflow, event_inputs(kind="scenario"))
    with pytest.raises(ValueError, match="observed events"):
        compare(workflow, request(pred, event, dataset_kind="field"))
    assert compare(workflow, request(pred, event))["included_event_count"] == 1


def test_event_duplicates_are_detected_across_evaluation_ids_and_time_offsets(setup):  # noqa: F811
    workflow, _ = setup
    pred, event = save_pair(workflow)
    with pytest.raises(ValueError, match="only once"):
        compare(workflow, request(pred, event, event))
    other = event_inputs()
    other["event"]["started_at"] = "2026-07-07T16:00:00+08:00"
    other["infiltration"]["k"] = .004
    alternate = workflow.save("owner", "field", "surface_evaluation", other, cycle_id="cycle", request_id="alternate")
    with pytest.raises(ValueError, match="same event start"):
        compare(workflow, request(pred, event, alternate))


def test_reference_cannot_cross_owner_unit_or_cycle(setup):  # noqa: F811
    workflow, _ = setup
    pred, event = save_pair(workflow)
    other = workflow.save("owner", "field2", "surface_evaluation", event_inputs(), cycle_id="cycle2", request_id="other")
    with pytest.raises(ValueError, match="selected unit"):
        compare(workflow, request(pred, other))
    no_cycle = workflow.save("owner", "field", "surface_evaluation", event_inputs(), request_id="no-cycle")
    with pytest.raises(ValueError, match="same unit and crop cycle"):
        compare(workflow, request(pred, no_cycle))
    with pytest.raises(ValueError):
        workflow.compare_surface_advance("other", "field", **request(pred, event))


def test_current_timezone_and_cycle_bounds_are_checked_again(setup):  # noqa: F811
    workflow, _ = setup
    pred, event = save_pair(workflow)
    with workflow.db.transaction() as conn:
        conn.execute("UPDATE farms SET timezone='Asia/Taipei' WHERE id='farm'")
    with pytest.raises(ValueError, match="timezone"):
        compare(workflow, request(pred, event))
    with workflow.db.transaction() as conn:
        conn.execute("UPDATE farms SET timezone='UTC' WHERE id='farm'")
        conn.execute("UPDATE production_cycles SET planted_on='2026-07-08' WHERE id='cycle'")
    with pytest.raises(ValueError):
        compare(workflow, request(pred, event))


def test_monitoring_end_must_fit_current_cycle_closure(setup):  # noqa: F811
    from agronaut_agent.crop_records import CropRecords

    workflow, _ = setup
    data = event_inputs()
    data["event"]["started_at"] = "2026-07-07T23:00:00+00:00"
    pred, event = save_pair(workflow, data)
    CropRecords(workflow.db).close_cycle("owner", "field", "cycle", "2026-07-07",
        "Synthetic closure", "Synthetic notebook", request_id="close", expected_revision=None)
    with pytest.raises(ValueError, match="after the crop cycle end"):
        compare(workflow, request(pred, event))


def test_multiple_events_are_equal_weight_per_anchor(setup):  # noqa: F811
    workflow, _ = setup
    pred, first = save_pair(workflow)
    second = workflow.save("owner", "field", "surface_evaluation", event_inputs("2026-07-08", half=20, full=60),
                           cycle_id="cycle", request_id="second")
    report = compare(workflow, request(pred, second, first))
    expected = pred["result"]["result"]["prediction"]["full_length_advance_min"]-65
    assert report["metrics_by_anchor"]["full_length"]["bias_min"] == pytest.approx(expected)
    assert report["metrics_by_anchor"]["full_length"]["count"] == 2
    assert report["comparisons"][0]["event_calculation"]["id"] == first["id"]


@pytest.mark.parametrize("cutback_min, accepted", [(69, False), (70, True)])
def test_flow_changes_after_observed_advance_do_not_exclude_events(setup, cutback_min, accepted):  # noqa: F811
    workflow, _ = setup
    data = event_inputs()
    data["inflow"][0]["end_min"] = cutback_min
    data["inflow"].insert(1, dict(start_min=cutback_min, end_min=100, mean_lps=1, source="Synthetic cutback"))
    pred, event = save_pair(workflow, data)
    if accepted:
        assert compare(workflow, request(pred, event))["included_event_count"] == 1
    else:
        with pytest.raises(ValueError, match="inflow"):
            compare(workflow, request(pred, event))


def test_unsolved_prediction_is_not_scored(setup):  # noqa: F811
    workflow, _ = setup
    _, event = save_pair(workflow)
    data = inputs()
    data["limits"]["max_advance_min"] = 1
    pred = workflow.save("owner", "field", "surface_advance", data, cycle_id="cycle", request_id="unsolved")
    with pytest.raises(ValueError, match="solved advance"):
        compare(workflow, request(pred, event))


@pytest.mark.parametrize("change", [
    lambda r: r.update(comparisons=[]),
    lambda r: r.update(selection_note=""),
    lambda r: r.update(dataset_kind="validated"),
    lambda r: r["comparisons"][0].update(independence=None),
    lambda r: r["comparisons"][0].update(representation_note=""),
    lambda r: r["comparisons"][0].update(accept=True),
])
def test_required_declarations_are_never_inferred(setup, change):  # noqa: F811
    workflow, _ = setup
    pred, event = save_pair(workflow)
    data = request(pred, event)
    change(data)
    with pytest.raises((TypeError, ValueError)):
        compare(workflow, data)


def test_stdlib_cli_can_compare_saved_events_without_loading_a_model(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    db, path = tmp_path/"cli.sqlite3", tmp_path/"request.json"

    def command(*args):
        result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", *args], cwd=root,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout+result.stderr
        return json.loads(result.stdout)

    unit = json.loads((root/"examples/soil_unit.json").read_text())
    unit["farm"]["timezone"] = "UTC"
    path.write_text(json.dumps(unit))
    command("farm", "--db", str(db), "create", "--input", str(path))

    def operation(name, data):
        path.write_text(json.dumps(data))
        return command("field", "--db", str(db), name, "--input", str(path))

    pred = operation("save", dict(task="surface_advance", inputs=inputs(), request_id="prediction"))
    event = operation("save", dict(task="surface_evaluation", inputs=event_inputs(), request_id="event"))
    report = operation("compare_surface_advance", request(pred, event))
    assert report["metrics_by_anchor"]["full_length"]["count"] == 1
    assert len(command("field", "--db", str(db), "list")["calculations"]) == 2


def test_agent_tool_routes_read_only_comparison_to_selected_unit(tmp_path):
    from langchain_core.messages import AIMessage

    from agronaut_agent.tests.test_unit_agent import agent_and_units

    agent, _ = agent_and_units(tmp_path)
    agent.select_unit("cli", "one", "field")
    pred = agent.field_workflow("cli", "one", "save", dict(task="surface_advance", inputs=inputs(), request_id="prediction"))
    event = agent.field_workflow("cli", "one", "save", dict(task="surface_evaluation", inputs=event_inputs(), request_id="event"))
    before = agent.field_workflow("cli", "one", "list", {})
    agent._base.replies = [AIMessage(content="", tool_calls=[dict(name="field_records", id="advance-compare",
        args=dict(operation="compare_surface_advance", data=request(pred, event)))]), AIMessage(content="Comparison ready.")]
    agent.handle_message("cli", "one", "Compare these supplied synthetic events with my saved advance scenario")
    report = json.loads(agent.tool_results("cli", "one", "field_records")[-1])
    assert report["evidence_status"] == "diagnostic"
    assert report["metrics_by_anchor"]["full_length"]["count"] == 1
    assert agent.field_workflow("cli", "one", "list", {}) == before
