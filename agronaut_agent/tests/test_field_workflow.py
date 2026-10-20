"""Saved plan -> actual daily water -> new plan, including ownership and erasure."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agro_model.farm import Farm, ProductionCycle, ProductionUnit
from agronaut_agent.farm_store import FarmStore
from agronaut_agent.field_workflow import FieldWorkflow
from agronaut_agent.store import SCHEMA_VERSION, _Db


def inputs():
    data = json.loads((Path(__file__).resolve().parents[2]/"examples/field_water.json").read_text())
    data["site"]["timezone"] = "UTC"
    data["weather"][0]["kind"] = "observed"
    data["weather"][0]["source"] = "Synthetic observation fixture, not field evidence"
    return data


def water(**changes):
    data = dict(day="2026-07-06", rainfall_mm=5, net_irrigation_mm=3, runoff_mm=1,
                source="Synthetic daily totals", provenance="reported", quality="unverified")
    data.update(changes)
    return data


@pytest.fixture
def setup(tmp_path):
    db = _Db(tmp_path/"field.sqlite3")
    store = FarmStore(db)
    for suffix, owner in (("", "owner"), ("2", "owner"), ("3", "other")):
        store.create_farm(Farm("farm"+suffix, owner, "Home", "UTC"))
        store.create_unit(owner, ProductionUnit("field"+suffix, "farm"+suffix, "Field", "soil",
                                                "soil", "open_field", "supplemental", "surface"))
        store.create_cycle(owner, ProductionCycle("cycle"+suffix, "field"+suffix, "tomato", "2026-07-01"))
    yield FieldWorkflow(db), store
    db._conn.close()


def test_plan_action_replay_preserves_original_and_does_not_double_count(setup):
    workflow, _ = setup
    base = workflow.save("owner", "field", "root_zone_budget", inputs(), request_id="base", cycle_id="cycle")
    logged = workflow.record_day("owner", "field", water(), request_id="rain1", cycle_id="cycle")
    replay = workflow.replay("owner", "field", base["id"], [logged["id"]], request_id="replay1")
    again = workflow.replay("owner", "field", replay["id"], [logged["id"]], request_id="replay2")
    assert workflow.get("owner", "field", base["id"]) == base
    assert base["inputs"]["events"][0]["net_irrigation_mm"] == 0
    assert replay["inputs"]["events"][0]["net_irrigation_mm"] == 3
    assert replay["inputs"]["rainfall_overrides"][0]["rainfall_mm"] == 5
    assert replay["inputs"]["weather"] == base["inputs"]["weather"]
    assert replay["parent_id"] == base["id"]
    assert again["applied_water_record_ids"] == [logged["id"]]
    before = base["result"]["result"]["days"][0]["balance"]["values"]
    updated = replay["result"]["result"]["days"][0]["balance"]["values"]
    repeated = again["result"]["result"]["days"][0]["balance"]["values"]
    assert updated == repeated
    assert updated["depletion_mm"] < before["depletion_mm"]
    assert logged["id"] in replay["inputs"]["events"][0]["source"]
    assert replay["result"]["evidence_status"] == "experimental"


def test_corrections_append_history_and_require_latest_revision(setup):
    workflow, _ = setup
    first = workflow.record_day("owner", "field", water(), request_id="day1")
    corrected = workflow.record_day("owner", "field", water(rainfall_mm=12),
                                    request_id="day2", supersedes_id=first["id"])
    listing = workflow.list("owner", "field")["water_days"]
    assert listing[0]["data"]["rainfall_mm"] == 5
    assert listing[0]["superseded"] and not listing[1]["superseded"]
    assert corrected["supersedes_id"] == first["id"]
    with pytest.raises(ValueError, match="already superseded"):
        workflow.record_day("owner", "field", water(rainfall_mm=15), request_id="bad", supersedes_id=first["id"])
    with pytest.raises(ValueError, match="already exist"):
        workflow.record_day("owner", "field", water(), request_id="duplicate")


def test_retries_are_idempotent_and_conflicting_or_boolean_alias_inputs_fail(setup):
    workflow, _ = setup
    data = inputs()
    first = workflow.save("owner", "field", "root_zone_budget", data, request_id="same")
    assert workflow.save("owner", "field", "root_zone_budget", data, request_id="same") == first
    data["schema_version"] = True
    with pytest.raises(ValueError, match="different data"):
        workflow.save("owner", "field", "root_zone_budget", data, request_id="same")
    record = workflow.record_day("owner", "field", water(), request_id="same")
    assert workflow.record_day("owner", "field", water(), request_id="same") == record
    replay = workflow.replay("owner", "field", first["id"], [record["id"]], request_id="r")
    assert workflow.replay("owner", "field", first["id"], [record["id"]], request_id="r") == replay
    assert len(workflow.list("owner", "field")["calculations"]) == 2


def test_ownership_cycle_and_unit_boundaries(setup):
    workflow, _ = setup
    plan = workflow.save("owner", "field", "root_zone_budget", inputs(), request_id="base", cycle_id="cycle")
    wrong_cycle = workflow.record_day("owner", "field", water(), request_id="no-cycle")
    other_unit = workflow.record_day("owner", "field2", water(), request_id="other-unit")
    for owner, unit in (("other", "field"), ("owner", "field2")):
        with pytest.raises(ValueError):
            workflow.get(owner, unit, plan["id"])
    for record in (wrong_cycle, other_unit):
        with pytest.raises(ValueError):
            workflow.replay("owner", "field", plan["id"], [record["id"]], request_id="bad")
    with pytest.raises(ValueError, match="cycle"):
        workflow.save("owner", "field", "root_zone_budget", inputs(), request_id="bad", cycle_id="cycle2")
    assert len(workflow.list("owner", "field")["calculations"]) == 1


@pytest.mark.parametrize("change", [
    {"rainfall_mm": None}, {"net_irrigation_mm": True}, {"runoff_mm": 6},
    {"provenance": "forecast"}, {"quality": "trusted"}, {"source": ""},
    {"day": datetime.now(timezone.utc).date().isoformat()},
    {"day": "2099-01-01"}, {"gross_irrigation_l": 20},
])
def test_bad_or_unfinished_daily_totals_fail_without_a_write(setup, change):
    workflow, _ = setup
    with pytest.raises(ValueError):
        workflow.record_day("owner", "field", water(**change), request_id="bad")
    assert workflow.list("owner", "field")["water_days"] == []


def test_replay_rejects_forecast_meteorology_rejected_records_and_ambiguous_days(setup):
    workflow, _ = setup
    data = inputs()
    data["weather"][0].update(kind="forecast", issued_at="2026-07-05T12:00:00+00:00")
    plan = workflow.save("owner", "field", "root_zone_budget", data, request_id="forecast")
    first = workflow.record_day("owner", "field", water(), request_id="day1")
    with pytest.raises(ValueError, match="observed meteorology"):
        workflow.replay("owner", "field", plan["id"], [first["id"]], request_id="bad")
    plan = workflow.save("owner", "field", "root_zone_budget", inputs(), request_id="observed")
    second = workflow.record_day("owner", "field", water(quality="rejected"), request_id="day2",
                                 supersedes_id=first["id"])
    for records, message in (([second["id"]], "rejected"), ([first["id"], first["id"]], "duplicate")):
        with pytest.raises(ValueError, match=message):
            workflow.replay("owner", "field", plan["id"], records, request_id="bad")
    third = workflow.record_day("owner", "field", water(), request_id="day3", supersedes_id=second["id"])
    with pytest.raises(ValueError, match="one revision"):
        workflow.replay("owner", "field", plan["id"], [first["id"], third["id"]], request_id="bad")


def test_saved_records_export_and_erase_with_their_owner(setup):
    workflow, farms = setup
    for owner, unit in (("owner", "field"), ("other", "field3")):
        plan = workflow.save(owner, unit, "root_zone_budget", inputs(), request_id="plan")
        record = workflow.record_day(owner, unit, water(), request_id="day")
        workflow.replay(owner, unit, plan["id"], [record["id"]], request_id="replay")
    export = farms.export("owner")
    assert len(export["calculations"]) == 2 and len(export["water_days"]) == 1
    json.dumps(export, allow_nan=False)
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())
    assert len(farms.export("other")["calculations"]) == 2
    with pytest.raises(ValueError):
        workflow.save("owner", "field", "root_zone_budget", inputs(), request_id="late")


def test_calculation_timezone_crop_and_dates_must_match_the_saved_farm_cycle(setup):
    workflow, _ = setup
    for changed in ("timezone", "crop", "date"):
        data = inputs()
        if changed == "timezone":
            data["site"]["timezone"] = "Asia/Taipei"
        elif changed == "crop":
            data["crop"]["name"] = "onion"
        else:
            data["weather"][0]["day"] = "2026-06-30"
            data["events"][0]["day"] = "2026-06-30"
            data["initial_state"]["as_of"] = "2026-06-29"
        with pytest.raises(ValueError):
            workflow.save("owner", "field", "root_zone_budget", data, request_id=changed, cycle_id="cycle")
    assert workflow.list("owner", "field")["calculations"] == []


def test_erasure_handles_a_history_longer_than_sqlite_cascade_recursion_limit(setup):
    workflow, farms = setup
    plan = workflow.save("owner", "field", "root_zone_budget", inputs(), request_id="base")
    record = workflow.record_day("owner", "field", water(), request_id="day")
    # Build a long valid FK chain without repeating the already-tested calculations.
    # Scope cascades must erase all rows without recursively deleting through lineage.
    with workflow.db.transaction() as conn:
        parent, prior = plan["id"], record["id"]
        for index in range(1100):
            pid, rid = f"plan-{index}", f"water-{index}"
            conn.execute("INSERT INTO field_calculations VALUES(?,?,?,?,?,?,?)",
                         (pid, "field", None, pid, parent, plan["created_at"], json.dumps(plan)))
            conn.execute("INSERT INTO field_water_days VALUES(?,?,?,?,?,?,?,?)",
                         (rid, "field", None, rid, record["day"], prior, record["created_at"], json.dumps(record)))
            parent, prior = pid, rid
    farms.purge("owner")
    assert workflow.db.query("SELECT count(*) FROM field_calculations")[0][0] == 0
    assert workflow.db.query("SELECT count(*) FROM field_water_days")[0][0] == 0


def test_v3_upgrade_backup_and_failed_migration_rollback(tmp_path, monkeypatch):
    from agronaut_agent import field_workflow

    path = tmp_path/"v3.sqlite3"
    db = _Db(path)
    # V4 only adds these tables; remove them to recreate the previous stored layout.
    db.execute("DROP TABLE farm_resource_plans")
    db.execute("DROP TABLE field_calculations")
    db.execute("DROP TABLE field_water_days")
    db.execute("DROP TABLE unit_setup_drafts")
    db.execute("DROP TABLE observation_reviews")
    db.execute("DROP TABLE crop_records")
    db.execute("PRAGMA user_version=3")
    db._conn.close()
    original = field_workflow.migrate_field_workflow

    def fail(conn):
        original(conn)
        raise RuntimeError("interrupted v4 migration")

    monkeypatch.setattr(field_workflow, "migrate_field_workflow", fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        _Db(path)
    with sqlite3.connect(path) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 3
        assert check.execute("SELECT name FROM sqlite_master WHERE name='field_water_days'").fetchone() is None
    monkeypatch.setattr(field_workflow, "migrate_field_workflow", original)
    migrated = _Db(path)
    assert migrated.query("PRAGMA user_version")[0][0] == SCHEMA_VERSION
    for backup in tmp_path.glob(f"v3.sqlite3.pre-v{SCHEMA_VERSION}-*.bak"):
        with sqlite3.connect(backup) as check:
            assert check.execute("PRAGMA user_version").fetchone()[0] == 3
    assert list(tmp_path.glob(f"v3.sqlite3.pre-v{SCHEMA_VERSION}-*.bak"))
    migrated._conn.close()


def test_weather_revision_converts_forecast_then_replays_actual_water(setup):
    workflow, _ = setup
    data = inputs()
    data["weather"][0].update(kind="forecast", issued_at="2026-07-05T12:00:00+00:00")
    base = workflow.save("owner", "field", "root_zone_budget", data, request_id="base", cycle_id="cycle")
    weather = inputs()["weather"]
    weather[0]["net_radiation_mj_m2"] = 15
    updated = workflow.revise_weather("owner", "field", base["id"], weather, request_id="observed")
    logged = workflow.record_day("owner", "field", water(), request_id="day", cycle_id="cycle")
    replayed = workflow.replay("owner", "field", updated["id"], [logged["id"]], request_id="actual")
    assert updated["parent_id"] == base["id"]
    assert replayed["parent_id"] == updated["id"]
    assert replayed["cycle_id"] == "cycle"
    assert replayed["inputs"]["initial_state"] == base["inputs"]["initial_state"]
    assert replayed["inputs"]["weather"][0]["kind"] == "observed"
    assert replayed["inputs"]["rainfall_overrides"][0]["rainfall_mm"] == 5
    assert workflow.get("owner", "field", base["id"]) == base
    assert workflow.get("owner", "field", updated["id"]) == updated
    assert updated["result"]["result"]["days"][0]["demand"] != base["result"]["result"]["days"][0]["demand"]


def test_weather_revision_preserves_exact_applied_water_revision_and_raw_request(setup):
    workflow, _ = setup
    base = workflow.save("owner", "field", "root_zone_budget", inputs(), request_id="base")
    first = workflow.record_day("owner", "field", water(), request_id="day")
    replayed = workflow.replay("owner", "field", base["id"], [first["id"]], request_id="actual")
    workflow.record_day("owner", "field", water(rainfall_mm=20), request_id="corrected", supersedes_id=first["id"])
    patch = inputs()["weather"]
    patch[0].update(rainfall_mm=0, net_radiation_mj_m2=16, source="Corrected station meteorology")
    updated = workflow.revise_weather("owner", "field", replayed["id"], patch, request_id="revision")
    assert updated["applied_water_record_ids"] == [first["id"]]
    assert updated["inputs"]["events"] == replayed["inputs"]["events"]
    assert updated["inputs"]["rainfall_overrides"][0]["rainfall_mm"] == 5
    assert updated["inputs"]["weather"] == patch
    assert first["id"] == updated["inputs"]["rainfall_overrides"][0]["water_record_id"]
    assert updated["operation"]["weather"] == patch
    assert patch[0]["rainfall_mm"] == 0
    assert workflow.revise_weather("owner", "field", replayed["id"], patch, request_id="revision") == updated
    patch[0]["rainfall_mm"] = 2
    with pytest.raises(ValueError, match="different data"):
        workflow.revise_weather("owner", "field", replayed["id"], patch, request_id="revision")


def test_extension_recomputes_entire_budget_from_initial_state_with_explicit_events(setup):
    from agronaut_agent.domains import run_capability

    workflow, _ = setup
    base = workflow.save("owner", "field", "root_zone_budget", inputs(), request_id="base")
    weather = [dict(inputs()["weather"][0], day="2026-07-07", kind="forecast",
                    issued_at="2026-07-06T12:00:00Z")]
    events = [dict(day="2026-07-07", net_irrigation_mm=0, runoff_mm=0, source="Explicit no-irrigation scenario")]
    extended = workflow.revise_weather("owner", "field", base["id"], weather, events=events, request_id="extended")
    expected = inputs()
    expected["weather"].extend(weather)
    expected["events"].extend(events)
    assert extended["inputs"] == expected
    assert extended["result"] == run_capability("soil", "root_zone_budget", expected)
    assert extended["result"]["result"]["days"][1]["event"]["net_irrigation_mm"] == 0
    assert workflow.get("owner", "field", base["id"]) == base


@pytest.mark.parametrize("change", ["missing_events", "gap", "prepend", "existing_event", "duplicate_event",
                                         "duplicate_weather", "partial_weather", "unknown_field", "boolean",
                                         "missing_rain", "runoff_exceeds_rain", "empty", "bad_events"])
def test_invalid_weather_revision_is_atomic(setup, change):
    workflow, _ = setup
    base = workflow.save("owner", "field", "root_zone_budget", inputs(), request_id="base")
    patch = [dict(inputs()["weather"][0], day="2026-07-07")]
    events = [dict(day="2026-07-07", net_irrigation_mm=0, runoff_mm=0, source="Explicit test scenario")]
    if change == "missing_events":
        events = []
    elif change in ("gap", "prepend"):
        patch[0]["day"] = events[0]["day"] = "2026-07-08" if change == "gap" else "2026-07-05"
    elif change == "existing_event":
        events.append(inputs()["events"][0])
    elif change == "duplicate_event":
        events *= 2
    elif change == "duplicate_weather":
        patch *= 2
    elif change == "partial_weather":
        del patch[0]["wind_m_s"]
    elif change == "unknown_field":
        patch[0]["owner_id"] = "other"
    elif change == "boolean":
        patch[0]["rainfall_mm"] = True
    elif change == "missing_rain":
        patch[0]["rainfall_mm"] = None
    elif change == "runoff_exceeds_rain":
        events[0]["runoff_mm"] = 100
    elif change == "empty":
        patch = []
    elif change == "bad_events":
        events = {}
    with pytest.raises(ValueError):
        workflow.revise_weather("owner", "field", base["id"], patch, events=events, request_id="bad")
    assert len(workflow.list("owner", "field")["calculations"]) == 1
    assert workflow.get("owner", "field", base["id"]) == base


@pytest.mark.parametrize("kind", ["forecast", "historical", "scenario"])
def test_weather_revision_cannot_downgrade_observations(setup, kind):
    workflow, _ = setup
    base = workflow.save("owner", "field", "crop_water", inputs(), request_id="base")
    patch = [dict(inputs()["weather"][0], kind=kind, issued_at="2026-07-05T13:00:00Z")]
    with pytest.raises(ValueError, match="observed meteorology"):
        workflow.revise_weather("owner", "field", base["id"], patch, request_id="bad")


def test_forecast_refresh_compares_issue_instants_and_crop_demand_can_extend_without_events(setup):
    workflow, _ = setup
    data = inputs()
    data["weather"][0].update(kind="forecast", issued_at="2026-07-05T12:00:00Z")
    base = workflow.save("owner", "field", "crop_water", data, request_id="base")
    for stamp in ("2026-07-05T19:00:00+08:00", "2026-07-05T20:00:00+08:00"):
        with pytest.raises(ValueError, match="newer issued_at"):
            workflow.revise_weather("owner", "field", base["id"],
                                    [dict(data["weather"][0], issued_at=stamp)], request_id="bad")
    patch = [dict(data["weather"][0], issued_at="2026-07-05T21:00:00+08:00"),
             dict(data["weather"][0], day="2026-07-07")]
    updated = workflow.revise_weather("owner", "field", base["id"], patch, request_id="good")
    assert len(updated["result"]["result"]["days"]) == 2
    assert all(row["weather_kind"] == "forecast" for row in updated["result"]["result"]["days"])
    with pytest.raises(ValueError, match="do not accept events"):
        workflow.revise_weather("owner", "field", base["id"], patch, events=inputs()["events"], request_id="bad")


def test_weather_revision_scope_export_and_erasure(setup):
    from agronaut_agent.field_workflow import execute_field_workflow

    workflow, farms = setup
    base = workflow.save("owner", "field", "crop_water", inputs(), request_id="base", cycle_id="cycle")
    data = dict(calculation_id=base["id"], weather=inputs()["weather"], request_id="revision")
    for owner, unit in (("other", "field"), ("owner", "field2")):
        with pytest.raises(ValueError):
            execute_field_workflow(workflow.db, owner, unit, "revise_weather", data)
    for extra in (dict(owner_id="other"), dict(cycle_id="cycle2"), dict(initial_state={})):
        with pytest.raises(ValueError, match="unknown fields"):
            execute_field_workflow(workflow.db, "owner", "field", "revise_weather", dict(data, **extra))
    updated = execute_field_workflow(workflow.db, "owner", "field", "revise_weather", data)
    assert updated["cycle_id"] == "cycle"
    assert len(farms.export("owner")["calculations"]) == 2
    farms.purge("owner")
    assert farms.export("owner")["calculations"] == []


def test_surface_event_save_preserves_evidence_without_creating_daily_water(setup):
    workflow, farms = setup
    data = json.loads((Path(__file__).resolve().parents[2]/"examples/surface_event.json").read_text())
    data["event"]["kind"] = "observed"
    saved = workflow.save("owner", "field", "surface_evaluation", data, cycle_id="cycle", request_id="event")
    assert saved["result"]["result"]["event_kind"] == "observed"
    assert saved["result"]["result"]["cutoff_recommendation"] is None
    assert workflow.save("owner", "field", "surface_evaluation", data, cycle_id="cycle", request_id="event") == saved
    assert workflow.list("owner", "field")["water_days"] == []
    assert farms.export("owner")["calculations"][0]["id"] == saved["id"]
    with pytest.raises(ValueError):
        workflow.get("other", "field", saved["id"])
    farms.purge("owner")
    assert farms.export("owner")["calculations"] == []


@pytest.mark.parametrize("change", ["timezone", "before_cycle", "unfinished"])
def test_surface_event_save_checks_farm_timezone_crop_cycle_and_completion(setup, change):
    workflow, _ = setup
    data = json.loads((Path(__file__).resolve().parents[2]/"examples/surface_event.json").read_text())
    data["event"]["kind"] = "observed"
    if change == "timezone":
        data["site"]["timezone"] = "Asia/Taipei"
    elif change == "before_cycle":
        data["event"]["started_at"] = "2026-06-30T08:00:00Z"
    else:
        data["event"]["started_at"] = "2099-01-01T08:00:00Z"
    with pytest.raises(ValueError):
        workflow.save("owner", "field", "surface_evaluation", data, cycle_id="cycle", request_id="bad")
    assert workflow.list("owner", "field")["calculations"] == []
