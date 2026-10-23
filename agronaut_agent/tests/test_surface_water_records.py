"""Actual patch supplies, immutable corrections, exact replay and checkpoint lineage."""

import json
import sqlite3
import subprocess
import sys
from copy import deepcopy

import pytest

from agronaut_agent.crop_records import CropRecords
from agronaut_agent.farm_store import FarmStore
from agronaut_agent.field_workflow import execute_field_workflow
from agronaut_agent.store import SCHEMA_VERSION, _Db
from agronaut_agent.surface_water_records import SurfaceWaterRecords
from agronaut_agent.tests.test_coupled_water_workflow import ROOT, example, next_day
from agronaut_agent.tests.test_field_workflow import setup as setup


def inputs(kind="observed"):
    data = example()
    data["weather"][0]["kind"] = kind
    if kind == "forecast":
        data["weather"][0]["issued_at"] = "2026-07-05T12:00:00Z"
    return data


def save(workflow, data=None, request_id="base"):
    return workflow.save("owner", "field", "coupled_soil_water", data or inputs(), request_id=request_id, cycle_id="cycle")


def water(**changes):
    data = dict(day="2026-07-06", patches=[dict(id="plot", rainfall_mm="5", rainfall_runoff_mm="1", irrigation_at_surface_mm="3",
        source="Synthetic surface-depth measurement")], source="Synthetic daily log", representation_note="Synthetic complete plot and day coverage",
        provenance="measured", quality="unverified")
    data.update(changes)
    return data


def record(workflow, base, data=None, request_id="water", **changes):
    return execute_field_workflow(workflow.db, "owner", "field", "record_surface_day", dict(calculation_id=base["id"], data=data or water(),
        request_id=request_id, **changes))


def replay(workflow, base, records, request_id="replay", **changes):
    return execute_field_workflow(workflow.db, "owner", "field", "replay_surface_water", dict(calculation_id=base["id"],
        record_ids=[r["id"] for r in records], request_id=request_id, **changes))


def test_saved_surface_record_and_replay_preserve_originals_and_replace_not_add(setup):
    workflow, store = setup
    base = save(workflow)
    before = store.export("owner")
    row = record(workflow, base)
    assert record(workflow, base) == row
    assert workflow.get("owner", "field", base["id"]) == base
    season = CropRecords(workflow.db).season_review("owner", "field", "cycle")
    assert season["surface_water"]["current_records"][0]["id"] == row["id"]
    assert season["water"]["totals"] == []
    assert store.export("owner")["water_days"] == []
    assert {k:v for k,v in store.export("owner").items() if k != "surface_water_days"} == {k:v for k,v in before.items() if k != "surface_water_days"}
    child = replay(workflow, base, [row])
    assert replay(workflow, base, [row]) == child
    again = replay(workflow, child, [row], request_id="again")
    assert again["result"] == child["result"]
    assert child["parent_id"] == base["id"] and child["surface_water_evidence"] == [row]
    assert child["applied_water_record_ids"] == []
    assert child["inputs"]["weather"] == base["inputs"]["weather"]
    old_patch, new_patch = base["inputs"]["days"][0]["patches"][0], child["inputs"]["days"][0]["patches"][0]
    assert {k:v for k,v in old_patch.items() if k != "water"} == {k:v for k,v in new_patch.items() if k != "water"}
    totals = child["result"]["result"]["totals"]
    assert totals["rainfall_l"]["exact"] == "500/1"
    assert totals["irrigation_at_surface_l"]["exact"] == "300/1"
    assert totals["combined_balance_residual_l"]["exact"] == "0/1"
    assert workflow.get("owner", "field", base["id"]) == base


