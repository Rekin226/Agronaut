"""Farm ownership, immutable scenarios, migration recovery and the offline CLI."""

import copy
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agro_model.farm import ProductionUnit
from agro_model.tests.test_resources import plan
from agronaut_agent.crop_records import CropRecords
from agronaut_agent.farm_resources import FarmResources, execute_farm_resources
from agronaut_agent.farm_store import FarmStore
from agronaut_agent.store import SCHEMA_VERSION, _Db
from agronaut_agent.tests.test_farm_store import seed


@pytest.fixture
def setup(tmp_path):
    db = _Db(tmp_path/"farm.sqlite3")
    farms = FarmStore(db)
    seed(farms)
    for key, system in (("hydro", "hydroponics"), ("pond", "aquaponics")):
        farms.create_unit("owner", ProductionUnit(key, "farm", key, system, "water", "greenhouse", "fully_irrigated"))
    yield FarmResources(db), farms
    db._conn.close()


def test_evaluate_is_read_only_and_save_retry_parent_comparison_are_immutable(setup):
    service, farms = setup
    before = farms.export("owner")
    evaluated = service.evaluate("owner", "farm", plan())
    assert [r["allocated_blocks"] for r in evaluated["result"]["allocations"]] == [10, 4, 1]
    assert farms.export("owner") == before
    saved = service.save("owner", "farm", plan(), request_id="first")
    assert service.save("owner", "farm", plan(), request_id="first") == saved
    changed = plan()
    changed["jobs"][0]["priority"], changed["jobs"][1]["priority"] = 2, 1
    alternate = service.save("owner", "farm", changed, request_id="second", parent_id=saved["id"])
    compared = service.compare("owner", "farm", [alternate["id"], saved["id"]])
    assert [r["id"] for r in compared["scenarios"]] == [alternate["id"], saved["id"]]
    assert compared["scenarios"][0]["result"]["allocations"][0]["allocated_blocks"] == 8
    assert service.get("owner", "farm", saved["id"])["inputs"] == saved["inputs"]
    assert len(service.list("owner", "farm")["plans"]) == 2
    assert farms.export("owner")["resource_plans"] == [saved, alternate]
    assert farms.export("owner")["crop_records"] == []
    assert farms.export("owner")["water_days"] == []


def test_farm_unit_cycle_parent_and_retry_scopes_reject_cross_farm_inputs(setup):
    service, farms = setup
    seed(farms, "other", "other")
    seed(farms, "owner", "same-owner")
    saved = service.save("owner", "farm", plan(), request_id="plan")
    for farm_id in ("farmother", "farmsame-owner", "unknown"):
        with pytest.raises(ValueError):
            service.save("owner", farm_id, plan(), request_id="wrong")
    with pytest.raises(ValueError, match="unknown farm"):
        service.get("other", "farm", saved["id"])
    for field, value in (("unit_id", "unitother"), ("unit_id", "unitsame-owner"), ("cycle_id", "cycleother")):
        inputs = plan()
        inputs["jobs"][0][field] = value
        with pytest.raises(ValueError):
            service.evaluate("owner", "farm", inputs)
    with pytest.raises(ValueError, match="different data"):
        service.save("owner", "farm", dict(plan(), title="Changed"), request_id="plan")
    with pytest.raises(ValueError, match="unknown resource plan"):
        service.save("owner", "farm", plan(), request_id="bad-parent", parent_id="missing")
    with pytest.raises(ValueError, match="timezone"):
        inputs = plan()
        inputs["period"]["timezone"] = "UTC"
        service.evaluate("owner", "farm", inputs)
    assert len(service.list("owner", "farm")["plans"]) == 1


def test_saved_cycle_assumptions_are_flagged_without_rewriting_evaluation_or_retry(setup):
    service, _ = setup
    saved = service.save("owner", "farm", plan(), request_id="plan")
    CropRecords(service.db).correct_planting("owner", "unit", "cycle", "pepper", "2026-07-02", "Synthetic", "Correction",
                                            request_id="amend", expected_revision=None)
    current = service.get("owner", "farm", saved["id"])
    assert current["review"]["needs_review"]
    assert current["review"]["cycle_changes"] == [dict(cycle_id="cycle", fields=["crop", "planted_on"])]
    assert current["result"] == saved["result"] and current["snapshot"] == saved["snapshot"]
    assert service.save("owner", "farm", plan(), request_id="plan") == saved
    revised = service.save("owner", "farm", plan(), request_id="reviewed", parent_id=saved["id"])
    assert not service.get("owner", "farm", revised["id"])["review"]["needs_review"]


