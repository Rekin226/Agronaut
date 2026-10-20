"""Field-zone identities preserve unit isolation, ownership and migration history."""

import copy
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier, Thread

import pytest

from agro_model.farm import Observation, ProductionCycle, ProductionUnit
from agronaut_agent.farm_resources import FarmResources
from agronaut_agent.farm_store import FarmStore
from agronaut_agent.field_workflow import execute_field_workflow
from agronaut_agent.irrigation_zones import IrrigationZones
from agronaut_agent.store import SCHEMA_VERSION, ConversationStore, MemoryStore, _Db
from agronaut_agent.tests.test_field_workflow import inputs, water
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_resource_ledger import plan as resource_plan
from agronaut_agent.unit_context import UnitContexts


def zone(**changes):
    return dict(dict(zone_key="north-drip", name="North drip zone", water_strategy="supplemental", irrigation_method="drip",
        ground_area_m2="500.25", boundary_note="Rows 1–10 inside the north field boundary", source="Synthetic operator zone declaration"), **changes)


def test_zone_identity_retry_same_field_key_and_discovery_do_not_copy_parent_records(setup):
    workflow, farms = setup
    db = workflow.db
    contexts = UnitContexts(db)
    parent = contexts.select("owner", "field")
    MemoryStore(db).set_fact(parent.context_id, "soil_ph", "6.8", "Parent-only measurement")
    workflow.record_day("owner", "field", water(), request_id="parent-day", cycle_id="cycle")
    before = farms.export("owner")
    service = IrrigationZones(db)
    saved = execute_field_workflow(db, "owner", "field", "create_zone", dict(data=zone(), request_id="zone"))
    assert service.create("owner", "field", zone(), request_id="zone") == saved
    assert saved["parent_unit_id"] == "field" and saved["data"]["ground_area_m2"] == "500.25"
    assert contexts.active("owner") == parent  # Scoped tool creation does not switch mid-turn.
    assert execute_field_workflow(db, "owner", "field", "describe_zones", {})["zones"] == [saved]
    child = contexts.select("owner", saved["id"])
    assert child.parent_unit_id == "field" and child.context_id != parent.context_id
    assert MemoryStore(db).get_facts(child.context_id) == {}
    assert farms.list_cycles("owner", saved["id"]) == []
    assert farms.observations("owner", saved["id"]) == []
    assert workflow.list("owner", saved["id"])["water_days"] == []
    listed = next(u for u in farms.list_units("owner") if u["id"] == saved["id"])
    assert listed["parent_name"] == "Field" and listed["parent_unit_id"] == "field"
    assert execute_field_workflow(db, "owner", saved["id"], "list_zones", {})["zone"] == saved
    assert farms.export("owner")["water_days"] == before["water_days"]
    with pytest.raises(ValueError, match="different data"):
        service.create("owner", "field", zone(name="Changed identity"), request_id="zone")
    with pytest.raises(ValueError, match="already exists"):
        service.create("owner", "field", zone(zone_key="NORTH-DRIP"), request_id="duplicate")
    another_field = service.create("owner", "field2", zone(ground_area_m2=None), request_id="zone")
    assert another_field["id"] != saved["id"] and another_field["data"]["ground_area_m2"] is None


def test_each_zone_has_independent_cycles_measurements_water_and_calculation_snapshots(setup):
    workflow, farms = setup
    service = IrrigationZones(workflow.db)
    a = service.create("owner", "field", zone(), request_id="a")
    b = service.create("owner", "field", zone(zone_key="south-sprinkler", name="South sprinkler", irrigation_method="sprinkler"), request_id="b")
    farms.create_cycle("owner", ProductionCycle("zone-cycle", a["id"], "tomato", "2026-07-01"))
    for cycle in ("cycle", "cycle2"):
        with pytest.raises(ValueError, match="cycle does not belong"):
            workflow.save("owner", a["id"], "root_zone_budget", inputs(), request_id="bad", cycle_id=cycle)
    payload = inputs()
    payload["area_m2"] = 500.25
    saved = workflow.save("owner", a["id"], "root_zone_budget", payload, request_id="plan", cycle_id="zone-cycle")
    assert saved["zone_snapshot"] == a
    actual = workflow.record_day("owner", a["id"], water(), request_id="day", cycle_id="zone-cycle")
    replayed = workflow.replay("owner", a["id"], saved["id"], [actual["id"]], request_id="replayed")
    assert replayed["zone_snapshot"] == a and replayed["applied_water_record_ids"] == [actual["id"]]
    for wrong in ("field", b["id"]):
        with pytest.raises(ValueError):
            workflow.get("owner", wrong, saved["id"])
        with pytest.raises(ValueError):
            workflow.replay("owner", wrong, saved["id"], [actual["id"]], request_id="wrong")
        assert workflow.list("owner", wrong)["calculations"] == []
    now = datetime.now(timezone.utc)
    measured = Observation("zone-reading", "owner", a["id"], "zone-cycle", "soil_water_content", 0.25, "m3/m3",
                           now, now, "measured", "Zone sample")
    farms.record_observation("owner", measured)
    assert len(farms.observations("owner", a["id"])) == 1
    assert farms.observations("owner", b["id"]) == farms.observations("owner", "field") == []


