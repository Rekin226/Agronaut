"""Cross-plan physical equipment conflicts, immutable history and concurrent writers."""

import copy
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier, Thread

import pytest

from agronaut_agent.crop_records import CropRecords
from agronaut_agent.equipment_calendar import EquipmentCalendar
from agronaut_agent.farm_resources import execute_farm_resources
from agronaut_agent.farm_store import FarmStore
from agronaut_agent.store import SCHEMA_VERSION, ConversationStore, _Db
from agronaut_agent.tests.test_farm_resources import setup as setup
from agronaut_agent.tests.test_farm_store import seed
from agronaut_agent.tests.test_resource_timing import saved_scenario


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 19, 12, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr("agronaut_agent.equipment_calendar.datetime", Clock)
    monkeypatch.setattr("agronaut_agent.resource_ledger.datetime", Clock)


def asset(**changes):
    return dict(dict(equipment_key="pump-a", name="Shared pump A", kind="pump", status="available", source="Synthetic operator equipment inventory"), **changes)


def prepared(service):
    calendar = EquipmentCalendar(service.db)
    pump = calendar.save_equipment("owner", "farm", asset(), request_id="pump")
    parent, timing = saved_scenario(service)
    plan = service.save_timed("owner", "farm", parent["id"], timing, request_id="timed")
    return calendar, pump, plan


def book(calendar, pump, plan, key="book", **changes):
    args = dict(plan_id=plan["id"], assignments={w["id"]: [pump["id"]] for w in plan["timing_inputs"]["work"]},
                source="Operator confirmed equipment applicability", accept_partial=False, request_id=key)
    return calendar.book_equipment("owner", "farm", **dict(args, **changes))


def block(pump, start="2026-10-20T08:30:00Z", end="2026-10-20T09:30:00Z"):
    return dict(equipment_ids=[pump["id"]], start=start, end=end, title="Outside work or maintenance", source="Synthetic equipment diary")


def test_cross_plan_conflict_touching_endpoints_and_budget_independence(setup):
    service, farms = setup
    calendar, pump, plan = prepared(service)
    before = farms.export("owner")
    first = book(calendar, pump, plan)
    assert book(calendar, pump, plan) == first
    fresh = service.save_new_work("owner", "farm", plan["id"], plan["inputs"], "Another event", request_id="separate")
    other = service.save_timed("owner", "farm", fresh["id"], plan["timing_inputs"], request_id="second-timing")
    with pytest.raises(ValueError, match="equipment conflict"):
        book(calendar, pump, other, "overlap")
    with pytest.raises(ValueError, match="family"):
        book(calendar, pump, plan, "same-work")
    with pytest.raises(ValueError, match="equipment conflict"):
        calendar.reserve_equipment("owner", "farm", block(pump), request_id="maintenance")
    adjacent = calendar.reserve_equipment("owner", "farm", block(pump, "2026-10-20T10:00:00Z", "2026-10-20T11:00:00Z"), request_id="adjacent")
    view = calendar.equipment_calendar("owner", "farm")
    assert [r["id"] for r in view["active"]] == [first["id"], adjacent["id"]]
    assert first["plan_snapshot"] == plan and first["equipment_snapshots"][pump["root_id"]] == pump
    for key in ("resource_commitments", "resource_budgets", "observations", "crop_records", "water_days"):
        assert farms.export("owner")[key] == before[key]


