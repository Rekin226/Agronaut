"""Calendar snapshots, explicit replacement, races and owned equipment proposals."""

import copy
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agro_model.tests.test_resource_schedule import request
from agronaut_agent.crop_records import CropRecords
from agronaut_agent.equipment_schedule import EquipmentScheduling
from agronaut_agent.farm_resources import execute_farm_resources
from agronaut_agent.store import ConversationStore
from agronaut_agent.tests.test_equipment_calendar import asset, block, book, prepared
from agronaut_agent.tests.test_farm_resources import setup as setup
from agronaut_agent.tests.test_farm_store import seed


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 19, 12, tzinfo=timezone.utc).astimezone(tz)
    for module in ("equipment_schedule", "equipment_calendar", "resource_ledger"):
        monkeypatch.setattr("agronaut_agent."+module+".datetime", Clock)


def inputs(plan, pump):
    schedule = request(plan["timing_inputs"])
    for row in schedule["runs"]:
        row["windows"][0]["end"] = "2026-10-20T12:00:00Z"
    for row in schedule["availability"]:
        row["windows"][0]["end"] = "2026-10-20T12:00:00Z"
    return dict(schedule=schedule, assignments={r["id"]: [pump["id"]] for r in schedule["runs"]},
                source="Operator reviewed fixed pump applicability", replaces_booking_id=None)


def search(service, plan, data, key=None):
    args = dict(plan_id=plan["id"], data=data)
    if key is not None:
        args["request_id"] = key
    return execute_farm_resources(service.db, "owner", "farm", "save_equipment_schedule" if key else "propose_equipment_schedule", args)


def test_proposal_reads_busy_calendar_save_retry_export_and_booking_are_separate(setup):
    service, farms = setup
    calendar, pump, plan = prepared(service)
    occupied = calendar.reserve_equipment("owner", "farm", block(pump, "2026-10-20T07:00:00Z", "2026-10-20T09:00:00Z"), request_id="outside")
    data = inputs(plan, pump)
    before = farms.export("owner")
    proposal = search(service, plan, data)
    assert farms.export("owner") == before
    assert [w["start"] for w in proposal["timing_inputs"]["work"]] == ["2026-10-20T09:00:00+00:00", "2026-10-20T10:00:00+00:00"]
    saved = search(service, plan, data, "saved")
    from agro_model.resource_schedule import propose_schedule
    replayed = propose_schedule(dict(saved["schedule_inputs"], equipment=saved["equipment_schedule"]["constraints"]), saved["inputs"], saved["result"])
    assert {key: saved[key] for key in replayed} == replayed
    assert saved["parent_id"] == plan["id"]
    assert saved["equipment_schedule"]["snapshot"]["bookings"][0]["id"] == occupied["id"]
    assert "plan_snapshot" not in saved["equipment_schedule"]["snapshot"]["bookings"][0]
    assert search(service, plan, data, "saved") == saved
    assert not service.get("owner", "farm", saved["id"])["equipment_schedule_review"]["changed"]
    assert farms.export("owner")["equipment_bookings"] == before["equipment_bookings"]
    booked = book(calendar, pump, saved)
    assert booked["plan_snapshot"] == saved
    after = farms.export("owner")
    for key in ("resource_commitments", "resource_budgets", "crop_records", "water_days", "observations"):
        assert after[key] == before[key]
    assert search(service, plan, data, "saved") == saved  # Retry does not recompute after booking.
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())


def test_later_conflict_flags_original_proposal_and_booking_rechecks_current_state(setup):
    service, _ = setup
    calendar, pump, plan = prepared(service)
    saved = search(service, plan, inputs(plan, pump), "saved")
    original = copy.deepcopy(saved)
    calendar.reserve_equipment("owner", "farm", block(pump), request_id="late")
    fetched = service.get("owner", "farm", saved["id"])
    assert fetched["equipment_schedule_review"]["changed"]
    compared = service.compare("owner", "farm", [plan["id"], saved["id"]])
    assert compared["scenarios"][1]["equipment_schedule_review"]["changed"]
    assert {k: fetched[k] for k in original} == original
    with pytest.raises(ValueError, match="equipment conflict"):
        book(calendar, pump, saved)
    assert len(calendar.equipment_calendar("owner", "farm")["active"]) == 1


def test_only_current_same_family_booking_can_be_excluded_and_replacement_stays_explicit(setup):
    service, _ = setup
    calendar, pump, plan = prepared(service)
    prior = book(calendar, pump, plan)
    data = inputs(plan, pump)
    with pytest.raises(ValueError, match="explicitly select"):
        search(service, plan, data)
    data["replaces_booking_id"] = prior["id"]
    saved = search(service, plan, data, "replacement-proposal")
    assert saved["equipment_schedule"]["snapshot"]["excluded_booking"]["id"] == prior["id"]
    assert calendar.equipment_calendar("owner", "farm")["active"][0]["id"] == prior["id"]
    with pytest.raises(ValueError, match="family"):
        book(calendar, pump, saved, "duplicate")
    replaced = book(calendar, pump, saved, "replacement", supersedes_id=prior["id"], reason="Adopt reviewed proposal")
    assert replaced["root_id"] == prior["root_id"]
    with pytest.raises(ValueError, match="superseded"):
        search(service, plan, data)
    assert search(service, plan, data, "replacement-proposal") == saved


