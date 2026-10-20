"""Public measurement entry, immutable retries, CLI/chat and owner data rights."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agronaut_agent.crop_records import CropRecords, execute_crop_records
from agronaut_agent.tests.test_field_workflow import inputs
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_soil_workflow import adoption


def reading(**changes):
    data = dict(quantity="soil_water_content", value=.25, unit="m3/m3", observed_at="2026-07-06T18:00:00+00:00",
                provenance="measured", source="Synthetic root-zone sampling")
    data.update(changes)
    return data


def test_measurement_entry_retries_and_corrections_preserve_originals(setup):
    workflow, farms = setup
    records = CropRecords(workflow.db)
    args = dict(data=reading(), request_id="reading", cycle_id="cycle")
    original = execute_crop_records(workflow.db, "owner", "field", "record_measurement", args)
    review = records.review_measurement("owner", "field", original["id"], "rejected", "Incorrect value", "Fixture",
                                       request_id="reject", expected_revision=None)
    assert records.record_measurement("owner", "field", **args) == original
    assert original["quality"] == "unverified"
    with pytest.raises(ValueError, match="different data"):
        records.record_measurement("owner", "field", reading(value=.26), request_id="reading", cycle_id="cycle")
    corrected = records.record_measurement("owner", "field", reading(value=.26), request_id="corrected", cycle_id="cycle")
    assert corrected["id"] != original["id"]
    assert records.list_measurements("owner", "field")["measurements"][0]["quality_review"] == review
    base = workflow.save("owner", "field", "root_zone_budget", inputs(), request_id="base", cycle_id="cycle")
    updated = workflow.apply_soil_state("owner", "field", **adoption(base, observation_id=corrected["id"]))
    exported = farms.export("owner")
    assert updated in exported["calculations"]
    assert corrected in exported["observations"]
    json.dumps(exported, allow_nan=False)
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())
    assert farms.export("other")["units"]


@pytest.mark.parametrize("changes", [dict(value=True), dict(unit="%"), dict(value=float("inf")),
    dict(value=-1), dict(observed_at="2026-07-06T18:00:00"), dict(source=""),
    dict(observed_at="2099-07-06T18:00:00+00:00"), dict(quality="checked"), dict(owner_id="other")])
def test_invalid_measurements_do_not_write(setup, changes):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    with pytest.raises(ValueError):
        records.record_measurement("owner", "field", reading(**changes), request_id="invalid")
    assert records.list_measurements("owner", "field")["measurements"] == []


def test_measurement_entry_enforces_owner_and_cycle_scope(setup):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    for owner, unit, cycle in (("other", "field", None), ("owner", "field", "cycle2")):
        with pytest.raises(ValueError):
            records.record_measurement(owner, unit, reading(), request_id="bad", cycle_id=cycle)


def test_stdlib_cli_records_then_adopts_and_removes_soil_state(tmp_path):
    root = Path(__file__).resolve().parents[2]
    db = str(tmp_path / "workflow.sqlite3")
    path = tmp_path / "request.json"
    def command(*args):
        result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", *args], cwd=root,
                                 capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)
    spec = json.loads((root / "examples/soil_unit.json").read_text())
    spec["farm"]["timezone"] = "UTC"
    path.write_text(json.dumps(spec))
    command("farm", "--db", db, "create", "--input", str(path))
    def operation(group, name, data):
        path.write_text(json.dumps(data))
        return command(group, "--db", db, name, "--input", str(path))
    original = operation("crop", "record_measurement", dict(data=reading(), request_id="reading"))
    assert operation("crop", "record_measurement", dict(data=reading(), request_id="reading")) == original
    base = operation("field", "save", dict(task="root_zone_budget", inputs=inputs(), request_id="base"))
    review = operation("field", "review_soil", dict(calculation_id=base["id"]))
    assert review["measurements"][0]["id"] == original["id"]
    updated = operation("field", "apply_soil_state", adoption(base, observation_id=original["id"]))
    assert updated["parent_id"] == base["id"]
    removed = operation("field", "remove_soil_state", dict(calculation_id=updated["id"], as_of="2026-07-06",
                         reason="Withdraw sample", request_id="remove"))
    assert removed["result"] == base["result"]


def test_scoped_chat_records_and_adopts_supplied_measurement(tmp_path):
    from langchain_core.messages import AIMessage

    from agronaut_agent.tests.test_unit_agent import agent_and_units
    brain, _ = agent_and_units(tmp_path)
    brain.select_unit("cli", "one", "field")
    base = brain.field_workflow("cli", "one", "save", dict(task="root_zone_budget", inputs=inputs(), request_id="base"))
    brain._base.replies = [AIMessage(content="", tool_calls=[dict(name="crop_records", id="reading",
        args=dict(operation="record_measurement", data=dict(data=reading(), request_id="reading")))]),
        AIMessage(content="Saved your supplied reading.")]
    brain.handle_message("cli", "one", "Record my supplied soil measurement with its timestamp and source.")
    original = json.loads(brain.tool_results("cli", "one", "crop_records")[-1])
    brain._base.replies = [AIMessage(content="", tool_calls=[dict(name="field_records", id="adopt",
        args=dict(operation="apply_soil_state", data=adoption(base, observation_id=original["id"])))]),
        AIMessage(content="Saved the soil-state revision.")]
    brain.handle_message("cli", "one", "Apply that sample using my supplied whole-root coverage and midnight adjustment.")
    updated = json.loads(brain.tool_results("cli", "one", "field_records")[-1])
    assert updated["soil_state_evidence"][0]["observation"]["id"] == original["id"]
    assert updated["parent_id"] == base["id"]
    assert brain.field_workflow("cli", "one", "list", {})["water_days"] == []
