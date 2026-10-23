"""Surface-demand calculations preserve scope, provenance and existing root states."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agro_model.tests.test_evaporation import example
from agronaut_agent.crop_records import CropRecords
from agronaut_agent.domains import capabilities, run_capability
from agronaut_agent.evaporation_io import evaluate_evaporation_request
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_field_workflow import water


def test_discovery_schema_adapter_and_stdlib_cli(tmp_path):
    import jsonschema

    data = example()
    cap = capabilities("soil")["soil"]["dual_crop_water"]
    jsonschema.validate(data, cap["input_schema"])
    assert cap["status"] == "experimental"
    result = run_capability("soil", "dual_crop_water", data)
    assert result["result"] == evaluate_evaporation_request(data)
    assert result["feasible"] is None and result["binding_constraints"] == []
    path = tmp_path/"dual.json"
    path.write_text(json.dumps(data))
    cmd = [sys.executable, "-S", "-m", "agronaut_agent.cli", "dual-crop-water", "--input", str(path)]
    output = subprocess.run([*cmd, "--json"], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert output.returncode == 0, output.stdout+output.stderr
    assert json.loads(output.stdout) == result["result"]
    output = subprocess.run(cmd, cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert output.returncode == 0 and "limited by remaining surface water" in output.stdout
    for system in ("hydroponics", "aquaponics"):
        with pytest.raises(ValueError):
            run_capability(system, "dual_crop_water", data)


def test_owned_save_retry_exports_sources_and_does_not_create_actuals_or_replay_into_root_budget(setup):
    workflow, farms = setup
    data = example()
    before = farms.export("owner")
    saved = workflow.save("owner", "field", "dual_crop_water", data, cycle_id="cycle", request_id="dual")
    assert workflow.save("owner", "field", "dual_crop_water", data, cycle_id="cycle", request_id="dual") == saved
    assert saved["inputs"] == data
    after = farms.export("owner")
    assert {k:v for k,v in after.items() if k != "calculations"} == {k:v for k,v in before.items() if k != "calculations"}
    assert after["calculations"] == [saved]
    for owner, unit in (("other", "field"), ("owner", "field2")):
        with pytest.raises(ValueError):
            workflow.get(owner, unit, saved["id"])
    changed = copy.deepcopy(data)
    changed["days"][0]["net_irrigation_mm"] = 15
    with pytest.raises(ValueError, match="different data"):
        workflow.save("owner", "field", "dual_crop_water", changed, cycle_id="cycle", request_id="dual")
    record = workflow.record_day("owner", "field", water(), cycle_id="cycle", request_id="actual")
    with pytest.raises(ValueError, match="forecast and scenario"):
        workflow.replay("owner", "field", saved["id"], [record["id"]], request_id="wrong-model")
    assert workflow.get("owner", "field", saved["id"]) == saved
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())


@pytest.mark.parametrize("kind", ["crop", "timezone", "cycle", "closure", "preplant"])
def test_foreign_or_inconsistent_cycle_inputs_leave_no_calculation(setup, kind):
    workflow, farms = setup
    data = example()
    cycle = "cycle"
    if kind == "crop":
        data["crop"]["name"] = "pepper"
    elif kind == "timezone":
        data["site"]["timezone"] = "Africa/Accra"
        for row in data["weather"]:
            row["timezone"] = "Africa/Accra"
    elif kind == "cycle":
        cycle = "cycle2"
    elif kind == "closure":
        CropRecords(workflow.db).close_cycle("owner", "field", "cycle", "2026-07-07", "Ended", "Synthetic", request_id="close", expected_revision=None)
    else:
        workflow.db.execute("UPDATE production_cycles SET planted_on='2026-07-07' WHERE id='cycle'")
    before = farms.export("owner")
    with pytest.raises(ValueError):
        workflow.save("owner", "field", "dual_crop_water", data, cycle_id=cycle, request_id="bad")
    assert farms.export("owner") == before


def test_season_context_changes_flag_saved_demand_without_recalculation(setup):
    workflow, _ = setup
    saved = workflow.save("owner", "field", "dual_crop_water", example(), cycle_id="cycle", request_id="dual")
    crops = CropRecords(workflow.db)
    crops.close_cycle("owner", "field", "cycle", "2026-07-07", "Earlier end", "Synthetic", request_id="end", expected_revision=None)
    summary = crops.season_review("owner", "field", "cycle")["calculations"][0]
    assert summary["cycle_conflicts"] == ["dual crop-water days extend after the current crop cycle end"]
    workflow.db.execute("UPDATE farms SET timezone='Africa/Accra' WHERE id='farm'")
    assert len(crops.season_review("owner", "field", "cycle")["calculations"][0]["cycle_conflicts"]) == 2
    assert workflow.get("owner", "field", saved["id"]) == saved


@pytest.mark.parametrize("key", ["basal_unstressed", "surface_transpiration_negligible", "no_mulch_or_frozen_soil"])
def test_unsupported_conditions_are_explicitly_rejected(key):
    data = example()
    data["conditions"][key] = False
    with pytest.raises(ValueError, match=key):
        evaluate_evaporation_request(data)


def test_forecast_labels_and_issuance_remain_distinct_from_observed_water():
    data = example()
    for row in data["weather"]:
        row["kind"] = "forecast"
    with pytest.raises(ValueError, match="forecast requires"):
        evaluate_evaporation_request(data)
    for row in data["weather"]:
        row["issued_at"] = "2026-07-05T12:00:00Z"
    result = evaluate_evaporation_request(data)
    assert all(row["weather_kind"] == "forecast" for row in result["days"])
    assert result["inputs"] == data
    data["weather"][0]["source_snapshot_id"] = "unowned-provider-snapshot"
    with pytest.raises(ValueError, match="retained provider snapshot"):
        evaluate_evaporation_request(data)


def test_reference_et_period_matches_dst_day_and_future_actuals_are_rejected():
    data = example()
    data["weather"] = data["weather"][:1]
    data["days"] = data["days"][:1]
    data["initial_state"]["as_of"] = "2026-10-31"
    data["site"]["timezone"] = "America/New_York"
    data["days"][0]["day"] = "2026-11-01"
    data["weather"][0].update(day="2026-11-01", timezone="America/New_York", period_hours=25)
    assert evaluate_evaporation_request(data)["days"][0]["day"] == "2026-11-01"
    data["weather"][0]["period_hours"] = 24
    with pytest.raises(ValueError, match="period_hours"):
        evaluate_evaporation_request(data)
    data["weather"][0].update(period_hours=24, day="2099-07-06", kind="observed")
    data["days"][0]["day"] = "2099-07-06"
    data["initial_state"]["as_of"] = "2099-07-05"
    with pytest.raises(ValueError, match="completed local day"):
        evaluate_evaporation_request(data)


def test_last_supported_calendar_date_does_not_escape_as_an_overflow():
    data = example()
    data["weather"] = [dict(data["weather"][0], day="9999-12-31")]
    data["days"] = [dict(data["days"][0], day="9999-12-31")]
    data["initial_state"]["as_of"] = "9999-12-30"
    with pytest.raises(ValueError, match="calendar boundaries"):
        evaluate_evaporation_request(data)
