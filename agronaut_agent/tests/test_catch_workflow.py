"""Catch-grid arithmetic reaches scoped persistence and the offline CLI."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agro_model.catch_can import evaluate_catch_test
from agro_model.tests.test_catch_can import example
from agronaut_agent.crop_records import CropRecords
from agronaut_agent.domains import capabilities, run_capability
from agronaut_agent.tests.test_field_workflow import setup as setup


def test_adapter_and_schema_preserve_native_evidence():
    import jsonschema

    data = example()
    schema = capabilities("soil")["soil"]["sprinkler_catch_test"]["input_schema"]
    jsonschema.validate(data, schema)
    result = run_capability("soil", "sprinkler_catch_test", data)
    assert result["result"] == evaluate_catch_test(data)
    assert result["capability"]["status"] == "experimental"
    for domain in ("hydroponics", "aquaponics"):
        with pytest.raises(ValueError):
            run_capability(domain, "sprinkler_catch_test", data)


def test_save_retry_scope_export_and_erasure_leave_actuals_unchanged(setup):
    workflow, farms = setup
    before = farms.export("owner")
    data = example()
    data["event"]["kind"] = "observed"
    saved = workflow.save("owner", "field", "sprinkler_catch_test", data, cycle_id="cycle", request_id="test")
    assert workflow.save("owner", "field", "sprinkler_catch_test", data, cycle_id="cycle", request_id="test") == saved
    assert workflow.get("owner", "field", saved["id"]) == saved
    after = farms.export("owner")
    assert after["calculations"] == [saved]
    assert {k:v for k,v in before.items() if k != "calculations"} == {k:v for k,v in after.items() if k != "calculations"}
    for owner, unit in (("other", "field"), ("owner", "field2")):
        with pytest.raises(ValueError):
            workflow.get(owner, unit, saved["id"])
    with pytest.raises(ValueError):
        workflow.save("owner", "field", "sprinkler_catch_test", data, cycle_id="cycle2", request_id="wrong-cycle")
    other = farms.export("other")
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())
    assert farms.export("other") == other


@pytest.mark.parametrize("kind", ["timezone", "preplant", "closed"])
def test_saved_test_requires_farm_timezone_and_cycle_dates(setup, kind):
    workflow, farms = setup
    data = example()
    if kind == "timezone":
        data["site"]["timezone"] = "Pacific/Honolulu"
    elif kind == "preplant":
        data["event"]["started_at"] = "2026-06-30T12:00:00Z"
    else:
        CropRecords(workflow.db).close_cycle("owner", "field", "cycle", "2026-07-06", "Ended", "Synthetic", request_id="end", expected_revision=None)
    before = farms.export("owner")
    with pytest.raises(ValueError):
        workflow.save("owner", "field", "sprinkler_catch_test", data, cycle_id="cycle", request_id="bad")
    assert farms.export("owner") == before


def test_season_keeps_saved_test_and_flags_changed_planting_and_timezone(setup):
    workflow, _ = setup
    saved = workflow.save("owner", "field", "sprinkler_catch_test", example(), cycle_id="cycle", request_id="test")
    records = CropRecords(workflow.db)
    records.correct_planting("owner", "field", "cycle", "pepper", "2026-07-08", "Synthetic", "Correct dates", request_id="correct", expected_revision=None)
    workflow.db.execute("UPDATE farms SET timezone='Pacific/Honolulu' WHERE id='farm'")
    summary = records.season_review("owner", "field", "cycle")["calculations"][0]
    assert summary["task"] == "sprinkler_catch_test" and len(summary["cycle_conflicts"]) == 2
    assert workflow.get("owner", "field", saved["id"]) == saved


def test_cli_runs_without_site_packages_and_reports_unknown_incomplete_grid(tmp_path):
    data = example()
    path = tmp_path/"catch.json"
    path.write_text(json.dumps(data))
    cmd = [sys.executable, "-S", "-m", "agronaut_agent.cli", "evaluate-sprinkler-catches", "--input", str(path)]
    result = subprocess.run([*cmd, "--json"], capture_output=True, text=True, timeout=30, cwd=Path(__file__).resolve().parents[2])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["statistics"]["mean_catch_rate_mm_h"] == 18.5
    data["catches"][0]["depth_mm"] = None
    path.write_text(json.dumps(data))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=Path(__file__).resolve().parents[2])
    assert result.returncode == 0, result.stderr
    assert "unknown / undefined" in result.stdout and "Statistics are withheld" in result.stdout
