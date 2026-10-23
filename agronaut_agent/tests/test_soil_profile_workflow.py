"""Scoped layered-soil calculations and offline discovery with preserved evidence."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agro_model.soil_profile import evaluate_soil_profile
from agro_model.tests.test_soil_profile import example
from agronaut_agent.crop_records import CropRecords
from agronaut_agent.domains import capabilities, run_capability
from agronaut_agent.irrigation_zones import IrrigationZones
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_irrigation_zones import zone


def test_schema_adapter_and_offline_cli_agree_with_direct_calculation(tmp_path):
    import jsonschema

    data = example()
    cap = capabilities("soil")["soil"]["soil_profile"]
    jsonschema.validate(data, cap["input_schema"])
    assert cap["status"] == "experimental"
    assert run_capability("soil", "soil_profile", data)["result"] == evaluate_soil_profile(data)
    for domain in ("aquaponics", "hydroponics"):
        with pytest.raises(ValueError):
            run_capability(domain, "soil_profile", data)
    path = tmp_path/"profile.json"
    path.write_text(json.dumps(data))
    cmd = [sys.executable, "-S", "-m", "agronaut_agent.cli", "evaluate-soil-profile", "--input", str(path)]
    process = subprocess.run([*cmd, "--json"], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stdout+process.stderr
    assert json.loads(process.stdout) == evaluate_soil_profile(data)
    data["layers"][1]["water_content"] = None
    jsonschema.validate(data, cap["input_schema"])
    path.write_text(json.dumps(data))
    process = subprocess.run(cmd, cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert process.returncode == 0 and "unknown / withheld" in process.stdout


def test_save_retry_ownership_export_and_erasure_without_state_adoption(setup):
    workflow, farms = setup
    data = example()
    data["profile"]["kind"] = "observed"
    before = farms.export("owner")
    saved = workflow.save("owner", "field", "soil_profile", data, cycle_id="cycle", request_id="profile")
    assert workflow.save("owner", "field", "soil_profile", data, cycle_id="cycle", request_id="profile") == saved
    assert saved["inputs"] == data and saved["result"]["result"]["inputs"] == data
    after = farms.export("owner")
    assert after["calculations"] == [saved]
    assert {key:value for key,value in before.items() if key != "calculations"} == {
        key:value for key,value in after.items() if key != "calculations"}
    for owner, unit in (("other", "field"), ("owner", "field2")):
        with pytest.raises(ValueError):
            workflow.get(owner, unit, saved["id"])
    data["layers"][0]["quality"] = "rejected"
    with pytest.raises(ValueError, match="different data"):
        workflow.save("owner", "field", "soil_profile", data, cycle_id="cycle", request_id="profile")
    updated = workflow.save("owner", "field", "soil_profile", data, cycle_id="cycle", request_id="reassessment")
    assert updated["result"]["result"]["root_zone"]["bounded_depletion_mm"] is None
    assert workflow.get("owner", "field", saved["id"]) == saved
    other = farms.export("other")
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())
    assert farms.export("other") == other


@pytest.mark.parametrize("kind", ["timezone", "preplant", "closed", "wrong_cycle", "invalid"])
def test_scoped_time_and_cycle_validation_roll_back_invalid_saves(setup, kind):
    workflow, farms = setup
    data = example()
    cycle_id = "cycle"
    if kind == "timezone":
        data["site"]["timezone"] = "Africa/Accra"  # Same offset still does not match the farm contract.
    elif kind == "preplant":
        data["profile"]["observed_at"] = "2026-06-30T23:59:00Z"
    elif kind == "closed":
        CropRecords(workflow.db).close_cycle("owner", "field", "cycle", "2026-07-05", "Ended", "Synthetic", request_id="end", expected_revision=None)
    elif kind == "wrong_cycle":
        cycle_id = "cycle2"
    else:
        data["layers"][0]["bottom_m"] = "0.3"
    before = farms.export("owner")
    with pytest.raises(ValueError):
        workflow.save("owner", "field", "soil_profile", data, cycle_id=cycle_id, request_id="bad")
    assert farms.export("owner") == before


def test_zone_profile_does_not_change_parent_records_and_season_review_flags_changed_context(setup):
    workflow, _ = setup
    child = IrrigationZones(workflow.db).create("owner", "field", zone(), request_id="zone")
    saved = workflow.save("owner", child["id"], "soil_profile", example(), request_id="zone-profile")
    assert saved["zone_snapshot"] == child
    assert workflow.list("owner", "field")["calculations"] == []
    with pytest.raises(ValueError):
        workflow.get("owner", "field", saved["id"])
    parent_profile = workflow.save("owner", "field", "soil_profile", example(), cycle_id="cycle", request_id="field-profile")
    crops = CropRecords(workflow.db)
    crops.correct_planting("owner", "field", "cycle", "pepper", "2026-07-08", "Synthetic", "Correction", request_id="correct", expected_revision=None)
    workflow.db.execute("UPDATE farms SET timezone='Pacific/Honolulu' WHERE id='farm'")
    summary = crops.season_review("owner", "field", "cycle")["calculations"][0]
    assert summary["task"] == "soil_profile" and len(summary["cycle_conflicts"]) == 2
    assert workflow.get("owner", "field", parent_profile["id"]) == parent_profile


def test_later_cycle_closure_flags_profile_without_rewriting_or_deleting_it(setup):
    workflow, _ = setup
    saved = workflow.save("owner", "field", "soil_profile", example(), cycle_id="cycle", request_id="profile")
    crops = CropRecords(workflow.db)
    crops.close_cycle("owner", "field", "cycle", "2026-07-05", "Ended earlier", "Synthetic", request_id="end", expected_revision=None)
    summary = crops.season_review("owner", "field", "cycle")["calculations"][0]
    assert summary["cycle_conflicts"] == ["soil profile is after the current crop cycle end"]
    assert workflow.get("owner", "field", saved["id"]) == saved