def test_corrections_require_latest_revision_and_refresh_child_without_changing_history(setup):
    workflow, _ = setup
    base = save(workflow)
    row = record(workflow, base)
    child = replay(workflow, base, [row])
    data = water()
    data["patches"][0]["irrigation_at_surface_mm"] = "8"
    corrected = record(workflow, base, data, request_id="correction", supersedes_id=row["id"], reason="Corrected meter conversion")
    assert record(workflow, base) == row
    review = SurfaceWaterRecords(workflow.db).review("owner", "field", child["id"])
    assert review["applied"][0]["needs_review"]
    assert review["records"][0]["superseded"]
    with pytest.raises(ValueError, match="superseded"):
        replay(workflow, base, [row], request_id="stale")
    with pytest.raises(ValueError, match="latest"):
        record(workflow, base, data, request_id="stale-correction", supersedes_id=row["id"], reason="Stale correction")
    fixed = replay(workflow, child, [corrected], request_id="fixed")
    assert fixed["surface_water_evidence"] == [corrected]
    assert fixed["result"]["result"]["totals"]["irrigation_at_surface_l"]["exact"] == "800/1"
    assert workflow.get("owner", "field", child["id"]) == child


def test_continuation_keeps_prior_evidence_and_requires_replaying_corrected_ancestor(setup):
    workflow, _ = setup
    base = save(workflow)
    row = record(workflow, base)
    child = replay(workflow, base, [row])
    weather, days = next_day(inputs())
    continued = workflow.continue_coupled_water("owner", "field", child["id"], weather, days, "Next observed day", request_id="next")
    assert continued["surface_water_evidence"] == [row]
    next_record = record(workflow, continued, water(day="2026-07-07"), request_id="second-day")
    next_replay = replay(workflow, continued, [next_record], request_id="next-replay")
    assert next_replay["surface_water_evidence"] == [row, next_record]
    corrected = record(workflow, base, water(quality="checked"), request_id="correction", supersedes_id=row["id"], reason="Review")
    with pytest.raises(ValueError, match="superseded"):
        workflow.continue_coupled_water("owner", "field", child["id"], weather, days, "Retry with new request", request_id="invalid-next")
    with pytest.raises(ValueError, match="superseded"):
        replay(workflow, continued, [next_record], request_id="invalid-replay")
    with pytest.raises(ValueError, match="ancestor"):
        replay(workflow, continued, [corrected], request_id="invalid-ancestor")
    revised = replay(workflow, child, [corrected], request_id="revised")
    resumed = workflow.continue_coupled_water("owner", "field", revised["id"], weather, days, "Revised branch", request_id="revised-next")
    assert resumed["surface_water_evidence"] == [corrected]
    assert workflow.continue_coupled_water("owner", "field", child["id"], weather, days, "Next observed day", request_id="next") == continued


@pytest.mark.parametrize("kind", ["scenario", "forecast", "historical"])
def test_actual_supply_does_not_relabel_weather_evidence(setup, kind):
    workflow, _ = setup
    base = save(workflow, inputs(kind))
    row = record(workflow, base)
    with pytest.raises(ValueError, match="weather"):
        replay(workflow, base, [row])
    if kind == "historical":
        child = replay(workflow, base, [row], weather_policy="allow_historical")
        assert child["inputs"]["weather"] == base["inputs"]["weather"]
    else:
        with pytest.raises(ValueError, match="weather"):
            replay(workflow, base, [row], weather_policy="allow_historical")


@pytest.mark.parametrize("case", ["empty", "missing_patch", "extra_patch", "duplicate", "missing_zero", "negative", "too_large", "runoff", "numeric", "source", "coverage", "provenance", "quality", "future", "preplant"])
def test_invalid_actual_water_never_mutates_records(setup, case):
    workflow, store = setup
    base, data = save(workflow), water()
    if case == "empty":
        data["patches"] = []
    elif case == "missing_patch":
        data["patches"][0]["id"] = "other"
    elif case == "extra_patch":
        data["patches"].append(dict(data["patches"][0], id="other"))
    elif case == "duplicate":
        data["patches"] *= 2
    elif case in ("missing_zero", "negative", "too_large", "numeric"):
        data["patches"][0]["irrigation_at_surface_mm"] = {"missing_zero":"", "negative":"-1", "too_large":"2001", "numeric":3}[case]
    elif case == "runoff":
        data["patches"][0]["rainfall_runoff_mm"] = "6"
    elif case == "source":
        data["patches"][0]["source"] = ""
    elif case == "coverage":
        data["representation_note"] = ""
    elif case == "provenance":
        data["provenance"] = "forecast"
    elif case == "quality":
        data["quality"] = None
    elif case == "future":
        data["day"] = "2099-01-01"
    elif case == "preplant":
        data["day"] = "2026-06-30"
    before = store.export("owner")
    with pytest.raises(ValueError):
        record(workflow, base, data)
    assert store.export("owner") == before