@pytest.mark.parametrize("change", ["calendar", "equipment", "crop", "erase", "unrelated"])
def test_search_releases_transaction_then_rechecks_owner_and_relevant_calendar(setup, monkeypatch, change):
    import agronaut_agent.equipment_schedule as module
    service, farms = setup
    calendar, pump, plan = prepared(service)
    data = inputs(plan, pump)
    other = calendar.save_equipment("owner", "farm", asset(equipment_key="other-pump"), request_id="other")
    real = module.propose_schedule

    def race(*args):
        assert not service.db._conn.in_transaction
        result = real(*args)
        if change == "erase":
            farms.purge("owner")
        elif change == "equipment":
            calendar.save_equipment("owner", "farm", asset(status="unavailable"), request_id="broken", supersedes_id=pump["id"], reason="Breakdown")
        elif change == "crop":
            CropRecords(service.db).correct_planting("owner", "unit", "cycle", "pepper", "2026-07-02", "Synthetic", "Correction", request_id="amend", expected_revision=None)
        else:
            calendar.reserve_equipment("owner", "farm", block(other if change == "unrelated" else pump), request_id="late")
        return result

    monkeypatch.setattr(module, "propose_schedule", race)
    if change == "unrelated":
        assert search(service, plan, data, "race")["schedule_result"]["status"] == "feasible_schedule_found"
    else:
        with pytest.raises(ValueError):
            search(service, plan, data, "race")
        assert not service.db.query("SELECT id FROM farm_resource_plans WHERE request_id='race'")


@pytest.mark.parametrize("fault", ["foreign_equipment", "foreign_farm", "foreign_owner", "exclude_other", "exclude_released", "wrong_count", "raw_busy", "incomplete"])
def test_invalid_or_foreign_requests_write_nothing(setup, fault):
    service, farms = setup
    calendar, pump, plan = prepared(service)
    seed(farms, "owner", "second")
    data = inputs(plan, pump)
    owner, farm = "owner", "farm"
    if fault == "foreign_equipment":
        foreign = calendar.save_equipment("owner", "farmsecond", asset(), request_id="foreign")
        data["assignments"]["field"] = [foreign["id"]]
    elif fault == "foreign_farm":
        farm = "farmsecond"
    elif fault == "foreign_owner":
        owner = "other"
    elif fault == "exclude_other":
        data["replaces_booking_id"] = calendar.reserve_equipment("owner", "farm", block(pump), request_id="outside")["id"]
    elif fault == "exclude_released":
        booked = book(calendar, pump, plan)
        released = calendar.release_equipment("owner", "farm", booked["id"], "Operator", "Cancel", request_id="release")
        data["replaces_booking_id"] = released["id"]
    elif fault == "wrong_count":
        data["assignments"]["field"] = []
    elif fault == "raw_busy":
        data["schedule"]["equipment"] = {}
    else:
        del data["assignments"]["field"]
    before = farms.export("owner")
    with pytest.raises(ValueError):
        EquipmentScheduling(service.db).save_equipment_schedule(owner, farm, plan["id"], data, request_id="bad")
    assert farms.export("owner") == before


def test_failed_and_limited_search_receipts_are_saved_without_timing_or_bookings(setup):
    service, farms = setup
    _, pump, plan = prepared(service)
    data = inputs(plan, pump)
    data["schedule"]["search_limit"] = 1
    limited = search(service, plan, data, "limited")
    assert limited["schedule_result"]["status"] == "search_limit_reached" and "timing_inputs" not in limited
    data["schedule"]["search_limit"] = 100
    data["schedule"]["runs"][0]["windows"] = []
    failed = search(service, plan, data, "impossible")
    assert failed["schedule_result"]["status"] == "no_feasible_schedule_on_grid" and "timing_inputs" not in failed
    assert farms.export("owner")["equipment_bookings"] == []
    with pytest.raises(ValueError, match="different data"):
        search(service, plan, data, "limited")


def test_standard_library_cli_discovers_proposes_and_saves(setup, tmp_path):
    service, _ = setup
    _, pump, plan = prepared(service)
    owner = ConversationStore(service.db).get_or_create_user("cli", "local")
    service.db.execute("UPDATE farms SET owner_id=? WHERE id='farm'", (owner,))
    future = (datetime.now(timezone.utc)+timedelta(days=1)).date().isoformat()
    data = inputs(plan, pump)
    inputs_plan = copy.deepcopy(plan["inputs"])
    inputs_plan["period"].update(start=future, end=future)
    parent = service.save(owner, "farm", inputs_plan, request_id="future")
    data = json.loads(json.dumps(data).replace("2026-10-20", future))
    path = tmp_path/"equipment-schedule.json"

    def command(operation, payload):
        path.write_text(json.dumps(payload))
        process = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", "resources", "--db", str(service.db.path),
            "--farm", "farm", operation, "--input", str(path)], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
        assert process.returncode == 0, process.stdout+process.stderr
        return json.loads(process.stdout)

    assert "save_equipment_schedule" in command("describe", {})["operations"]
    assert command("equipment_schedule_inputs", dict(plan_id=parent["id"]))["equipment"][0]["id"] == pump["id"]
    proposal = command("propose_equipment_schedule", dict(plan_id=parent["id"], data=data))
    assert proposal["schedule_result"]["equipment_checks_pass"]
    saved = command("save_equipment_schedule", dict(plan_id=parent["id"], data=data, request_id="cli-save"))
    assert command("get", dict(plan_id=saved["id"]))["equipment_schedule"] == saved["equipment_schedule"]
    assert command("equipment_calendar", {})["active"] == []