def test_shared_resource_plan_retains_zone_definition_and_marks_parent_zone_overlap(setup):
    workflow, _ = setup
    a = IrrigationZones(workflow.db).create("owner", "field", zone(), request_id="a")
    data = resource_plan("600", a["id"])
    data["period"]["timezone"] = "UTC"
    data["jobs"].append(dict(data["jobs"][0], id="whole-field", unit_id="field", priority=2, requirements=dict(water="100")))
    result = FarmResources(workflow.db).save("owner", "farm", data, request_id="mixed-scope")
    assert result["snapshot"]["irrigation_zones"] == [a]
    assert result["snapshot"]["parent_zone_overlap"] == [dict(parent_unit_id="field", zone_unit_id=a["id"])]
    assert result["result"]["resources"][0]["assigned"] == "600"  # Each explicit task still obeys the shared limit.


@pytest.mark.parametrize("fault", ["owner", "aquatic", "nested", "unknown_environment", "zero", "negative", "float", "missing_source", "missing_boundary", "bad_key", "rainfed", "bad_method", "extra_id"])
def test_invalid_zone_creation_is_atomic(setup, fault):
    workflow, farms = setup
    service = IrrigationZones(workflow.db)
    data, owner, parent = zone(), "owner", "field"
    if fault == "owner":
        owner = "other"
    elif fault == "aquatic":
        farms.create_unit("owner", ProductionUnit("pond", "farm", "Pond", "aquaponics", "water", "greenhouse", "fully_irrigated"))
        parent = "pond"
    elif fault == "nested":
        parent = service.create("owner", "field", zone(), request_id="parent")["id"]
    elif fault == "unknown_environment":
        workflow.db.execute("UPDATE production_units SET environment=NULL WHERE id='field'")
    elif fault in ("zero", "negative", "float"):
        data["ground_area_m2"] = {"zero": "0", "negative": "-1", "float": 20.5}[fault]
    elif fault == "missing_source":
        data["source"] = " "
    elif fault == "missing_boundary":
        data["boundary_note"] = ""
    elif fault == "bad_key":
        data["zone_key"] = "an ambiguous alias?"
    elif fault == "rainfed":
        data["water_strategy"] = "rainfed"
    elif fault == "bad_method":
        data["irrigation_method"] = "fog"
    else:
        data["parent_unit_id"] = "field3"
    before = farms.export("owner")
    with pytest.raises(ValueError):
        service.create(owner, parent, data, request_id="bad")
    assert farms.export("owner") == before


def test_zone_parent_cannot_be_injected_through_regular_unit_setup(setup):
    from agronaut_agent.farm_setup import create_unit
    workflow, farms = setup
    spec = dict(farm=dict(id="farm"), unit=dict(name="Bypass", production_system="soil", medium="soil", environment="open_field",
        water_strategy="supplemental", irrigation_method="drip", parent_unit_id="field"))
    with pytest.raises(ValueError):
        create_unit(workflow.db, "owner", spec)
    with pytest.raises(ValueError, match="IrrigationZones"):
        farms.create_unit("owner", ProductionUnit("bypass", "farm", "Bypass", "soil", "soil", "open_field", "supplemental", "drip", "field"))
    assert len(farms.list_units("owner")) == 2


