"""Emitter evaluations preserve scope and source evidence through save and CLI."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agro_model.emitter_test import evaluate_emitter_test
from agro_model.tests.test_emitter_test import example
from agronaut_agent.crop_records import CropRecords
from agronaut_agent.domains import capabilities, run_capability
from agronaut_agent.tests.test_field_workflow import setup as setup


def test_schema_adapter_and_offline_cli_keep_individual_collection_times(tmp_path):
    import jsonschema

    data = example()
    cap = capabilities("soil")["soil"]["drip_emitter_test"]
    jsonschema.validate(data, cap["input_schema"])
    assert cap["status"] == "experimental"
    assert run_capability("soil", "drip_emitter_test", data)["result"] == evaluate_emitter_test(data)
    path = tmp_path/"emitters.json"
    path.write_text(json.dumps(data))
    cmd = [sys.executable, "-S", "-m", "agronaut_agent.cli", "evaluate-drip-emitters", "--input", str(path)]
    result = subprocess.run([*cmd, "--json"], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["statistics"]["mean_flow_l_h"] == 3.9
    data["samples"][0]["volume_ml"] = None
    path.write_text(json.dumps(data))
    result = subprocess.run(cmd, cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and "unknown / undefined" in result.stdout
    for domain in ("hydroponics", "aquaponics"):
        with pytest.raises(ValueError):
            run_capability(domain, "drip_emitter_test", data)


def test_save_retry_ownership_and_export_preserve_sources_without_actuals(setup):
    workflow, farms = setup
    data = example()
    data["event"]["kind"] = "observed"
    before = farms.export("owner")
    saved = workflow.save("owner", "field", "drip_emitter_test", data, cycle_id="cycle", request_id="emitters")
    assert workflow.save("owner", "field", "drip_emitter_test", data, cycle_id="cycle", request_id="emitters") == saved
    assert saved["inputs"] == data
    assert saved["result"]["result"]["samples"][0]["source"] == data["samples"][0]["source"]
    after = farms.export("owner")
    assert after["calculations"] == [saved]
    assert {k:v for k,v in before.items() if k != "calculations"} == {k:v for k,v in after.items() if k != "calculations"}
    for owner, unit in (("other", "field"), ("owner", "field2")):
        with pytest.raises(ValueError):
            workflow.get(owner, unit, saved["id"])
    with pytest.raises(ValueError):
        workflow.save("owner", "field", "drip_emitter_test", data, cycle_id="cycle2", request_id="wrong-cycle")
    other = farms.export("other")
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())
    assert farms.export("other") == other


@pytest.mark.parametrize("kind", ["timezone", "preplant", "after_closure", "crosses_closure"])
def test_farm_timezone_and_entire_test_window_must_fit_assigned_cycle(setup, kind):
    workflow, farms = setup
    data = example()
    if kind == "timezone":
        data["site"]["timezone"] = "Pacific/Honolulu"
    elif kind == "preplant":
        data["event"].update(started_at="2026-06-30T23:59:00Z", ended_at="2026-07-01T00:30:00Z")
    else:
        CropRecords(workflow.db).close_cycle("owner", "field", "cycle", "2026-07-06", "Ended", "Synthetic", request_id="end", expected_revision=None)
        if kind == "crosses_closure":
            data["event"].update(started_at="2026-07-06T23:59:00Z", ended_at="2026-07-07T00:30:00Z")
    before = farms.export("owner")
    with pytest.raises(ValueError):
        workflow.save("owner", "field", "drip_emitter_test", data, cycle_id="cycle", request_id="bad")
    assert farms.export("owner") == before


def test_season_history_flags_changed_assumptions_without_rewriting_original(setup):
    workflow, _ = setup
    saved = workflow.save("owner", "field", "drip_emitter_test", example(), cycle_id="cycle", request_id="emitters")
    records = CropRecords(workflow.db)
    records.correct_planting("owner", "field", "cycle", "pepper", "2026-07-08", "Synthetic", "Correct date", request_id="correct", expected_revision=None)
    workflow.db.execute("UPDATE farms SET timezone='Pacific/Honolulu' WHERE id='farm'")
    summary = records.season_review("owner", "field", "cycle")["calculations"][0]
    assert summary["task"] == "drip_emitter_test" and len(summary["cycle_conflicts"]) == 2
    assert workflow.get("owner", "field", saved["id"]) == saved