def test_wrong_scope_footprint_duplicates_and_rejected_records_are_not_replayed(setup):
    workflow, store = setup
    base = save(workflow)
    row = record(workflow, base)
    records = SurfaceWaterRecords(workflow.db)
    for owner, unit in (("other", "field"), ("owner", "field2")):
        with pytest.raises(ValueError):
            records.review(owner, unit, base["id"])
        with pytest.raises(ValueError):
            records.replay(owner, unit, base["id"], [row["id"]], request_id="wrong")
    with pytest.raises(ValueError, match="duplicate"):
        replay(workflow, base, [row, row])
    with pytest.raises(ValueError, match="already exist"):
        record(workflow, base, request_id="duplicate-day")
    different = inputs()
    different["area_m2"] = "200"
    other = save(workflow, different, request_id="other-area")
    with pytest.raises(ValueError, match="footprint"):
        replay(workflow, other, [row])
    rejected = record(workflow, base, water(quality="rejected"), request_id="reject", supersedes_id=row["id"], reason="Bad measurement")
    with pytest.raises(ValueError, match="rejected"):
        replay(workflow, base, [rejected])
    assert len(store.export("owner")["surface_water_days"]) == 2


def test_legacy_water_ids_and_conflicting_request_retries_are_not_accepted(setup):
    from agronaut_agent.tests.test_field_workflow import water as root_water
    workflow, store = setup
    base = save(workflow)
    legacy = workflow.record_day("owner", "field", root_water(), request_id="root", cycle_id="cycle")
    with pytest.raises(ValueError, match="unknown record"):
        replay(workflow, base, [legacy])
    row = record(workflow, base)
    with pytest.raises(ValueError, match="different data"):
        record(workflow, base, water(quality="checked"))
    child = replay(workflow, base, [row])
    with pytest.raises(ValueError, match="different data"):
        replay(workflow, base, [row], weather_policy="allow_historical")
    assert workflow.get("owner", "field", child["id"]) == child
    assert store.export("owner")["water_days"] == [legacy]


def test_record_and_replay_reject_timezone_changes_and_other_crop_cycle(setup):
    from agro_model.farm import ProductionCycle
    workflow, store = setup
    base = save(workflow)
    row = record(workflow, base)
    store.create_cycle("owner", ProductionCycle("other-cycle", "field", "tomato", "2026-07-01"))
    other = workflow.save("owner", "field", "coupled_soil_water", inputs(), request_id="other-cycle", cycle_id="other-cycle")
    with pytest.raises(ValueError, match="cycle"):
        replay(workflow, other, [row])
    workflow.db.execute("UPDATE farms SET timezone='Asia/Taipei' WHERE id='farm'")
    with pytest.raises(ValueError, match="timezone"):
        record(workflow, base, water(day="2026-07-07"), request_id="wrong-time")
    with pytest.raises(ValueError, match="timezone"):
        replay(workflow, base, [row])


