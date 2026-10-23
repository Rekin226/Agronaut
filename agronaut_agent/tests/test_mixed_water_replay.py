"""Historical weather plus separately evidenced daily water, with immutable lineage."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agronaut_agent.field_io import evaluate_field_request, render_field_report
from agronaut_agent.tests.test_field_workflow import inputs, setup, water  # noqa: F401
from agronaut_agent.tests.test_weather_provider import bundle, response, weather_request
from agronaut_agent.weather_provider import adapt_response


def historical_inputs():
    data = inputs()
    b = bundle("2026-07-07T12:00:00+00:00")
    data.update({key: b[key] for key in ("weather", "weather_sources")})
    return data


def start(workflow):
    parent = workflow.save("owner", "field", "root_zone_budget", historical_inputs(), request_id="base", cycle_id="cycle")
    actual = workflow.record_day("owner", "field", water(provenance="measured", quality="checked"),
                                 request_id="actual", cycle_id="cycle")
    return parent, actual


def replay(workflow, parent, actual, request_id="mixed"):
    return workflow.replay("owner", "field", parent["id"], [actual["id"]], request_id=request_id,
                           weather_policy="allow_historical")


def test_historical_replay_is_explicit_preserves_provider_and_replaces_daily_totals(request):
    workflow, _ = request.getfixturevalue("setup")
    parent, actual = start(workflow)
    with pytest.raises(ValueError, match="allow_historical"):
        workflow.replay("owner", "field", parent["id"], [actual["id"]], request_id="no-choice")
    child = replay(workflow, parent, actual)
    assert child["inputs"]["weather"] == parent["inputs"]["weather"]
    assert child["inputs"]["weather_sources"] == parent["inputs"]["weather_sources"]
    assert child["inputs"]["weather"][0]["kind"] == "historical"
    assert child["inputs"]["weather"][0]["rainfall_mm"] == 12
    assert child["inputs"]["rainfall_overrides"][0] == dict(day="2026-07-06", rainfall_mm=5,
        source=actual["data"]["source"], provenance="measured", quality="checked", water_record_id=actual["id"])
    row = child["result"]["result"]["days"][0]
    assert row["balance"]["values"]["depletion_mm"] == pytest.approx(59.9)
    assert row["rainfall"]["rainfall_mm"] == 5
    assert row["demand"] == parent["result"]["result"]["days"][0]["demand"]
    assert actual["id"] in render_field_report(child["result"]["result"])
    assert "historical" in render_field_report(child["result"]["result"])
    assert "measured, checked" in render_field_report(child["result"]["result"])
    assert replay(workflow, parent, actual) == child
    with pytest.raises(ValueError, match="different data"):
        workflow.replay("owner", "field", parent["id"], [actual["id"]], request_id="mixed")
    again = replay(workflow, child, actual, "again")
    assert again["inputs"] == child["inputs"] and again["result"] == child["result"]
    assert workflow.get("owner", "field", parent["id"]) == parent
    assert len(workflow.list("owner", "field")["water_days"]) == 1


def test_weather_refresh_keeps_exact_applied_revision_until_explicit_correction(request):
    workflow, _ = request.getfixturevalue("setup")
    parent, actual = start(workflow)
    child = replay(workflow, parent, actual)
    correction = workflow.record_day("owner", "field", water(rainfall_mm=8),
        request_id="correction", cycle_id="cycle", supersedes_id=actual["id"])
    source = copy.deepcopy(parent["inputs"]["weather_sources"][0])
    source["response"]["hourly"]["et0_fao_evapotranspiration"] = [0.5] * 24
    source["response"]["hourly"]["precipitation"] = [0] * 24
    new = adapt_response(source["request"], source["response"], "2026-07-08T12:00:00+00:00")
    refreshed = workflow.revise_weather("owner", "field", child["id"], new["weather"],
                                       weather_sources=new["weather_sources"], request_id="refresh")
    assert refreshed["inputs"]["weather"] == new["weather"]
    assert refreshed["inputs"]["weather_sources"] == new["weather_sources"]
    assert refreshed["inputs"]["rainfall_overrides"] == child["inputs"]["rainfall_overrides"]
    assert refreshed["applied_water_record_ids"] == [actual["id"]]
    assert refreshed["result"]["result"]["days"][0]["balance"]["values"]["depletion_mm"] == pytest.approx(66.8)
    corrected = replay(workflow, refreshed, correction, "adopt-correction")
    assert corrected["applied_water_record_ids"] == [correction["id"]]
    assert corrected["inputs"]["rainfall_overrides"][0]["rainfall_mm"] == 8
    assert corrected["inputs"]["weather_sources"] == refreshed["inputs"]["weather_sources"]
    assert workflow.get("owner", "field", child["id"]) == child


@pytest.mark.parametrize("kind", ["forecast", "scenario"])
def test_explicit_historical_policy_does_not_authorize_forecast_or_scenario(request, kind):
    workflow, _ = request.getfixturevalue("setup")
    parent, actual = start(workflow)
    data = inputs()
    data["weather"][0].update(kind=kind, issued_at="2026-07-05T12:00:00Z")
    other = workflow.save("owner", "field", "root_zone_budget", data, request_id="other", cycle_id="cycle")
    with pytest.raises(ValueError, match="forecast and scenario"):
        replay(workflow, other, actual)
    mixed = replay(workflow, parent, actual)
    with pytest.raises(ValueError, match="applied daily water requires"):
        workflow.revise_weather("owner", "field", mixed["id"], data["weather"], request_id="downgrade")


def test_owned_log_references_cannot_be_forged_in_imports(request):
    workflow, _ = request.getfixturevalue("setup")
    parent, actual = start(workflow)
    child = replay(workflow, parent, actual)
    for record_id in (actual["id"], "other-unit-record", "nonexistent"):
        data = copy.deepcopy(child["inputs"])
        data["rainfall_overrides"][0]["water_record_id"] = record_id
        with pytest.raises(ValueError, match="require explicit replay"):
            workflow.save("owner", "field", "root_zone_budget", data, request_id="forged", cycle_id="cycle")


def test_replay_rejects_other_cycle_unit_and_rejected_quality_under_historical_policy(request):
    workflow, _ = request.getfixturevalue("setup")
    parent, actual = start(workflow)
    wrong_cycle = workflow.record_day("owner", "field", water(), request_id="wrong-cycle")
    wrong_unit = workflow.record_day("owner", "field2", water(), request_id="wrong-unit")
    rejected = workflow.record_day("owner", "field", water(quality="rejected"), cycle_id="cycle",
                                   request_id="rejected", supersedes_id=actual["id"])
    for record in (wrong_cycle, wrong_unit, rejected):
        with pytest.raises(ValueError):
            replay(workflow, parent, record)
    assert len(workflow.list("owner", "field")["calculations"]) == 1


def test_missing_weather_rain_is_usable_only_with_separate_rain_and_runoff_stays_bounded():
    data = inputs()
    data["weather"][0]["rainfall_mm"] = None
    rain = dict(day="2026-07-06", rainfall_mm=2, source="Synthetic separate source",
                provenance="reported", quality="unverified")
    data["rainfall_overrides"] = [rain]
    result = evaluate_field_request(data, "irrigation-plan")
    assert result["days"][0]["rainfall"]["rainfall_mm"] == 2
    assert result["inputs"]["weather"][0]["rainfall_mm"] is None
    data["initial_state"] = None
    data["events"][0]["runoff_mm"] = 3
    with pytest.raises(ValueError, match="runoff"):
        evaluate_field_request(data, "irrigation-plan")


@pytest.mark.parametrize("case", ["duplicate", "outside", "null", "quality", "crop-demand"])
def test_override_import_rejects_ambiguous_or_ignored_data(case):
    data = inputs()
    rain = dict(day="2026-07-06", rainfall_mm=0, source="Synthetic rain", provenance="estimated", quality="unverified")
    data["rainfall_overrides"] = [rain]
    if case == "duplicate":
        data["rainfall_overrides"].append(dict(rain))
    elif case == "outside":
        rain["day"] = "2026-07-07"
    elif case == "null":
        rain["rainfall_mm"] = None
    elif case == "quality":
        rain["quality"] = "rejected"
    with pytest.raises(ValueError):
        evaluate_field_request(data, "water-needs" if case == "crop-demand" else "irrigation-plan")


def test_snapshot_with_separate_rain_replays_offline_under_stdlib(request, tmp_path):
    workflow, _ = request.getfixturevalue("setup")
    parent, actual = start(workflow)
    child = replay(workflow, parent, actual)
    path = tmp_path / "mixed-inputs.json"
    path.write_text(json.dumps(child["inputs"]))
    output = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", "irrigation-plan",
        "--input", str(path), "--json"], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert output.returncode == 0, output.stdout + output.stderr
    assert json.loads(output.stdout) == child["result"]["result"]


def test_old_saved_replay_remains_readable_and_new_version_adds_separate_provenance(request):
    workflow, _ = request.getfixturevalue("setup")
    base = workflow.save("owner", "field", "root_zone_budget", inputs(), request_id="old-base")
    actual = workflow.record_day("owner", "field", water(), request_id="old-actual")
    legacy = workflow.replay("owner", "field", base["id"], [actual["id"]], request_id="old-replay")
    # A persisted pre-separation payload: actual rainfall used to replace the weather value.
    legacy["inputs"].pop("rainfall_overrides")
    legacy["inputs"]["weather"][0]["rainfall_mm"] = actual["data"]["rainfall_mm"]
    legacy["inputs"]["weather"][0]["source"] += "; rainfall replaced from old daily record"
    legacy["result"]["result"] = evaluate_field_request(legacy["inputs"], "irrigation-plan")
    with workflow.db.transaction() as conn:
        conn.execute("UPDATE field_calculations SET payload=? WHERE id=?", (json.dumps(legacy), legacy["id"]))
    assert workflow.get("owner", "field", legacy["id"]) == legacy
    patch = [dict(inputs()["weather"][0], rainfall_mm=50)]
    updated = workflow.revise_weather("owner", "field", legacy["id"], patch, request_id="new-format")
    assert updated["inputs"]["weather"] == patch
    assert updated["inputs"]["rainfall_overrides"][0]["water_record_id"] == actual["id"]
    assert updated["result"]["result"]["days"][0]["balance"]["values"] == legacy["result"]["result"]["days"][0]["balance"]["values"]
    assert workflow.get("owner", "field", legacy["id"]) == legacy


def test_owned_cli_replay_requires_and_records_historical_policy(tmp_path):
    root = Path(__file__).resolve().parents[2]
    db = str(tmp_path / "workflow.sqlite3")
    def command(*args, status=0):
        result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", *args], cwd=root,
                                 capture_output=True, text=True, timeout=30)
        assert result.returncode == status, result.stdout + result.stderr
        return json.loads(result.stdout)
    spec = json.loads((root / "examples/soil_unit.json").read_text())
    spec["farm"]["timezone"] = "UTC"
    path = tmp_path / "operation.json"
    path.write_text(json.dumps(spec))
    command("farm", "--db", db, "create", "--input", str(path))
    def operation(name, data, status=0):
        path.write_text(json.dumps(data))
        return command("field", "--db", db, name, "--input", str(path), status=status)
    base = operation("save", dict(task="root_zone_budget", inputs=historical_inputs(), request_id="base"))
    actual = operation("record_day", dict(data=water(), request_id="actual"))
    request_data = dict(calculation_id=base["id"], water_record_ids=[actual["id"]], request_id="mixed")
    assert "allow_historical" in operation("replay", request_data, status=2)["error"]
    child = operation("replay", dict(request_data, weather_policy="allow_historical"))
    assert child["inputs"]["weather"] == base["inputs"]["weather"]
    assert child["inputs"]["rainfall_overrides"][0]["water_record_id"] == actual["id"]
    assert operation("replay", dict(request_data, weather_policy="allow_historical")) == child


def test_chat_replay_preserves_estimate_label_and_records_explicit_choice(tmp_path):
    from langchain_core.messages import AIMessage

    from agronaut_agent.tests.test_unit_agent import agent_and_units
    brain, _ = agent_and_units(tmp_path)
    brain.select_unit("cli", "one", "field")
    base = brain.field_workflow("cli", "one", "save", dict(task="root_zone_budget",
        inputs=historical_inputs(), request_id="base"))
    actual = brain.field_workflow("cli", "one", "record_day", dict(data=water(), request_id="actual"))
    data = dict(calculation_id=base["id"], water_record_ids=[actual["id"]], request_id="mixed",
                weather_policy="allow_historical")
    brain._base.replies = [AIMessage(content="", tool_calls=[dict(name="field_records", id="mixed-replay",
        args=dict(operation="replay", data=data))]), AIMessage(content="Saved with historical weather estimates.")]
    brain.handle_message("cli", "one", "Use these daily records with the historical weather estimates; retain their separate sources.")
    saved = json.loads(brain.tool_results("cli", "one", "field_records")[-1])
    assert saved["operation"]["weather_policy"] == "allow_historical"
    assert saved["inputs"]["weather"] == base["inputs"]["weather"]
    assert saved["inputs"]["rainfall_overrides"][0]["water_record_id"] == actual["id"]


def test_past_actuals_do_not_replace_following_forecast_or_assumed_events(request):
    workflow, _ = request.getfixturevalue("setup")
    spec = weather_request()
    spec["end"] = "2026-07-07"
    weather = adapt_response(spec, response(count=48), "2026-07-07T12:00:00+00:00")
    assert [w["kind"] for w in weather["weather"]] == ["historical", "forecast"]
    data = inputs()
    data.update({key: weather[key] for key in ("weather", "weather_sources")})
    future_event = dict(day="2026-07-07", net_irrigation_mm=0, runoff_mm=0, source="Explicit future assumptions")
    data["events"].append(future_event)
    base = workflow.save("owner", "field", "root_zone_budget", data, request_id="mixed-window")
    actual = workflow.record_day("owner", "field", water(), request_id="actual")
    child = replay(workflow, base, actual)
    assert child["inputs"]["events"][1] == future_event
    assert child["inputs"]["weather"] == base["inputs"]["weather"]
    assert [r["rainfall"]["rainfall_mm"] for r in child["result"]["result"]["days"]] == [5, 12]
    assert child["result"]["result"]["days"][1]["weather_kind"] == "forecast"