def test_replacement_is_atomic_release_and_restore_keep_history(setup):
    service, _ = setup
    calendar, pump, plan = prepared(service)
    first = book(calendar, pump, plan)
    later_data = block(pump, "2026-10-20T10:00:00Z", "2026-10-20T11:00:00Z")
    calendar.reserve_equipment("owner", "farm", later_data, request_id="other")
    with pytest.raises(ValueError, match="equipment conflict"):
        calendar.reserve_equipment("owner", "farm", later_data, request_id="bad", supersedes_id=first["id"], reason="Move")
    assert calendar.equipment_calendar("owner", "farm")["active"][0]["id"] == first["id"]
    revised = book(calendar, pump, plan, "revise", supersedes_id=first["id"], reason="Recheck allocation")
    assert revised["root_id"] == first["root_id"]
    with pytest.raises(ValueError, match="superseded"):
        calendar.release_equipment("owner", "farm", first["id"], "Operator", "Cancel", request_id="stale")
    released = calendar.release_equipment("owner", "farm", revised["id"], "Operator", "Cancel unused booking", request_id="release")
    assert released["status"] == "released" and released["intervals"] == revised["intervals"]
    assert calendar.release_equipment("owner", "farm", revised["id"], "Operator", "Cancel unused booking", request_id="release") == released
    restored = book(calendar, pump, plan, "restore", supersedes_id=released["id"], reason="Reinstate work")
    assert restored["root_id"] == first["root_id"]
    with pytest.raises(ValueError, match="different data"):
        book(calendar, pump, plan, source="Changed source")
    assert book(calendar, pump, plan) == first


def test_equipment_changes_flag_held_intervals_and_stale_revisions_cannot_book(setup):
    service, _ = setup
    calendar, pump, plan = prepared(service)
    booked = book(calendar, pump, plan)
    corrected = calendar.save_equipment("owner", "farm", asset(status="unavailable"), request_id="broken",
                                       supersedes_id=pump["id"], reason="Operator reports breakdown")
    view = calendar.equipment_calendar("owner", "farm")
    assert view["active"][0]["review"]["unavailable_equipment"] == [pump["root_id"]]
    assert view["active"][0]["intervals"] == booked["intervals"]
    with pytest.raises(ValueError, match="superseded"):
        book(calendar, pump, plan, "stale", supersedes_id=booked["id"], reason="Retry")
    with pytest.raises(ValueError, match="unavailable"):
        book(calendar, corrected, plan, "broken", supersedes_id=booked["id"], reason="Retry")
    maintenance = calendar.reserve_equipment("owner", "farm", block(corrected, "2026-10-20T10:00:00Z", "2026-10-20T11:00:00Z"), request_id="repair")
    assert maintenance["equipment_snapshots"][pump["root_id"]] == corrected
    assert book(calendar, pump, plan) == booked


def test_changed_plan_evidence_keeps_booking_and_requires_review(setup):
    service, _ = setup
    calendar, pump, plan = prepared(service)
    booked = book(calendar, pump, plan)
    CropRecords(service.db).correct_planting("owner", "unit", "cycle", "pepper", "2026-07-02", "Synthetic", "Correction",
                                            request_id="amend", expected_revision=None)
    assert calendar.equipment_calendar("owner", "farm")["active"][0]["review"]["plan"]["needs_review"]
    with pytest.raises(ValueError, match="evidence changed"):
        book(calendar, pump, plan, "fresh-attempt", supersedes_id=booked["id"], reason="Rebook")
    assert book(calendar, pump, plan) == booked


