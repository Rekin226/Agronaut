"""Explicit repeat work keeps historical charges and revalidates copied inputs."""

import copy

import pytest

from agro_model.tests.test_resource_schedule import request
from agronaut_agent.crop_records import CropRecords
from agronaut_agent.farm_resources import FarmResources, execute_farm_resources
from agronaut_agent.tests import test_field_workflow as field_fixtures
from agronaut_agent.tests.test_equipment_consultation import example
from agronaut_agent.tests.test_farm_resources import setup as setup
from agronaut_agent.tests.test_farm_store import seed
from agronaut_agent.tests.test_resource_designs import resource_plan, specification
from agronaut_agent.tests.test_resource_ledger import commit, plan
from agronaut_agent.tests.test_resource_settlement import clock as clock
from agronaut_agent.tests.test_resource_settlement import prepared_settlement, settle, usage
from agronaut_agent.tests.test_resource_timing import saved_scenario

linked_setup = field_fixtures.setup


def new_work(service, template, data, key="new", reason="Separate second irrigation event"):
    return execute_farm_resources(service.db, "owner", "farm", "save_new_work",
        dict(template_id=template["id"], data=data, reason=reason, request_id=key))


def test_separate_work_spends_only_remaining_budget_and_edits_stay_in_new_family(setup):
    service, farms = setup
    ledger, settlement, pool, template, reserved = prepared_settlement(service)
    settled = settle(settlement, reserved, usage(reserved, "450"))
    alternate = service.save("owner", "farm", plan("550"), parent_id=template["id"], request_id="old-alternative")
    with pytest.raises(ValueError, match="family"):
        commit(ledger, pool, alternate, "old-again")
    before = farms.export("owner")
    separate = new_work(service, template, plan("600"))
    assert separate["parent_id"] is None
    assert separate["new_work"]["template"] == {key: template[key] for key in ("id", "created_at")} | {
        "title": template["inputs"]["title"], "period": template["inputs"]["period"]}
    assert farms.export("owner")["resource_commitments"] == before["resource_commitments"]
    with pytest.raises(ValueError, match="insufficient"):
        commit(ledger, pool, separate, "too-much")
    adjusted = service.save("owner", "farm", plan("550"), parent_id=separate["id"], request_id="adjusted")
    committed = commit(ledger, pool, adjusted, "second-event")
    balance = ledger.ledger("owner", "farm")["budgets"][0]
    assert (balance["reserved"], balance["recorded_use"], balance["remaining"]) == ("550", "450", "0")
    assert committed["plan_snapshot"]["parent_id"] == separate["id"]
    assert ledger.ledger("owner", "farm")["settlements"][0]["amounts"] == settled["amounts"]
    with pytest.raises(ValueError, match="family"):
        commit(ledger, pool, separate, "duplicate-new-family")
    after = farms.export("owner")
    for key in ("water_days", "observations", "crop_records"):
        assert after[key] == before[key]


@pytest.mark.parametrize("fault", ["reason", "template", "owner", "farm", "cycle", "incomplete", "parent"])
def test_invalid_or_foreign_new_work_is_atomic(setup, fault):
    service, farms = setup
    seed(farms, "owner", "second")
    template = service.save("owner", "farm", plan(), request_id="template")
    payload = dict(template_id=template["id"], data=plan(), reason="Distinct work", request_id="new")
    owner, farm = "owner", "farm"
    if fault == "reason":
        payload["reason"] = "  "
    elif fault == "template":
        payload["template_id"] = "missing"
    elif fault == "owner":
        owner = "other"
    elif fault == "farm":
        farm = "farmsecond"
    elif fault == "cycle":
        payload["data"]["jobs"][0]["cycle_id"] = "cyclesecond"
    elif fault == "incomplete":
        del payload["data"]["period"]
    else:
        payload["parent_id"] = template["id"]
    before = farms.export("owner")
    with pytest.raises(ValueError):
        execute_farm_resources(service.db, owner, farm, "save_new_work", payload)
    assert farms.export("owner") == before


def test_new_work_revalidates_current_context_and_retry_preserves_original_export(setup):
    service, farms = setup
    template = service.save("owner", "farm", plan(), request_id="template")
    CropRecords(service.db).correct_planting("owner", "unit", "cycle", "pepper", "2026-07-02", "Synthetic", "Correction",
                                            request_id="amend", expected_revision=None)
    fresh = new_work(service, template, plan())
    assert fresh["snapshot"] != template["snapshot"]
    assert not service.get("owner", "farm", fresh["id"])["review"]["needs_review"]
    assert service.get("owner", "farm", template["id"])["snapshot"] == template["snapshot"]
    service.db.execute("UPDATE farms SET timezone='UTC' WHERE id='farm'")
    assert new_work(service, template, plan()) == fresh
    with pytest.raises(ValueError, match="different data"):
        new_work(service, template, plan(), reason="Changed declaration")
    with pytest.raises(ValueError, match="timezone"):
        new_work(service, template, plan(), key="fresh-attempt")
    assert service.list("owner", "farm")["plans"][-1]["work_template_id"] == template["id"]
    assert farms.export("owner")["resource_plans"] == [template, fresh]
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())


def test_new_work_does_not_copy_timing_or_schedule(setup):
    service, farms = setup
    template, timing = saved_scenario(service)
    scheduled = service.save_schedule("owner", "farm", template["id"], request(timing), request_id="scheduled")
    assert "timing_result" in scheduled and "schedule_result" in scheduled
    before = farms.export("owner")
    data = copy.deepcopy(scheduled["inputs"])
    data["period"].update(start="2026-10-21", end="2026-10-21")
    fresh = new_work(service, scheduled, data)
    assert fresh["parent_id"] is None and fresh["inputs"]["period"] == data["period"]
    assert not any(key.startswith(("timing_", "schedule_")) for key in fresh)
    assert {k: v for k, v in farms.export("owner").items() if k != "resource_plans"} == {
        k: v for k, v in before.items() if k != "resource_plans"}


def test_linked_design_date_must_be_explicitly_reviewed_for_new_work(linked_setup):
    workflow, farms = linked_setup
    source = workflow.save("owner", "field", "drip_design", example("drip_design"), cycle_id="cycle", request_id="equipment")
    service = FarmResources(workflow.db)
    spec = specification(source)
    data = resource_plan(service.design_job("owner", "farm", spec), spec)
    template = service.save("owner", "farm", data, request_id="template")
    before = farms.export("owner")
    data["period"].update(start="2026-07-09", end="2026-07-09")
    with pytest.raises(ValueError):
        new_work(service, template, data)
    assert farms.export("owner") == before
    reviewed = dict(spec, day="2026-07-09", source="Operator reviewed applicability for the next event")
    data["jobs"] = [service.design_job("owner", "farm", reviewed)["job"]]
    fresh = new_work(service, template, data)
    assert fresh["inputs"]["jobs"][0]["irrigation_design"]["day"] == "2026-07-09"
