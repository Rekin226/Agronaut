"""Owned proposals, immutable search receipts, races, export and stdlib CLI."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agro_model.tests.test_resource_dependencies import dependency
from agro_model.tests.test_resource_schedule import request
from agro_model.tests.test_resource_timing import scenario
from agronaut_agent import farm_resources
from agronaut_agent.crop_records import CropRecords
from agronaut_agent.farm_resources import execute_farm_resources
from agronaut_agent.store import ConversationStore
from agronaut_agent.tests.test_farm_resources import setup as setup
from agronaut_agent.tests.test_farm_store import seed
from agronaut_agent.tests.test_resource_timing import saved_scenario


def test_read_only_proposal_and_immutable_child_keep_exact_parent_and_export(setup):
    service, farms = setup
    parent, timing = saved_scenario(service)
    data = request(timing)
    before = farms.export("owner")
    result = service.propose_schedule("owner", "farm", parent["id"], data)
    assert result["schedule_result"]["status"] == "feasible_schedule_found"
    assert farms.export("owner") == before
    saved = execute_farm_resources(service.db, "owner", "farm", "save_schedule",
                                  dict(plan_id=parent["id"], data=data, request_id="scheduled"))
    for key in ("inputs", "result", "snapshot"):
        assert saved[key] == parent[key]
    for key in ("schedule_inputs", "schedule_result", "timing_inputs", "timing_result"):
        assert saved[key] == result[key]
    assert saved["parent_id"] == parent["id"]
    assert "schedule_result" not in service.get("owner", "farm", parent["id"])
    exported = farms.export("owner")
    assert exported["resource_plans"] == [parent, saved]
    assert {k: v for k, v in exported.items() if k != "resource_plans"} == {k: v for k, v in before.items() if k != "resource_plans"}
    assert service.list("owner", "farm")["plans"][1]["schedule_status"] == "feasible_schedule_found"
    compared = service.compare("owner", "farm", [parent["id"], saved["id"]])
    assert compared["scenarios"][1]["schedule_result"] == result["schedule_result"]
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())


def test_limits_and_infeasible_attempts_can_be_saved_without_a_fictitious_timing(setup):
    service, _ = setup
    parent, timing = saved_scenario(service)
    data = request(timing)
    data["search_limit"] = 1
    limited = service.save_schedule("owner", "farm", parent["id"], data, request_id="limited")
    assert limited["schedule_result"]["status"] == "search_limit_reached"
    assert "timing_inputs" not in limited and "timing_result" not in limited
    data["search_limit"] = 100
    data["runs"][0]["windows"] = []
    failed = service.save_schedule("owner", "farm", limited["id"], data, request_id="failed")
    assert failed["schedule_result"]["status"] == "no_feasible_schedule_on_grid"
    assert "timing_inputs" not in failed and "timing_result" not in failed
    assert failed["result"] == parent["result"]


def test_retries_do_not_recompute_or_accept_changed_assumptions(setup, monkeypatch):
    service, _ = setup
    parent, timing = saved_scenario(service)
    data = request(timing)
    saved = service.save_schedule("owner", "farm", parent["id"], data, request_id="scheduled")
    CropRecords(service.db).correct_planting("owner", "unit", "cycle", "pepper", "2026-07-02", "Synthetic", "Correction",
                                            request_id="amend", expected_revision=None)
    assert service.get("owner", "farm", saved["id"])["review"]["needs_review"]
    monkeypatch.setattr(farm_resources, "propose_schedule", lambda *_: pytest.fail("retry must not search again"))
    assert service.save_schedule("owner", "farm", parent["id"], data, request_id="scheduled") == saved
    with pytest.raises(ValueError, match="different data"):
        service.save_schedule("owner", "farm", parent["id"], dict(data, search_limit=99), request_id="scheduled")


def test_invalid_or_foreign_requests_do_not_write(setup):
    service, farms = setup
    parent, timing = saved_scenario(service)
    data = request(timing)
    seed(farms, "owner", "second")
    for owner, farm in (("other", "farm"), ("owner", "farmsecond")):
        for operation in ("propose_schedule", "save_schedule"):
            args = dict(plan_id=parent["id"], data=data)
            if operation == "save_schedule":
                args["request_id"] = "foreign"
            with pytest.raises(ValueError):
                execute_farm_resources(service.db, owner, farm, operation, args)
    with pytest.raises(ValueError, match="exactly"):
        service.save_schedule("owner", "farm", parent["id"], dict(data, runs=[]), request_id="missing")
    with pytest.raises(ValueError, match="different data"):
        service.save_schedule("owner", "farm", parent["id"], data, request_id="allocation")
    assert farms.export("owner")["resource_plans"] == [parent]


@pytest.mark.parametrize("save", [False, True])
def test_erasure_during_search_is_rechecked_without_holding_transaction(setup, monkeypatch, save):
    service, farms = setup
    parent, timing = saved_scenario(service)
    real = farm_resources.propose_schedule

    def erase(*args):
        assert not service.db._conn.in_transaction
        result = real(*args)
        farms.purge("owner")
        return result

    monkeypatch.setattr(farm_resources, "propose_schedule", erase)
    with pytest.raises(ValueError, match="unknown farm"):
        if save:
            service.save_schedule("owner", "farm", parent["id"], request(timing), request_id="late")
        else:
            service.propose_schedule("owner", "farm", parent["id"], request(timing))
    assert all(not rows for rows in farms.export("owner").values())


def test_scheduling_cli_uses_no_site_packages(setup, tmp_path):
    service, _ = setup
    owner = ConversationStore(service.db).get_or_create_user("cli", "local")
    service.db.execute("UPDATE farms SET owner_id=?, timezone='UTC' WHERE id='farm'", (owner,))
    plan, timing = scenario()
    parent = service.save(owner, "farm", plan, request_id="allocation")
    data = dict(plan_id=parent["id"], data=dict(request(timing), dependencies=[dependency()]))
    path = tmp_path/"schedule.json"

    def command(operation, payload):
        path.write_text(json.dumps(payload))
        proc = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", "resources", "--db", str(service.db.path),
            "--farm", "farm", operation, "--input", str(path)], cwd=Path(__file__).resolve().parents[2],
            capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stdout+proc.stderr
        return json.loads(proc.stdout)

    assert command("propose_schedule", data)["timing_result"]["declared_checks_pass"]
    saved = command("save_schedule", dict(data, request_id="scheduled"))
    assert command("get", dict(plan_id=saved["id"]))["schedule_inputs"] == data["data"]
    assert "propose_schedule" in command("describe", {})["operations"]


def test_dependencies_survive_save_retry_review_export_and_manual_timing_edit(setup):
    service, farms = setup
    parent, timing = saved_scenario(service)
    data = dict(request(timing), dependencies=[dependency()])
    saved = service.save_schedule("owner", "farm", parent["id"], data, request_id="ordered")
    assert saved["timing_result"]["dependencies"][0]["satisfied"]
    assert service.save_schedule("owner", "farm", parent["id"], data, request_id="ordered") == saved
    manual = saved["timing_inputs"]
    for row in manual["work"]:
        hour = 8 if row["id"] == "field" else 9
        row.update(start=f"2026-10-20T{hour:02d}:00:00Z", end=f"2026-10-20T{hour+1:02d}:00:00Z")
    failed = service.save_timed("owner", "farm", saved["id"], manual, request_id="manual-reversal")
    assert {i["code"] for i in failed["timing_result"]["issues"]} == {"dependency_not_satisfied"}
    assert service.get("owner", "farm", saved["id"])["timing_result"]["declared_checks_pass"]
    CropRecords(service.db).correct_planting("owner", "unit", "cycle", "pepper", "2026-07-02", "Synthetic", "Correction",
                                            request_id="amend-order", expected_revision=None)
    assert service.get("owner", "farm", failed["id"])["review"]["needs_review"]
    assert farms.export("owner")["resource_plans"][-1]["timing_inputs"]["dependencies"] == [dependency()]
    assert farms.export("owner")["water_days"] == []
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())


@pytest.mark.parametrize("operation", ["evaluate_timing", "save_timed", "propose_schedule", "save_schedule"])
def test_parent_dependencies_cannot_disappear_through_omission(setup, operation):
    service, _ = setup
    parent, timing = saved_scenario(service)
    inputs = dict(request(timing), dependencies=[dependency()])
    ordered = service.save_schedule("owner", "farm", parent["id"], inputs, request_id="ordered")
    data = timing if "tim" in operation else request(timing)
    args = dict(plan_id=ordered["id"], data=data)
    if operation.startswith("save"):
        args["request_id"] = "without-dependencies"
    with pytest.raises(ValueError, match="parent has work dependencies"):
        execute_farm_resources(service.db, "owner", "farm", operation, args)
    # Removal is an explicit alternative, never a mutation of the saved parent.
    args["data"] = dict(data, dependencies=[])
    result = execute_farm_resources(service.db, "owner", "farm", operation, args)
    assert result["timing_result"]["declared_checks_pass"]
    assert service.get("owner", "farm", ordered["id"])["timing_inputs"]["dependencies"] == [dependency()]


def test_dependency_change_conflicts_with_existing_request_key(setup):
    service, _ = setup
    parent, timing = saved_scenario(service)
    data = dict(request(timing), dependencies=[dependency()])
    service.save_schedule("owner", "farm", parent["id"], data, request_id="ordered")
    data["dependencies"][0]["min_gap_seconds"] = 1
    with pytest.raises(ValueError, match="different data"):
        service.save_schedule("owner", "farm", parent["id"], data, request_id="ordered")
