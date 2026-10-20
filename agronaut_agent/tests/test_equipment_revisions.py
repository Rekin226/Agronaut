"""Previewed design changes preserve provenance, target scope and immutable history."""

import json
import os
import sqlite3
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from agronaut_agent.equipment_revisions import EquipmentRevisions
from agronaut_agent.field_consultation import FieldConsultation
from agronaut_agent.field_workflow import execute_field_workflow
from agronaut_agent.tests.test_equipment_targets import budget, link
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_field_workflow import water

ROOT = Path(__file__).resolve().parents[2]


def parent(workflow, method="drip"):
    inputs = json.loads((ROOT / f"examples/{method}_design.json").read_text())
    return workflow.save("owner", "field", method + "_design", inputs, cycle_id="cycle", request_id="parent")


def request(saved, **changes):
    data = dict(calculation_id=saved["id"], patch={"supply": {"daily_available_l": 0, "source": "Synthetic revised allowance"}},
                reason="Synthetic source unavailable", target_policy="preserve")
    data.update(changes)
    return data


def linked(workflow, method="drip_design", source=None):
    source = source or budget(workflow, method)
    wizard = FieldConsultation(workflow.db)
    ready = link(wizard, source, method)
    return wizard.save("owner", "field", ready["draft_id"], ready["revision"])["calculation"]


@pytest.mark.parametrize("method", ["drip", "sprinkler"])
def test_preview_save_retry_and_explicit_comparison(setup, method):
    workflow, farms = setup
    original = parent(workflow, method)
    data = request(original)
    before = farms.export("owner")
    preview = execute_field_workflow(workflow.db, "owner", "field", "preview_equipment_revision", data)
    assert farms.export("owner") == before
    assert preview["comparison"]["before_feasible"] is True
    assert preview["comparison"]["after_feasible"] is False
    assert "daily_source_water_l" in preview["comparison"]["after_constraints"]
    assert {row["field"] for row in preview["changes"]} == {"daily_available_l", "source"}
    args = dict(data, preview_token=preview["preview_token"], request_id="revision")
    child = execute_field_workflow(workflow.db, "owner", "field", "revise_equipment", args)
    assert child["parent_id"] == original["id"] and child["cycle_id"] == original["cycle_id"]
    assert child["result"] == preview["result"]
    assert child["equipment_revision"]["changes"] == preview["changes"]
    assert execute_field_workflow(workflow.db, "owner", "field", "revise_equipment", args) == child
    assert workflow.get("owner", "field", original["id"]) == original
    assert len(workflow.list("owner", "field")["calculations"]) == 2
    assert workflow.list("owner", "field")["water_days"] == []
    with pytest.raises(ValueError, match="different data"):
        execute_field_workflow(workflow.db, "owner", "field", "revise_equipment", dict(args, reason="Changed request"))


@pytest.mark.parametrize("patch", [{}, {"supply": {}}, {"schema_version": 2}, {"new": {}},
    {"supply": {"invented": 12, "source": "Fixture"}}, {"supply": {"daily_available_l": 0}},
    {"supply": {"daily_available_l": -1, "source": "Fixture"}},
    {"supply": {"daily_available_l": True, "source": "Fixture"}},
    {"supply": {"daily_available_l": 0, "source": " "}},
    {"layout": {"terrain": "hilly", "source": "Fixture"}}])
def test_bad_or_unsourced_changes_never_write(setup, patch):
    workflow, farms = setup
    saved = parent(workflow)
    before = farms.export("owner")
    with pytest.raises(ValueError):
        EquipmentRevisions(workflow.db).preview("owner", "field", **request(saved, patch=patch))
    assert farms.export("owner") == before


@pytest.mark.parametrize("method", ["drip_design", "sprinkler_design"])
def test_linked_target_is_preserved_and_cannot_be_silently_resized(setup, method):
    workflow, _ = setup
    saved = linked(workflow, method)
    service = EquipmentRevisions(workflow.db)
    data = request(saved)
    preview = service.preview("owner", "field", **data)
    child = service.save("owner", "field", **data, preview_token=preview["preview_token"], request_id="keep")
    assert child["water_target"] == saved["water_target"]
    depth = "gross_depth_mm" if method == "drip_design" else "net_depth_mm"
    with pytest.raises(ValueError, match="original application amount"):
        service.preview("owner", "field", **request(saved,
            patch={"application": {depth: 1, "source": "Synthetic altered amount"}}))
    count = "row_count" if method == "drip_design" else "lateral_count"
    with pytest.raises(ValueError, match="area"):
        service.preview("owner", "field", **request(saved,
            patch={"layout": {count: 20, "source": "Synthetic expanded field"}}))


