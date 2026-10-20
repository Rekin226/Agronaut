"""Owned delivery constraints survive saving, retries and alternative scheduling."""

import copy

import pytest

from agro_model.resource_schedule import propose_schedule
from agro_model.tests.test_resource_delivery import delivery, free_counts
from agro_model.tests.test_resource_schedule import request
from agronaut_agent.farm_resources import execute_farm_resources
from agronaut_agent.resource_ledger import ResourceLedger
from agronaut_agent.tests.test_equipment_calendar import book, prepared
from agronaut_agent.tests.test_equipment_schedule import clock as clock
from agronaut_agent.tests.test_equipment_schedule import inputs, search
from agronaut_agent.tests.test_farm_resources import setup as setup
from agronaut_agent.tests.test_resource_timing import saved_scenario


def test_readonly_check_immutable_save_retry_replay_export_and_erasure(setup):
    service, farms = setup
    parent, timing = saved_scenario(service)
    free_counts(timing)
    data = dict(request(timing), water_delivery=delivery())
    before = farms.export("owner")
    proposal = execute_farm_resources(service.db, "owner", "farm", "propose_schedule", dict(plan_id=parent["id"], data=data))
    assert proposal["schedule_result"]["delivery_pruned"] > 0
    assert farms.export("owner") == before
    saved = service.save_schedule("owner", "farm", parent["id"], data, request_id="delivery")
    assert service.save_schedule("owner", "farm", parent["id"], data, request_id="delivery") == saved
    assert saved["inputs"] == parent["inputs"] and saved["snapshot"] == parent["snapshot"]
    replay = propose_schedule(saved["schedule_inputs"], saved["inputs"], saved["result"])
    assert {key: saved[key] for key in replay} == replay
    assert service.compare("owner", "farm", [parent["id"], saved["id"]])["scenarios"][1]["timing_result"]["water_delivery_checked"]
    after = farms.export("owner")
    assert after["resource_plans"][-1]["schedule_inputs"]["water_delivery"] == delivery()
    for key in before:
        if key != "resource_plans":
            assert before[key] == after[key]
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())


@pytest.mark.parametrize("operation", ["evaluate_timing", "save_timed", "propose_schedule", "save_schedule"])
def test_parent_limits_cannot_be_silently_dropped_but_explicit_removal_is_retained(setup, operation):
    service, _ = setup
    base, timing = saved_scenario(service)
    parent = service.save_timed("owner", "farm", base["id"], dict(timing, water_delivery=delivery()), request_id="timed")
    data = request(timing) if "schedule" in operation else timing
    args = dict(plan_id=parent["id"], data=data)
    if operation.startswith("save"):
        args["request_id"] = "alternative"
    with pytest.raises(ValueError, match="parent has water delivery"):
        execute_farm_resources(service.db, "owner", "farm", operation, args)
    data["water_delivery"] = []
    result = execute_farm_resources(service.db, "owner", "farm", operation, args)
    assert result["timing_inputs"]["water_delivery"] == []
    assert not result["timing_result"]["water_delivery_checked"]
    assert service.get("owner", "farm", parent["id"])["timing_inputs"]["water_delivery"] == delivery()


def test_failed_proposal_retains_limits_and_invalid_or_foreign_save_does_not_write(setup):
    service, farms = setup
    parent, timing = saved_scenario(service)
    data = dict(request(timing), water_delivery=delivery("499"))
    failed = service.save_schedule("owner", "farm", parent["id"], data, request_id="failed")
    assert failed["schedule_result"]["status"] == "no_feasible_schedule_on_grid"
    assert "timing_inputs" not in failed
    with pytest.raises(ValueError, match="parent has water delivery"):
        service.propose_schedule("owner", "farm", failed["id"], request(timing))
    before = farms.export("owner")
    with pytest.raises(ValueError):
        service.save_schedule("other", "farm", parent["id"], data, request_id="foreign")
    bad = copy.deepcopy(data)
    bad["water_delivery"][0]["windows"][0]["source"] = ""
    with pytest.raises(ValueError):
        service.save_schedule("owner", "farm", parent["id"], bad, request_id="bad")
    with pytest.raises(ValueError, match="different data"):
        service.save_schedule("owner", "farm", parent["id"], dict(data, water_delivery=[]), request_id="failed")
    assert farms.export("owner") == before


def test_equipment_search_honors_delivery_and_failed_timing_cannot_be_booked(setup):
    service, farms = setup
    calendar, pump, parent = prepared(service)
    data = inputs(parent, pump)
    data["schedule"]["water_delivery"] = delivery("500", start="2026-10-20T10:00:00Z", end="2026-10-20T12:00:00Z")
    saved = search(service, parent, data, "delivery-equipment")
    assert saved["timing_result"]["water_delivery_checked"]
    assert saved["timing_inputs"]["work"][0]["start"] == "2026-10-20T10:00:00+00:00"
    missing = copy.deepcopy(data)
    del missing["schedule"]["water_delivery"]
    with pytest.raises(ValueError, match="parent has water delivery"):
        search(service, saved, missing)
    failed = service.save_timed("owner", "farm", saved["id"], dict(saved["timing_inputs"], water_delivery=delivery("0")), request_id="failed")
    before = farms.export("owner")
    with pytest.raises(ValueError, match="timing"):
        book(calendar, pump, failed)
    with pytest.raises(ValueError, match="timing checks failed"):
        ResourceLedger(service.db).commit_plan("owner", "farm", failed["id"], {}, "Synthetic", False, request_id="failed-commit")
    assert farms.export("owner") == before
    assert book(calendar, pump, saved)["plan_snapshot"]["timing_inputs"]["water_delivery"] == data["schedule"]["water_delivery"]