def test_comparison_rejects_unknown_ids_different_periods_and_duplicate_selections(setup):
    service, _ = setup
    saved = service.save("owner", "farm", plan(), request_id="plan")
    inputs = plan()
    inputs["period"]["end"] = "2026-10-21"
    other = service.save("owner", "farm", inputs, request_id="other")
    for ids in ([saved["id"]], [saved["id"], saved["id"]], [saved["id"], "unknown"], [saved["id"], other["id"]]):
        with pytest.raises(ValueError):
            service.compare("owner", "farm", ids)
    with pytest.raises(ValueError):
        execute_farm_resources(service.db, "owner", "farm", "save", dict(data=plan(), request_id="bad", approve=True))


def test_export_and_erasure_keep_other_owners_plans(setup):
    service, farms = setup
    seed(farms, "other", "other")
    service.save("owner", "farm", plan(), request_id="own")
    other = copy.deepcopy(plan())
    other["jobs"] = [dict(other["jobs"][0], unit_id="unitother", cycle_id="cycleother")]
    theirs = service.save("other", "farmother", other, request_id="other")
    farms.purge("owner")
    assert all(not entries for entries in farms.export("owner").values())
    assert farms.export("other")["resource_plans"] == [theirs]


def test_v7_migration_failure_rolls_back_and_backup_is_restorable(setup, monkeypatch):
    import agronaut_agent.farm_resources as module

    service, farms = setup
    path = service.db.path
    original = farms.export("owner")
    service.db.execute("DROP TABLE farm_resource_plans")
    service.db.execute("PRAGMA user_version=7")
    service.db._conn.close()
    migrate = module.migrate_resource_plans

    def fail(conn):
        migrate(conn)
        raise RuntimeError("Synthetic interrupted resource migration")

    monkeypatch.setattr(module, "migrate_resource_plans", fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        _Db(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 7
    assert raw.execute("SELECT name FROM sqlite_master WHERE name='farm_resource_plans'").fetchone() is None
    assert raw.execute("SELECT count(*) FROM production_units").fetchone()[0] == 3
    raw.close()
    backups = list(path.parent.glob(f"*.pre-v{SCHEMA_VERSION}-*.bak"))
    assert len(backups) == 1 and backups[0].stat().st_mode & 0o777 == 0o600
    backup = sqlite3.connect(backups[0])
    assert backup.execute("PRAGMA user_version").fetchone()[0] == 7
    backup.close()
    monkeypatch.setattr(module, "migrate_resource_plans", migrate)
    restored = _Db(path)
    assert FarmStore(restored).export("owner") == original
    assert restored.query("PRAGMA user_version")[0][0] == SCHEMA_VERSION
    restored._conn.close()


def test_standard_library_cli_evaluate_save_get_and_compare(tmp_path):
    root = Path(__file__).resolve().parents[2]
    db, request = tmp_path/"cli.sqlite3", tmp_path/"input.json"

    def command(*args):
        result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", *args], cwd=root, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout+result.stderr
        return json.loads(result.stdout)

    unit = command("farm", "--db", str(db), "create", "--input", str(root/"examples/soil_unit.json"))["created"]
    inputs = plan()
    inputs["period"]["timezone"] = "Asia/Taipei"
    raw = _Db(db)
    inputs["period"]["timezone"] = raw.query("SELECT timezone FROM farms WHERE id=?", (unit["farm_id"],))[0][0]
    raw._conn.close()
    inputs["jobs"] = [dict(inputs["jobs"][0], unit_id=unit["id"], cycle_id=None)]

    def op(name, data):
        request.write_text(json.dumps(data))
        return command("resources", "--db", str(db), "--farm", unit["farm_id"], name, "--input", str(request))

    assert "save" in op("describe", {})["operations"]
    assert op("evaluate", dict(data=inputs))["result"]["all_requested_blocks_allocated"]
    saved = op("save", dict(data=inputs, request_id="first"))
    second = op("save", dict(data=dict(inputs, title="Second"), request_id="second", parent_id=saved["id"]))
    assert op("get", dict(plan_id=saved["id"]))["result"] == saved["result"]
    assert len(op("list", {})["plans"]) == 2
    assert len(op("compare", dict(plan_ids=[saved["id"], second["id"]]))["scenarios"]) == 2
    assert "save_new_work" in op("describe", {})["operations"]
    separate_request = dict(template_id=second["id"], data=dict(inputs, title="Separate work"),
                            reason="Another explicitly planned event", request_id="separate")
    separate = op("save_new_work", separate_request)
    assert separate["parent_id"] is None
    assert op("save_new_work", separate_request) == separate
    assert op("get", dict(plan_id=separate["id"]))["new_work"]["template"]["id"] == second["id"]
    assert len(op("list", {})["plans"]) == 3