def test_export_erasure_removes_child_contexts_and_keeps_other_owner(setup):
    workflow, farms = setup
    service = IrrigationZones(workflow.db)
    a = service.create("owner", "field", zone(), request_id="mine")
    b = service.create("other", "field3", zone(), request_id="theirs")
    child = UnitContexts(workflow.db).select("owner", a["id"])
    MemoryStore(workflow.db).set_fact(child.context_id, "crop", "tomato", "Zone operator")
    ConversationStore(workflow.db).append_message(child.context_id, "user", "Zone-specific note")
    assert farms.export("owner")["irrigation_zones"] == [a]
    foreign = farms.export("other")
    farms.purge("owner")
    assert all(not entries for entries in farms.export("owner").values())
    assert UnitContexts(workflow.db).active("owner") is None
    assert MemoryStore(workflow.db).get_facts(child.context_id) == {}
    assert farms.export("other") == foreign and foreign["irrigation_zones"] == [b]


def test_v14_upgrade_and_failure_roll_back_hierarchy_with_recoverable_backup(setup, monkeypatch):
    import agronaut_agent.irrigation_zones as module
    workflow, farms = setup
    workflow.save("owner", "field", "root_zone_budget", inputs(), request_id="old-plan", cycle_id="cycle")
    before = copy.deepcopy(farms.export("owner"))
    workflow.db.execute("DROP TABLE irrigation_zones")
    workflow.db.execute("DROP INDEX idx_units_parent")
    workflow.db.execute("ALTER TABLE production_units DROP COLUMN parent_unit_id")
    workflow.db.execute("PRAGMA user_version=14")
    path = workflow.db.path
    workflow.db._conn.close()
    migrate = module.migrate_irrigation_zones

    def fail(conn):
        migrate(conn)
        raise RuntimeError("Synthetic interrupted zone migration")

    monkeypatch.setattr(module, "migrate_irrigation_zones", fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        _Db(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 14
    assert "parent_unit_id" not in {r[1] for r in raw.execute("PRAGMA table_info(production_units)")}
    assert raw.execute("SELECT name FROM sqlite_master WHERE name='irrigation_zones'").fetchone() is None
    raw.close()
    backups = list(path.parent.glob(f"*.pre-v{SCHEMA_VERSION}-*.bak"))
    assert len(backups) == 1 and backups[0].stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(module, "migrate_irrigation_zones", migrate)
    restored = _Db(path)
    assert FarmStore(restored).export("owner") == before
    assert restored.query("PRAGMA user_version")[0][0] == SCHEMA_VERSION
    restored._conn.close()
    import agronaut_agent.store as store
    monkeypatch.setattr(store, "SCHEMA_VERSION", 14)
    with pytest.raises(store.SchemaTooNewError):
        _Db(path)


def test_concurrent_duplicate_zone_keys_do_not_create_orphan_units(setup):
    workflow, farms = setup
    before = len(farms.list_units("owner"))
    dbs = [_Db(workflow.db.path), _Db(workflow.db.path)]
    gate, results = Barrier(2), []

    def worker(index):
        gate.wait(timeout=10)
        try:
            results.append(IrrigationZones(dbs[index]).create("owner", "field", zone(), request_id=f"zone-{index}"))
        except ValueError as exc:
            results.append(str(exc))

    threads = [Thread(target=worker, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive()
    for db in dbs:
        db._conn.close()
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, str) and "already exists" in r for r in results) == 1
    assert len(farms.list_units("owner")) == before+1
    assert len(farms.export("owner")["irrigation_zones"]) == 1


def test_stdlib_cli_creates_selects_lists_and_keeps_parent_selection_for_field_tool(tmp_path):
    db, payload = tmp_path/"cli.sqlite3", tmp_path/"zone.json"
    root = Path(__file__).resolve().parents[2]

    def command(*args):
        run = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", *args], cwd=root, capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stdout+run.stderr
        return json.loads(run.stdout)

    parent = command("farm", "--db", str(db), "create", "--input", str(root/"examples/soil_unit.json"))["created"]
    payload.write_text(json.dumps(dict(data=zone(), request_id="zone")))
    created = command("farm", "--db", str(db), "create-zone", "--unit", parent["id"], "--input", str(payload))
    assert created["active"]["unit_id"] == created["created"]["id"]
    assert created["active"]["parent_unit_id"] == parent["id"]
    assert command("farm", "--db", str(db), "zones", "--unit", parent["id"])["zones"] == [created["created"]]
    command("farm", "--db", str(db), "select", "--unit", parent["id"])
    payload.write_text(json.dumps(dict(data=zone(zone_key="south"), request_id="south")))
    second = command("field", "--db", str(db), "create_zone", "--input", str(payload))
    listed = command("farm", "--db", str(db), "list")
    assert listed["active"]["unit_id"] == parent["id"] and len(listed["units"]) == 3
    assert second["id"] != created["created"]["id"]