def test_detachment_is_explicit_and_retains_prior_evidence_in_parent(setup):
    workflow, _ = setup
    saved = linked(workflow)
    service = EquipmentRevisions(workflow.db)
    with pytest.raises(ValueError, match="application source"):
        service.preview("owner", "field", **request(saved, target_policy="detach"))
    data = request(saved, target_policy="detach", patch={"application": {
        "gross_depth_mm": 2, "source": "Synthetic independent amount; previous bucket target withdrawn"}})
    preview = service.preview("owner", "field", **data)
    assert preview["water_target"] is None
    child = service.save("owner", "field", **data, preview_token=preview["preview_token"], request_id="detach")
    assert "water_target" not in child
    assert child["equipment_revision"]["previous_water_target_id"] == saved["water_target"]["calculation_id"]
    assert workflow.get("owner", "field", saved["id"]) == saved
    with pytest.raises(ValueError, match="no linked"):
        service.preview("owner", "field", **dict(data, calculation_id=child["id"]))


def test_changed_daily_evidence_between_preview_and_save_requires_review(setup):
    workflow, _ = setup
    source = budget(workflow)
    day = workflow.record_day("owner", "field", water(), cycle_id="cycle", request_id="day")
    source = workflow.replay("owner", "field", source["id"], [day["id"]], request_id="replay")
    saved = linked(workflow, source=source)
    service = EquipmentRevisions(workflow.db)
    data = request(saved)
    preview = service.preview("owner", "field", **data)
    workflow.record_day("owner", "field", water(net_irrigation_mm=4), cycle_id="cycle",
                        request_id="correction", supersedes_id=day["id"])
    with pytest.raises(ValueError, match="corrected"):
        service.save("owner", "field", **data, preview_token=preview["preview_token"], request_id="stale")
    assert len(workflow.list("owner", "field")["calculations"]) == 3


def test_scope_preview_token_and_atomic_metadata_save(setup):
    workflow, farms = setup
    saved = parent(workflow)
    service = EquipmentRevisions(workflow.db)
    data = request(saved)
    preview = service.preview("owner", "field", **data)
    before = farms.export("owner")
    for owner, unit in (("other", "field"), ("owner", "field2")):
        with pytest.raises(ValueError):
            service.preview(owner, unit, **data)
    changed = deepcopy(data)
    changed["patch"]["supply"]["daily_available_l"] = 1
    with pytest.raises(ValueError, match="differs from its preview"):
        service.save("owner", "field", **changed, preview_token=preview["preview_token"], request_id="changed")
    with workflow.db.transaction() as conn:
        conn.execute("CREATE TRIGGER fail_revision BEFORE UPDATE ON field_calculations BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="fixture failure"):
        service.save("owner", "field", **data, preview_token=preview["preview_token"], request_id="rollback")
    assert farms.export("owner") == before


def test_revision_history_is_exported_and_erased_with_owner(setup):
    workflow, farms = setup
    saved = parent(workflow)
    service = EquipmentRevisions(workflow.db)
    data = request(saved)
    preview = service.preview("owner", "field", **data)
    child = service.save("owner", "field", **data, preview_token=preview["preview_token"], request_id="revision")
    exported = farms.export("owner")
    assert child in exported["calculations"]
    assert child not in farms.export("other")["calculations"]
    farms.purge("owner")
    assert farms.export("owner")["calculations"] == []
    with pytest.raises(ValueError):
        workflow.get("owner", "field", child["id"])


def test_engine_result_change_requires_a_fresh_preview(setup, monkeypatch):
    from agronaut_agent import equipment_revisions

    workflow, farms = setup
    saved = parent(workflow)
    service = EquipmentRevisions(workflow.db)
    data = request(saved)
    preview = service.preview("owner", "field", **data)
    run = equipment_revisions.run_capability
    before = farms.export("owner")

    def changed_engine(*args):
        result = run(*args)
        result["result"]["model_version"] = "synthetic-changed-engine-version"
        return result

    monkeypatch.setattr(equipment_revisions, "run_capability", changed_engine)
    with pytest.raises(ValueError, match="differs from its preview"):
        service.save("owner", "field", **data, preview_token=preview["preview_token"], request_id="changed-engine")
    assert farms.export("owner") == before


def test_wrong_task_and_standard_library_cli(setup, tmp_path):
    workflow, _ = setup
    wrong = budget(workflow)
    service = EquipmentRevisions(workflow.db)
    with pytest.raises(ValueError, match="parent task"):
        service.preview("owner", "field", **request(wrong))
    saved = parent(workflow)
    # CLI identities are owned separately; map this local fixture's CLI identity.
    from agronaut_agent.store import ConversationStore
    owner = ConversationStore(workflow.db).get_or_create_user("cli", "reviser")
    with workflow.db.transaction() as conn:
        conn.execute("UPDATE farms SET owner_id=? WHERE id='farm'", (owner,))
    path = tmp_path / "revision.json"
    path.write_text(json.dumps(request(saved)))
    base = [sys.executable, "-S", "-m", "agronaut_agent.cli", "field", "--db", str(workflow.db.path),
            "--user", "reviser", "--unit", "field"]
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    result = subprocess.run(base + ["preview_equipment_revision", "--input", str(path)], cwd=tmp_path,
                            env=env, capture_output=True, text=True, check=True)
    preview = json.loads(result.stdout)
    path.write_text(json.dumps(dict(request(saved), preview_token=preview["preview_token"], request_id="cli-save")))
    result = subprocess.run(base + ["revise_equipment", "--input", str(path)], cwd=tmp_path,
                            env=env, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["parent_id"] == saved["id"]
