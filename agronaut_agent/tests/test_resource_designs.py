"""Saved equipment evidence becomes dated resource work without inventing actuals."""

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from agro_model.resource_schedule import propose_schedule
from agro_model.resource_timing import check_timing
from agro_model.resources import allocate_resources, validate_plan
from agronaut_agent.crop_records import CropRecords
from agronaut_agent.farm_resources import FarmResources, execute_farm_resources
from agronaut_agent.field_consultation import FieldConsultation
from agronaut_agent.store import ConversationStore
from agronaut_agent.tests.test_equipment_consultation import example
from agronaut_agent.tests.test_equipment_targets import budget, link
from agronaut_agent.tests.test_field_workflow import inputs, water
from agronaut_agent.tests.test_field_workflow import setup as setup


def specification(saved, day="2026-07-07"):
    return dict(calculation_id=saved["id"], day=day, job_id="irrigate", title="Full supplied equipment event", priority=1,
        water_resource_id="tank", pump_resource_id="pump", source="Synthetic operator-reviewed date, full-area applicability and pump/maintenance mapping",
        resources=[dict(id="tank", kind="water", unit="L", available="100000", source="Synthetic water availability"),
                   dict(id="pump", kind="pump_time", unit="h", available="24", source="Synthetic pump availability"),
                   dict(id="crew", kind="labor_time", unit="h", available="24", source="Synthetic crew availability")],
        other_requirements={"crew": "2"})


def resource_plan(preview, data):
    return dict(title="Synthetic linked event", source=data["source"], period=dict(start="2026-07-06", end="2026-07-08", timezone="UTC"),
                resources=data["resources"], jobs=[preview["job"]])


@pytest.mark.parametrize("task", ["drip_design", "sprinkler_design"])
def test_preview_save_exact_evidence_and_upward_rounding_are_read_only_until_save(setup, task):
    workflow, farms = setup
    source = workflow.save("owner", "field", task, example(task), cycle_id="cycle", request_id="equipment")
    service = FarmResources(workflow.db)
    data = specification(source)
    before = farms.export("owner")
    preview = execute_farm_resources(workflow.db, "owner", "farm", "design_job", dict(data=data))
    assert farms.export("owner") == before
    job = preview["job"]
    assert job["requested_blocks"] == job["minimum_blocks"] == 1
    assert job["unit_id"] == "field" and job["cycle_id"] == "cycle"
    for key, original in (("tank", "candidate_daily_upper_water_l"), ("pump", "candidate_daily_hours")):
        delta = Decimal(job["requirements"][key])-Decimal(str(source["result"]["result"][original]))
        assert 0 <= delta < Decimal("0.000001")
    assert job["requirements"]["crew"] == "2"
    saved = service.save("owner", "farm", resource_plan(preview, data), request_id="linked")
    assert saved["snapshot"]["irrigation_designs"][0]["calculation"] == source
    assert saved["snapshot"]["irrigation_designs"][0] == preview["evidence"]
    assert not service.get("owner", "farm", saved["id"])["review"]["needs_review"]
    assert service.save("owner", "farm", resource_plan(preview, data), request_id="linked") == saved
    exported = farms.export("owner")
    assert {k: v for k, v in exported.items() if k != "resource_plans"} == {k: v for k, v in before.items() if k != "resource_plans"}
    assert service.describe("owner", "farm")["irrigation_designs"][0]["id"] == source["id"]
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())


@pytest.mark.parametrize("kind", ["other_owner", "other_farm", "foreign_source", "failed", "water_only", "missing_pool", "missing_requirement", "override", "wrong_kind"])
def test_foreign_failed_and_incomplete_sources_cannot_be_adopted(setup, kind):
    workflow, _ = setup
    design = example("drip_design")
    if kind == "failed":
        design["supply"]["daily_available_l"] = 1
    task = "root_zone_budget" if kind == "water_only" else "drip_design"
    source = workflow.save("owner", "field", task, inputs() if task == "root_zone_budget" else design, cycle_id="cycle", request_id="equipment")
    data = specification(source)
    owner, farm = "owner", "farm"
    if kind == "other_owner":
        owner = "other"
    if kind == "other_farm":
        farm = "farm2"
    if kind == "foreign_source":
        data["calculation_id"] = workflow.save("other", "field3", "drip_design", design, request_id="foreign")["id"]
    if kind == "missing_pool":
        data["water_resource_id"] = "missing"
    if kind == "missing_requirement":
        data["other_requirements"] = {}
    if kind == "override":
        data["other_requirements"]["tank"] = "1"
    if kind == "wrong_kind":
        data["pump_resource_id"] = "crew"
    with pytest.raises(ValueError):
        FarmResources(workflow.db).design_job(owner, farm, data)