@pytest.mark.parametrize("fault", ["missing", "duplicate", "wrong_kind", "no_pump", "foreign", "owner", "farm", "not_timed", "ended", "partial_flag", "extra"])
def test_invalid_assignments_or_scopes_are_atomic(setup, fault):
    service, farms = setup
    calendar, pump, plan = prepared(service)
    seed(farms, "owner", "second")
    payload = dict(plan_id=plan["id"], assignments={w["id"]: [pump["id"]] for w in plan["timing_inputs"]["work"]},
                   source="Synthetic", accept_partial=False, request_id="bad")
    owner, farm = "owner", "farm"
    if fault == "missing":
        payload["assignments"].pop("field")
    elif fault == "duplicate":
        payload["assignments"]["field"] *= 2
    elif fault == "wrong_kind":
        other = calendar.save_equipment("owner", "farm", asset(equipment_key="tractor", kind="tractor"), request_id="tractor")
        payload["assignments"]["field"] = [other["id"]]
    elif fault == "no_pump":
        payload["assignments"]["field"] = []
    elif fault == "foreign":
        other = calendar.save_equipment("owner", "farmsecond", asset(), request_id="foreign")
        payload["assignments"]["field"] = [other["id"]]
    elif fault == "owner":
        owner = "other"
    elif fault == "farm":
        farm = "farmsecond"
    elif fault == "not_timed":
        payload["plan_id"] = plan["parent_id"]
    elif fault == "ended":
        inputs = copy.deepcopy(plan["inputs"])
        inputs["period"].update(start="2026-10-18", end="2026-10-18")
        parent = service.save("owner", "farm", inputs, request_id="past")
        timing = json.loads(json.dumps(plan["timing_inputs"]).replace("2026-10-20", "2026-10-18"))
        payload["plan_id"] = service.save_timed("owner", "farm", parent["id"], timing, request_id="past-timing")["id"]
    elif fault == "partial_flag":
        payload["accept_partial"] = "yes"
    else:
        payload["approve"] = True
    before = farms.export("owner")
    with pytest.raises(ValueError):
        execute_farm_resources(service.db, owner, farm, "book_equipment", payload)
    assert farms.export("owner") == before


def test_simultaneous_work_needs_different_physical_pumps_even_if_pool_allows_two(setup):
    service, _ = setup
    calendar, pump, plan = prepared(service)
    timing = copy.deepcopy(plan["timing_inputs"])
    timing["work"][1].update(start=timing["work"][0]["start"], end=timing["work"][0]["end"])
    for a in timing["availability"]:
        a["windows"][0]["units"] *= 2
    concurrent = service.save_timed("owner", "farm", plan["id"], timing, request_id="concurrent")
    assert concurrent["timing_result"]["declared_checks_pass"]
    with pytest.raises(ValueError, match="equipment conflict"):
        book(calendar, pump, concurrent)
    other = calendar.save_equipment("owner", "farm", asset(equipment_key="pump-b"), request_id="other-pump")
    saved = book(calendar, pump, concurrent, assignments=dict(field=[pump["id"]], greenhouse=[other["id"]]))
    assert len(saved["equipment_snapshots"]) == 2


@pytest.mark.parametrize("start,end", [("2026-10-20T08:00:00", "2026-10-20T09:00:00Z"),
    ("2026-10-20T08:00:00+08:00", "2026-10-20T09:00:00+08:00"),
    ("2026-10-20T09:00:00Z", "2026-10-20T08:00:00Z"), ("2026-10-18T08:00:00Z", "2026-10-18T09:00:00Z")])
def test_manual_blocks_require_real_current_offset_intervals(setup, start, end):
    service, farms = setup
    calendar, pump, _ = prepared(service)
    before = farms.export("owner")
    with pytest.raises(ValueError):
        calendar.reserve_equipment("owner", "farm", block(pump, start, end), request_id="bad")
    assert farms.export("owner") == before


def test_dst_folds_compare_utc_and_timezone_changes_do_not_move_old_bookings(setup):
    service, _ = setup
    calendar, pump, _ = prepared(service)
    service.db.execute("UPDATE farms SET timezone='America/New_York' WHERE id='farm'")
    first = calendar.reserve_equipment("owner", "farm", block(pump, "2026-11-01T01:00:00-04:00", "2026-11-01T01:00:00-05:00"), request_id="fall")
    calendar.reserve_equipment("owner", "farm", block(pump, "2026-11-01T01:00:00-05:00", "2026-11-01T02:00:00-05:00"), request_id="touch")
    service.db.execute("UPDATE farms SET timezone='UTC' WHERE id='farm'")
    with pytest.raises(ValueError, match="equipment conflict"):
        calendar.reserve_equipment("owner", "farm", block(pump, "2026-11-01T05:30:00Z", "2026-11-01T06:30:00Z"), request_id="overlap")
    view = calendar.equipment_calendar("owner", "farm")
    assert view["active"][0]["review"]["timezone_changed"]
    assert view["active"][0]["intervals"] == first["intervals"]


