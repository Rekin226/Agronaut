"""Recorded daily water drives surface demand without rewriting meteorology."""

import copy
import json
import subprocess
import sys

import pytest

from agro_model.tests.test_evaporation import example
from agronaut_agent.domains import capabilities
from agronaut_agent.evaporation_io import (
    evaluate_evaporation_request,
    render_evaporation_report,
)
from agronaut_agent.field_workflow import execute_field_workflow
from agronaut_agent.tests.test_evaporation_weather import provider_inputs
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_field_workflow import water
from agronaut_agent.weather_provider import adapt_response


def start(workflow):
    parent = workflow.save("owner", "field", "dual_crop_water", provider_inputs("2026-07-09T12:00:00+00:00"),
                           request_id="base", cycle_id="cycle")
    actual = workflow.record_day("owner", "field", water(provenance="measured", quality="checked"), request_id="actual", cycle_id="cycle")
    return parent, actual


def replay(workflow, parent, actual, request_id="replay"):
    return execute_field_workflow(workflow.db, "owner", "field", "replay", dict(calculation_id=parent["id"],
        water_record_ids=[actual["id"]], request_id=request_id, weather_policy="allow_historical"))


def test_replay_requires_historical_choice_and_preserves_all_other_evidence(setup, tmp_path):
    import jsonschema

    workflow, farms = setup
    parent, actual = start(workflow)
    before = farms.export("owner")
    with pytest.raises(ValueError, match="allow_historical"):
        workflow.replay("owner", "field", parent["id"], [actual["id"]], request_id="no-choice")
    child = replay(workflow, parent, actual)
    assert replay(workflow, parent, actual) == child
    assert child["parent_id"] == parent["id"] and child["applied_water_record_ids"] == [actual["id"]]
    for key in ("weather", "weather_sources", "crop", "surface_soil", "initial_state", "conditions"):
        assert child["inputs"][key] == parent["inputs"][key]
    for old, new in zip(parent["inputs"]["days"], child["inputs"]["days"]):
        assert {key: value for key, value in new.items() if key not in ("water_source", "net_irrigation_mm", "runoff_mm")} == {
            key: value for key, value in old.items() if key not in ("net_irrigation_mm", "runoff_mm")}
    row = child["result"]["result"]["days"][0]
    assert row["rainfall"]["rainfall_mm"] == 5 and child["inputs"]["weather"][0]["rainfall_mm"] == 12
    assert row["values"]["surface_wetting_mm"] == 14
    assert row["values"]["ending_depletion_mm"] == pytest.approx(13.8)
    assert row["values"]["basal_et_mm"] == parent["result"]["result"]["days"][0]["values"]["basal_et_mm"]
    report = render_evaporation_report(child["result"]["result"])
    assert "Rainfall used: 5 mm (measured, checked" in report and actual["id"] in report
    assert "Net irrigation 3 mm; runoff 1 mm" in report and "rain 12 mm" in report
    jsonschema.validate(child["inputs"], capabilities("soil")["soil"]["dual_crop_water"]["input_schema"])
    after = farms.export("owner")
    assert {key: value for key, value in after.items() if key != "calculations"} == {key: value for key, value in before.items() if key != "calculations"}
    assert workflow.get("owner", "field", parent["id"]) == parent
    again = replay(workflow, child, actual, "again")
    assert again["result"] == child["result"] and again["inputs"] == child["inputs"]
    path = tmp_path/"replay.json"
    path.write_text(json.dumps(child["inputs"]))
    result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", "dual-crop-water", "--input", str(path), "--json"],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout+result.stderr
    assert json.loads(result.stdout) == child["result"]["result"]
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())