@pytest.mark.parametrize("change", ["water", "pump", "cycle", "unit", "blocks", "outside_day", "duplicate"])
def test_link_cannot_be_used_to_forge_or_scale_the_design_requirements(setup, change):
    workflow, _ = setup
    source = workflow.save("owner", "field", "drip_design", example("drip_design"), cycle_id="cycle", request_id="equipment")
    service = FarmResources(workflow.db)
    data = specification(source)
    plan = resource_plan(service.design_job("owner", "farm", data), data)
    job = plan["jobs"][0]
    if change in ("water", "pump"):
        job["requirements"]["tank" if change == "water" else "pump"] = "1"
    elif change in ("cycle", "unit"):
        job[change+"_id"] = None if change == "cycle" else "field2"
    elif change == "blocks":
        job["requested_blocks"] = 2
    elif change == "outside_day":
        job["irrigation_design"]["day"] = "2026-07-09"
    else:
        plan["jobs"].append(dict(job, id="duplicate", priority=2))
    with pytest.raises(ValueError):
        service.save("owner", "farm", plan, request_id="forged")
    assert service.list("owner", "farm")["plans"] == []


def test_current_crop_change_blocks_new_adoption_but_saved_evidence_remains_reviewable(setup):
    workflow, _ = setup
    source = workflow.save("owner", "field", "drip_design", example("drip_design"), cycle_id="cycle", request_id="equipment")
    service = FarmResources(workflow.db)
    data = specification(source)
    plan = resource_plan(service.design_job("owner", "farm", data), data)
    saved = service.save("owner", "farm", plan, request_id="linked")
    CropRecords(workflow.db).correct_planting("owner", "field", "cycle", "pepper", "2026-07-02", "Synthetic", "Corrected crop", request_id="amend", expected_revision=None)
    with pytest.raises(ValueError, match="need review"):
        service.design_job("owner", "farm", data)
    with pytest.raises(ValueError, match="need review"):
        service.save("owner", "farm", plan, request_id="new")
    assert service.save("owner", "farm", plan, request_id="linked") == saved
    current = service.get("owner", "farm", saved["id"])
    assert current["snapshot"] == saved["snapshot"]
    assert current["review"]["irrigation_designs"][0]["needs_review"]


def test_event_date_respects_planting_closure_and_saved_timezone_review(setup):
    workflow, _ = setup
    source = workflow.save("owner", "field", "drip_design", example("drip_design"), cycle_id="cycle", request_id="equipment")
    service = FarmResources(workflow.db)
    data = specification(source)
    with pytest.raises(ValueError, match="precede crop planting"):
        service.design_job("owner", "farm", dict(data, day="2026-06-30"))
    saved = service.save("owner", "farm", resource_plan(service.design_job("owner", "farm", data), data), request_id="linked")
    CropRecords(workflow.db).close_cycle("owner", "field", "cycle", "2026-07-06", "Synthetic crop end", "Synthetic", request_id="close", expected_revision=None)
    with pytest.raises(ValueError, match="after the crop cycle end"):
        service.design_job("owner", "farm", data)
    assert service.get("owner", "farm", saved["id"])["review"]["irrigation_designs"][0]["needs_review"]
    workflow.db.execute("UPDATE farms SET timezone='Europe/Paris' WHERE id='farm'")
    current = service.get("owner", "farm", saved["id"])
    assert current["review"]["timezone_changed"]
    assert current["inputs"]["period"]["timezone"] == "UTC"