def test_two_connections_cannot_book_the_same_item_twice(setup):
    service, _ = setup
    _, pump, _ = prepared(service)
    dbs = [_Db(service.db.path), _Db(service.db.path)]
    gate, results = Barrier(2), []

    def worker(index):
        gate.wait(timeout=10)
        try:
            results.append(EquipmentCalendar(dbs[index]).reserve_equipment("owner", "farm", block(pump), request_id=f"race-{index}"))
        except ValueError as exc:
            results.append(str(exc))

    threads = [Thread(target=worker, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive()
    for db in dbs:
        db._conn.close()
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, str) and "equipment conflict" in r for r in results) == 1


def test_asset_identity_corrections_and_owner_scope(setup):
    service, farms = setup
    calendar, pump, _ = prepared(service)
    before = farms.export("owner")
    for data, extra in ((asset(equipment_key="PUMP-A"), {}),
                        (asset(equipment_key="new-key"), dict(supersedes_id=pump["id"], reason="Rename identity")),
                        (asset(kind="tractor"), dict(supersedes_id=pump["id"], reason="Change kind")),
                        (asset(name="Corrected name"), dict(supersedes_id=pump["id"])),
                        (asset(source=""), {}), (asset(equipment_key="a duplicate alias?"), {})):
        with pytest.raises(ValueError):
            calendar.save_equipment("owner", "farm", data, request_id="bad", **extra)
    for operation in (lambda: calendar.equipment_calendar("other", "farm"),
                      lambda: calendar.save_equipment("other", "farm", asset(equipment_key="foreign"), request_id="bad")):
        with pytest.raises(ValueError):
            operation()
    assert farms.export("owner") == before
    changed = calendar.save_equipment("owner", "farm", asset(name="New label"), request_id="rename", supersedes_id=pump["id"], reason="Label correction")
    assert changed["root_id"] == pump["root_id"]
    assert calendar.save_equipment("owner", "farm", asset(), request_id="pump") == pump
    with pytest.raises(ValueError, match="superseded"):
        calendar.save_equipment("owner", "farm", asset(name="Stale label"), request_id="stale", supersedes_id=pump["id"], reason="Stale")


def test_partial_work_requires_acceptance_and_other_equipment_can_be_assigned(setup):
    service, _ = setup
    calendar, pump, plan = prepared(service)
    data = copy.deepcopy(plan["inputs"])
    data["resources"][0]["available"] = "500"
    partial = service.save("owner", "farm", data, request_id="partial")
    timing = copy.deepcopy(plan["timing_inputs"])
    timing["work"] = timing["work"][:1]
    timed = service.save_timed("owner", "farm", partial["id"], timing, request_id="partial-timed")
    assert timed["timing_result"]["declared_checks_pass"]
    tractor = calendar.save_equipment("owner", "farm", asset(equipment_key="tractor", kind="tractor"), request_id="tractor")
    assignments = dict(field=[pump["id"], tractor["id"]])
    with pytest.raises(ValueError, match="accept_partial"):
        book(calendar, pump, timed, assignments=assignments)
    result = book(calendar, pump, timed, assignments=assignments, accept_partial=True)
    assert len(result["intervals"]) == 2 and {r["job_id"] for r in result["intervals"]} == {"field"}
    failed = copy.deepcopy(plan["timing_inputs"])
    failed["work"][1].update(start=failed["work"][0]["start"], end=failed["work"][0]["end"])
    invalid = service.save_timed("owner", "farm", plan["id"], failed, request_id="failed")
    assert not invalid["timing_result"]["declared_checks_pass"]
    with pytest.raises(ValueError, match="passing timing"):
        book(calendar, pump, invalid, key="failed")