def test_weather_refresh_retains_exact_applied_revision_until_correction_is_selected(setup):
    workflow, _ = setup
    parent, actual = start(workflow)
    child = replay(workflow, parent, actual)
    correction = workflow.record_day("owner", "field", water(rainfall_mm=8), request_id="correction", cycle_id="cycle", supersedes_id=actual["id"])
    source = copy.deepcopy(parent["inputs"]["weather_sources"][0])
    source["response"]["hourly"]["precipitation"] = [0]*72
    source["response"]["hourly"]["et0_fao_evapotranspiration"] = [0.5]*72
    weather = adapt_response(source["request"], source["response"], "2026-07-10T12:00:00+00:00")
    refreshed = workflow.revise_weather("owner", "field", child["id"], weather["weather"], request_id="refresh", weather_sources=weather["weather_sources"])
    assert refreshed["inputs"]["weather"] == weather["weather"]
    assert refreshed["inputs"]["rainfall_overrides"] == child["inputs"]["rainfall_overrides"]
    assert refreshed["applied_water_record_ids"] == [actual["id"]]
    assert refreshed["result"]["result"]["days"][0]["values"]["surface_wetting_mm"] == 14
    corrected = replay(workflow, refreshed, correction, "correction")
    assert corrected["applied_water_record_ids"] == [correction["id"]]
    assert corrected["inputs"]["rainfall_overrides"][0]["rainfall_mm"] == 8
    assert corrected["result"]["result"]["days"][0]["values"]["surface_wetting_mm"] == 17
    assert workflow.get("owner", "field", child["id"]) == child
    with pytest.raises(ValueError, match="one revision"):
        workflow.replay("owner", "field", parent["id"], [actual["id"], correction["id"]], request_id="both", weather_policy="allow_historical")


@pytest.mark.parametrize("kind", ["forecast", "scenario"])
def test_actual_water_cannot_adopt_forecast_or_scenario_weather(setup, kind):
    workflow, farms = setup
    parent, actual = start(workflow)
    data = example()
    for row in data["weather"]:
        row.update(kind=kind, issued_at="2026-07-05T12:00:00Z")
    proposed = workflow.save("owner", "field", "dual_crop_water", data, request_id="proposal", cycle_id="cycle")
    child = replay(workflow, parent, actual)
    before = farms.export("owner")
    with pytest.raises(ValueError, match="forecast and scenario"):
        replay(workflow, proposed, actual, "bad")
    with pytest.raises(ValueError, match="applied daily water requires"):
        workflow.revise_weather("owner", "field", child["id"], data["weather"], request_id="downgrade")
    assert farms.export("owner") == before


@pytest.mark.parametrize("case", ["owner", "unit", "cycle", "rejected", "outside", "forged_import"])
def test_foreign_rejected_or_forged_water_evidence_is_rejected_atomically(setup, case):
    workflow, farms = setup
    parent, actual = start(workflow)
    owner, unit = "owner", "field"
    if case == "owner":
        owner = "other"
    elif case == "unit":
        actual = workflow.record_day("owner", "field2", water(), request_id="other-unit", cycle_id="cycle2")
    elif case == "cycle":
        actual = workflow.record_day("owner", "field", water(), request_id="other-cycle")
    elif case == "rejected":
        actual = workflow.record_day("owner", "field", water(quality="rejected"), request_id="bad-quality", cycle_id="cycle", supersedes_id=actual["id"])
    elif case == "outside":
        actual = workflow.record_day("owner", "field", water(day="2026-07-09"), request_id="outside", cycle_id="cycle")
    if case == "forged_import":
        child = replay(workflow, parent, actual)
        before = farms.export("owner")
        with pytest.raises(ValueError, match="explicit replay"):
            workflow.save("owner", "field", "dual_crop_water", child["inputs"], request_id="forged", cycle_id="cycle")
    else:
        before = farms.export("owner")
        with pytest.raises(ValueError):
            workflow.replay(owner, unit, parent["id"], [actual["id"]], request_id="bad", weather_policy="allow_historical")
    assert farms.export("owner") == before


def test_observed_replay_needs_no_historical_policy_and_retains_unselected_days(setup):
    workflow, _ = setup
    data = example()
    for row in data["weather"]:
        row["kind"] = "observed"
    parent = workflow.save("owner", "field", "dual_crop_water", data, request_id="base", cycle_id="cycle")
    actual = workflow.record_day("owner", "field", water(), request_id="actual", cycle_id="cycle")
    child = workflow.replay("owner", "field", parent["id"], [actual["id"]], request_id="observed")
    assert child["inputs"]["days"][1:] == data["days"][1:]
    assert child["inputs"]["weather"] == data["weather"]


def test_unknown_manual_rain_with_an_override_remains_renderable_beside_provider_weather():
    data = provider_inputs("2026-07-09T12:00:00+00:00")
    data["weather"][0] = dict(example()["weather"][0], rainfall_mm=None)
    data["rainfall_overrides"] = [dict(day="2026-07-06", rainfall_mm=2, source="Synthetic separate rain", provenance="reported", quality="unverified")]
    result = evaluate_evaporation_request(data)
    assert result["days"][0]["rainfall"]["rainfall_mm"] == 2
    assert "rain unknown" in render_evaporation_report(result)