def test_linked_event_intervals_and_proposed_windows_stay_on_the_declared_local_day(setup):
    workflow, _ = setup
    source = workflow.save("owner", "field", "drip_design", example("drip_design"), request_id="equipment")
    service = FarmResources(workflow.db)
    data = specification(source)
    plan = validate_plan(resource_plan(service.design_job("owner", "farm", data), data))
    timing = dict(source="Synthetic timing", work=[dict(id="run", job_id="irrigate", blocks=1,
        start="2026-07-07T08:00:00Z", end="2026-07-07T12:00:00Z", concurrent={"pump": 1, "crew": 1}, source="Synthetic 4-hour reserved interval")],
        storage=[dict(resource_id="tank", capacity_l="100000", initial_l="100000", minimum_l="0", inflows=[], external_withdrawals=[], source="Synthetic tank")],
        availability=[dict(resource_id=key, windows=[dict(id="day", start="2026-07-07T00:00:00Z", end="2026-07-08T00:00:00Z", units=1, source="Synthetic availability")]) for key in ("pump", "crew")])
    allocation = allocate_resources(plan)
    assert check_timing(timing, plan, allocation)["declared_checks_pass"]
    search = dict(source=timing["source"], step_seconds=3600, search_limit=100, storage=timing["storage"], availability=timing["availability"],
        runs=[dict(id="run", job_id="irrigate", blocks=1, duration_seconds=14400, concurrent={"pump": 1, "crew": 1},
                   windows=[dict(start="2026-07-07T08:00:00Z", end="2026-07-07T12:00:00Z")], source="Synthetic window")])
    assert propose_schedule(search, plan, allocation)["timing_result"]["declared_checks_pass"]
    for value in (timing["work"][0], search["runs"][0]["windows"][0]):
        value.update(start="2026-07-06T08:00:00Z", end="2026-07-06T12:00:00Z")
    with pytest.raises(ValueError):
        check_timing(timing, plan, allocation)
    with pytest.raises(ValueError):
        propose_schedule(search, plan, allocation)


def test_end_of_day_target_date_and_later_corrected_evidence_are_checked(setup):
    workflow, _ = setup
    source_inputs = inputs()
    source_inputs["initial_state"]["depletion_mm"] = 0
    source = budget(workflow, initial_state=source_inputs["initial_state"])
    wizard = FieldConsultation(workflow.db)
    view = link(wizard, source)
    saved = wizard.save("owner", "field", view["draft_id"], view["revision"])["calculation"]
    service = FarmResources(workflow.db)
    data = specification(saved)
    assert service.design_job("owner", "farm", data)["evidence"]["calculation"]["water_target"]["calculation_id"] == source["id"]
    with pytest.raises(ValueError, match="end-of-day"):
        service.design_job("owner", "farm", dict(data, day="2026-07-06"))
    # Correcting the source crop makes the water-target lineage stale too.
    CropRecords(workflow.db).correct_planting("owner", "field", "cycle", "tomato", "2026-07-02", "Synthetic", "Correction", request_id="planting", expected_revision=None)
    with pytest.raises(ValueError, match="review"):
        service.design_job("owner", "farm", data)


def test_corrected_water_evidence_flags_existing_job_and_prevents_new_adoption(setup):
    workflow, _ = setup
    source = budget(workflow, initial_state=dict(as_of="2026-07-05", depletion_mm=0, source="Synthetic low depletion"))
    actual = workflow.record_day("owner", "field", water(rainfall_mm=0, net_irrigation_mm=0, runoff_mm=0), cycle_id="cycle", request_id="actual")
    source = workflow.replay("owner", "field", source["id"], [actual["id"]], request_id="replayed")
    wizard = FieldConsultation(workflow.db)
    view = link(wizard, source)
    equipment = wizard.save("owner", "field", view["draft_id"], view["revision"])["calculation"]
    service = FarmResources(workflow.db)
    data = specification(equipment)
    plan = resource_plan(service.design_job("owner", "farm", data), data)
    saved = service.save("owner", "farm", plan, request_id="linked")
    workflow.record_day("owner", "field", water(rainfall_mm=1, net_irrigation_mm=0, runoff_mm=0), cycle_id="cycle", supersedes_id=actual["id"], request_id="correction")
    with pytest.raises(ValueError, match="water target needs review"):
        service.design_job("owner", "farm", data)
    current = service.get("owner", "farm", saved["id"])
    assert current["review"]["irrigation_designs"][0]["needs_review"]
    assert "corrected" in current["review"]["irrigation_designs"][0]["issues"][0]
    assert current["snapshot"] == saved["snapshot"]


def test_cli_preview_runs_without_site_packages_and_preserves_the_link(setup, tmp_path):
    workflow, _ = setup
    owner = ConversationStore(workflow.db).get_or_create_user("cli", "local")
    workflow.db.execute("UPDATE farms SET owner_id=? WHERE id='farm'", (owner,))
    source = workflow.save(owner, "field", "drip_design", example("drip_design"), request_id="equipment")
    path = tmp_path/"request.json"
    path.write_text(json.dumps(dict(data=specification(source))))
    command = [sys.executable, "-S", "-m", "agronaut_agent.cli", "resources", "--db", str(workflow.db.path), "--farm", "farm", "design_job", "--input", str(path)]
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout+result.stderr
    assert json.loads(result.stdout)["job"]["irrigation_design"]["calculation_id"] == source["id"]