def test_export_erasure_and_v13_migration_failure_recovery(setup, monkeypatch):
    import agronaut_agent.equipment_calendar as module

    service, farms = setup
    calendar, pump, plan = prepared(service)
    booking = book(calendar, pump, plan)
    seed(farms, "other", "other")
    foreign = calendar.save_equipment("other", "farmother", asset(), request_id="foreign")
    assert farms.export("owner")["equipment_bookings"] == [booking]
    farms.purge("owner")
    assert all(not entries for entries in farms.export("owner").values())
    assert farms.export("other")["equipment"] == [foreign]
    # Independently remove the additive tables to model a real v13 database.
    service.db.execute("DROP TABLE farm_equipment_bookings")
    service.db.execute("DROP TABLE farm_equipment")
    service.db.execute("PRAGMA user_version=13")
    path = service.db.path
    service.db._conn.close()
    migrate = module.migrate_equipment_calendar

    def fail(conn):
        migrate(conn)
        raise RuntimeError("Synthetic interrupted equipment migration")

    monkeypatch.setattr(module, "migrate_equipment_calendar", fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        _Db(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 13
    assert raw.execute("SELECT name FROM sqlite_master WHERE name='farm_equipment'").fetchone() is None
    assert raw.execute("SELECT count(*) FROM farms").fetchone()[0] == 1
    raw.close()
    backups = list(path.parent.glob(f"*.pre-v{SCHEMA_VERSION}-*.bak"))
    assert len(backups) == 1 and backups[0].stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(module, "migrate_equipment_calendar", migrate)
    restored = _Db(path)
    assert len(FarmStore(restored).export("other")["farms"]) == 1
    assert FarmStore(restored).export("other")["equipment"] == []
    restored._conn.close()


def test_stdlib_cli_equipment_booking_release_and_discovery(setup, tmp_path):
    service, _ = setup
    _, _, plan = prepared(service)
    owner = ConversationStore(service.db).get_or_create_user("cli", "local")
    service.db.execute("UPDATE farms SET owner_id=? WHERE id='farm'", (owner,))
    path = tmp_path/"equipment-request.json"

    def command(operation, data):
        path.write_text(json.dumps(data))
        process = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", "resources", "--db", str(service.db.path),
            "--farm", "farm", operation, "--input", str(path)], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
        assert process.returncode == 0, process.stdout+process.stderr
        return json.loads(process.stdout)

    assert "book_equipment" in command("describe", {})["operations"]
    pump = command("save_equipment", dict(data=asset(equipment_key="cli-pump"), request_id="cli-pump"))
    # Use future dates relative to the actual clock in the subprocess.
    from datetime import timedelta
    tomorrow = (datetime.now(timezone.utc)+timedelta(days=1)).date().isoformat()
    booked = command("reserve_equipment", dict(data=block(pump, tomorrow+"T08:00:00Z", tomorrow+"T09:00:00Z"), request_id="cli-book"))
    assert command("equipment_calendar", {})["active"][0]["id"] == booked["id"]
    assert len(command("equipment_inputs", dict(plan_id=plan["id"]))["work"]) == 2
    released = command("release_equipment", dict(booking_id=booked["id"], source="CLI operator", reason="Cancelled", request_id="cli-release"))
    assert released["status"] == "released" and command("equipment_calendar", {})["active"] == []


def test_v13_upgrade_keeps_existing_budget_charges_and_older_reader_refuses_new_schema(setup, monkeypatch):
    import agronaut_agent.store as store
    from agronaut_agent.tests.test_resource_ledger import commit
    from agronaut_agent.tests.test_resource_ledger import prepared as ledger_prepared

    service, farms = setup
    ledger, pool, plan = ledger_prepared(service)
    commit(ledger, pool, plan)
    original = farms.export("owner")
    service.db.execute("DROP TABLE farm_equipment_bookings")
    service.db.execute("DROP TABLE farm_equipment")
    service.db.execute("PRAGMA user_version=13")
    service.db._conn.close()
    restored = _Db(service.db.path)
    assert FarmStore(restored).export("owner") == original
    assert restored.query("PRAGMA user_version")[0][0] == SCHEMA_VERSION
    restored._conn.close()
    monkeypatch.setattr(store, "SCHEMA_VERSION", 13)
    with pytest.raises(store.SchemaTooNewError):
        _Db(service.db.path)
