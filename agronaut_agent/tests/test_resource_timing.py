"""Timed alternatives preserve exact parent allocations, ownership and history."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agro_model.tests.test_resource_timing import scenario
from agronaut_agent.crop_records import CropRecords
from agronaut_agent.farm_resources import execute_farm_resources
from agronaut_agent.store import ConversationStore
from agronaut_agent.tests.test_farm_resources import setup as setup
from agronaut_agent.tests.test_farm_store import seed


def saved_scenario(service):
    plan, timing = scenario()
    service.db.execute("UPDATE farms SET timezone='UTC' WHERE id='farm'")
    plan["jobs"][0]["cycle_id"] = "cycle"
    return service.save("owner", "farm", plan, request_id="allocation"), timing


def test_evaluate_and_save_timed_children_keep_parent_exact_and_have_no_actual_effects(setup):
    service, farms = setup
    parent, timing = saved_scenario(service)
    before = farms.export("owner")
    evaluated = service.evaluate_timing("owner", "farm", parent["id"], timing)
    assert evaluated["timing_result"]["declared_checks_pass"]
    assert farms.export("owner") == before
    saved = service.save_timed("owner", "farm", parent["id"], timing, request_id="timed")
    assert saved["inputs"] == parent["inputs"] and saved["result"] == parent["result"] and saved["snapshot"] == parent["snapshot"]
    assert saved["parent_id"] == parent["id"]
    assert service.save_timed("owner", "farm", parent["id"], timing, request_id="timed") == saved
    assert "timing_inputs" not in service.get("owner", "farm", parent["id"])
    assert len(farms.export("owner")["resource_plans"]) == 2
    assert farms.export("owner")["water_days"] == farms.export("owner")["crop_records"] == []
    listed = service.list("owner", "farm")["plans"]
    assert listed[0]["timing_checks_pass"] is None and listed[1]["timing_checks_pass"] is True
    compared = service.compare("owner", "farm", [parent["id"], saved["id"]])
    assert "timing_result" not in compared["scenarios"][0]
    assert compared["scenarios"][1]["timing_result"] == evaluated["timing_result"]


def test_conflicting_intervals_remain_reviewable_and_changed_crop_assumptions_stay_flagged(setup):
    service, _ = setup
    parent, timing = saved_scenario(service)
    timing["work"][1].update(start=timing["work"][0]["start"], end=timing["work"][0]["end"])
    failed = service.save_timed("owner", "farm", parent["id"], timing, request_id="conflict")
    assert not failed["timing_result"]["declared_checks_pass"]
    CropRecords(service.db).correct_planting("owner", "unit", "cycle", "pepper", "2026-07-02", "Synthetic", "Correction",
                                            request_id="amend", expected_revision=None)
    assert service.save_timed("owner", "farm", parent["id"], timing, request_id="conflict") == failed
    assert service.get("owner", "farm", failed["id"])["review"]["needs_review"]
    timing["work"][1].update(start="2026-10-20T09:00:00Z", end="2026-10-20T10:00:00Z")
    corrected = service.save_timed("owner", "farm", failed["id"], timing, request_id="corrected")
    assert corrected["timing_result"]["declared_checks_pass"]
    assert service.get("owner", "farm", corrected["id"])["review"]["needs_review"]  # Timing edits do not accept amended crop assumptions.


def test_ownership_missing_data_and_conflicting_retries_fail_atomically(setup):
    service, farms = setup
    parent, timing = saved_scenario(service)
    seed(farms, "owner", "second")
    for owner, farm in (("other", "farm"), ("owner", "farmsecond")):
        with pytest.raises(ValueError):
            service.save_timed(owner, farm, parent["id"], timing, request_id="wrong")
    with pytest.raises(ValueError, match="exactly"):
        service.save_timed("owner", "farm", parent["id"], dict(timing, work=[]), request_id="missing")
    with pytest.raises(ValueError, match="different data"):
        service.save_timed("owner", "farm", parent["id"], timing, request_id="allocation")
    saved = execute_farm_resources(service.db, "owner", "farm", "save_timed", dict(plan_id=parent["id"], data=timing, request_id="timed"))
    with pytest.raises(ValueError, match="different data"):
        service.save_timed("owner", "farm", parent["id"], dict(timing, source="Changed"), request_id="timed")
    assert farms.export("owner")["resource_plans"] == [parent, saved]
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())


def test_timing_cli_works_without_site_packages(setup, tmp_path):
    from agro_model.tests.test_resource_delivery import delivery

    service, _ = setup
    owner = ConversationStore(service.db).get_or_create_user("cli", "local")
    service.db.execute("UPDATE farms SET owner_id=?, timezone='UTC' WHERE id='farm'", (owner,))
    plan, timing = scenario()
    timing["water_delivery"] = delivery()
    parent = service.save(owner, "farm", plan, request_id="allocation")
    path = tmp_path/"timing.json"
    request = dict(plan_id=parent["id"], data=timing)
    root = Path(__file__).resolve().parents[2]

    def command(operation, data):
        path.write_text(json.dumps(data))
        process = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", "resources", "--db", str(service.db.path),
            "--farm", "farm", operation, "--input", str(path)], cwd=root, capture_output=True, text=True, timeout=30)
        assert process.returncode == 0, process.stdout+process.stderr
        return json.loads(process.stdout)

    assert command("evaluate_timing", request)["timing_result"]["declared_checks_pass"]
    saved = command("save_timed", dict(request, request_id="timed"))
    assert command("get", dict(plan_id=saved["id"]))["timing_inputs"] == timing