def test_surface_actuals_constrain_planting_and_closure_and_export_erasure(setup):
    workflow, store = setup
    base = save(workflow)
    row = record(workflow, base, water(day="2026-07-08"))
    crops = CropRecords(workflow.db)
    with pytest.raises(ValueError):
        crops.close_cycle("owner", "field", "cycle", "2026-07-07", "Close", "Synthetic", request_id="close", expected_revision=None)
    with pytest.raises(ValueError, match="water"):
        crops.correct_planting("owner", "field", "cycle", "tomato", "2026-07-09", "Synthetic", "Correction", request_id="planting", expected_revision=None)
    record(workflow, base, water(day="2026-07-08", quality="rejected"), request_id="reject", supersedes_id=row["id"], reason="Bad date")
    crops.close_cycle("owner", "field", "cycle", "2026-07-07", "Close", "Synthetic", request_id="close", expected_revision=None)
    with pytest.raises(ValueError, match="cycle end"):
        record(workflow, base, water(day="2026-07-09"), request_id="closed")
    others = store.export("other")
    store.purge("owner")
    assert all(not rows for rows in store.export("owner").values())
    assert store.export("other") == others


def test_multiple_patches_keep_local_depths_and_exact_area_weighted_volumes(setup):
    workflow, _ = setup
    data, actual = inputs(), water()
    extra = deepcopy(data["initial_state"]["patches"][0])
    extra.update(id="other", area_fraction="0.75")
    data["initial_state"]["patches"][0]["area_fraction"] = "0.25"
    data["initial_state"]["patches"].append(extra)
    day = deepcopy(data["days"][0]["patches"][0])
    day["id"] = "other"
    data["days"][0]["patches"].append(day)
    actual["patches"].append(dict(actual["patches"][0], id="other", irrigation_at_surface_mm="7"))
    base = save(workflow, data)
    child = replay(workflow, base, [record(workflow, base, actual)])
    assert child["result"]["result"]["totals"]["irrigation_at_surface_l"]["exact"] == "600/1"


def test_v15_migration_has_recoverable_backup_and_atomic_failure(setup, monkeypatch):
    import agronaut_agent.surface_water_records as module
    workflow, store = setup
    save(workflow)
    before, path = store.export("owner"), workflow.db.path
    workflow.db.execute("DROP TABLE field_surface_water_days")
    workflow.db.execute("PRAGMA user_version=15")
    workflow.db._conn.close()
    migrate = module.migrate_surface_water
    def fail(conn):
        migrate(conn)
        raise RuntimeError("Synthetic migration failure")
    monkeypatch.setattr(module, "migrate_surface_water", fail)
    with pytest.raises(RuntimeError):
        _Db(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 15
    assert raw.execute("SELECT name FROM sqlite_master WHERE name='field_surface_water_days'").fetchone() is None
    raw.close()
    backup = next(path.parent.glob(f"*.pre-v{SCHEMA_VERSION}-*.bak"))
    assert backup.stat().st_mode & 0o777 == 0o600
    old = sqlite3.connect(backup)
    assert old.execute("PRAGMA user_version").fetchone()[0] == 15
    old.close()
    monkeypatch.setattr(module, "migrate_surface_water", migrate)
    reopened = _Db(path)
    assert FarmStore(reopened).export("owner") == before
    reopened._conn.close()


def test_stdlib_cli_records_and_replays_surface_water(tmp_path):
    db, path = tmp_path/"surface.sqlite3", tmp_path/"request.json"
    def command(group, operation, data):
        path.write_text(json.dumps(data))
        result = subprocess.run([sys.executable, "-S", "-m", "agronaut_agent.cli", group, "--db", str(db), operation, "--input", str(path)],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout+result.stderr
        return json.loads(result.stdout)
    command("farm", "create", json.loads((ROOT/"examples/soil_unit.json").read_text()))
    data = inputs()
    data["site"]["timezone"] = data["weather"][0]["timezone"] = "Africa/Accra"
    base = command("field", "save", dict(task="coupled_soil_water", inputs=data, request_id="base"))
    row = command("field", "record_surface_day", dict(calculation_id=base["id"], data=water(), request_id="water"))
    child = command("field", "replay_surface_water", dict(calculation_id=base["id"], record_ids=[row["id"]], request_id="replay"))
    assert child["surface_water_evidence"] == [row]
    assert len(command("field", "review_surface_water", dict(calculation_id=child["id"]))["records"]) == 1
