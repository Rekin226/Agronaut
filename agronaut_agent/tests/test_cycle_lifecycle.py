"""Crop completion is explicit, revisioned, owned and consistent with actual records."""

from datetime import datetime, timezone

import pytest

from agro_model.farm import Observation
from agronaut_agent.crop_records import CropRecords, execute_crop_records
from agronaut_agent.field_workflow import FieldWorkflow
from agronaut_agent.store import _Db
from agronaut_agent.tests.test_crop_records import create, harvest, observation
from agronaut_agent.tests.test_crop_records import setup as setup
from agronaut_agent.tests.test_field_workflow import inputs, water


def close(records, cycle, day="2026-07-10", revision=None, request="close", unit="field", owner="owner"):
    return records.close_cycle(owner, unit, cycle, day, "Planting ended", "Synthetic completion notebook",
                               request_id=request, expected_revision=revision)


def reopen(records, cycle, revision, request="reopen", unit="field", owner="owner"):
    return records.reopen_cycle(owner, unit, cycle, "Planting still growing", "Synthetic correction",
                                request_id=request, expected_revision=revision)


def test_close_correct_reopen_reclose_is_immutable_and_retry_does_not_reapply_old_state(setup):
    records, farms = setup
    cycle = create(records)["cycle"]["id"]
    assert records.get("owner", "field", cycle)["lifecycle"]["status"] == "open"
    batch = records.record_harvest("owner", "field", cycle, harvest(), request_id="harvest")
    first = close(records, cycle)
    assert close(records, cycle) == first
    second = close(records, cycle, day="2026-07-08", revision=first["id"], request="correct-end")
    assert second["supersedes_id"] == first["id"]
    state = records.get("owner", "field", cycle)["lifecycle"]
    assert state["status"] == "closed" and state["ended_on"] == "2026-07-08"
    assert state["revision_id"] == second["id"]
    assert records.list("owner", "field")["cycles"][0]["lifecycle"] == state
    assert farms.list_cycles("owner", "field")[0]["lifecycle"] == state
    with pytest.raises(ValueError, match="lifecycle changed"):
        reopen(records, cycle, first["id"])
    opened = reopen(records, cycle, second["id"])
    assert close(records, cycle) == first
    assert records.get("owner", "field", cycle)["lifecycle"]["status"] == "open"
    assert reopen(records, cycle, second["id"]) == opened
    with pytest.raises(ValueError, match="already open"):
        reopen(records, cycle, opened["id"], request="reopen-again")
    closed = close(records, cycle, revision=opened["id"], request="reclose")
    journal = records.get("owner", "field", cycle)
    assert journal["lifecycle"]["revision_id"] == closed["id"]
    assert len(journal["records"]) == 6
    assert journal["harvest_totals"][0]["quantity"] == "10"
    assert not next(r for r in journal["records"] if r["id"] == batch["id"])["superseded"]
    other_db = _Db(records.db.path)
    assert CropRecords(other_db).get("owner", "field", cycle)["lifecycle"] == journal["lifecycle"]
    other_db._conn.close()


@pytest.mark.parametrize("kind", ["harvest", "stage", "scouting"])
def test_closure_respects_latest_actuals_and_late_entry_boundary(setup, kind):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    write = getattr(records, f"record_{kind}")
    void = records.void_harvest if kind == "harvest" else records.void_observation
    data = harvest(day="2026-07-11") if kind == "harvest" else observation(kind, day="2026-07-11")
    original = write("owner", "field", cycle, data, request_id="original")
    with pytest.raises(ValueError, match="precedes a current actual record on 2026-07-11"):
        close(records, cycle)
    cancelled = void("owner", "field", original["id"], "Wrong date", request_id="void")
    closed = close(records, cycle)
    with pytest.raises(ValueError, match="after the crop cycle end"):
        write("owner", "field", cycle, data, request_id="restore", supersedes_id=cancelled["id"])
    restored = write("owner", "field", cycle, dict(data, day="2026-07-10"), request_id="restore-correct",
                     supersedes_id=cancelled["id"])
    assert restored["data"]["day"] == "2026-07-10"
    with pytest.raises(ValueError, match="precedes a current actual"):
        close(records, cycle, day="2026-07-09", revision=closed["id"], request="shorten")
    with pytest.raises(ValueError, match="after the crop cycle end"):
        write("owner", "field", cycle, dict(data, **({"batch": "new"} if kind == "harvest" else {"entry": "new"})), request_id="late")
    reopen(records, cycle, closed["id"])
    write("owner", "field", cycle, data, request_id="restore-later", supersedes_id=restored["id"])


def test_water_logs_constrain_closure_and_rejected_revisions_stay_explicit(setup):
    records, _ = setup
    workflow = FieldWorkflow(records.db)
    cycle = create(records)["cycle"]["id"]
    row = workflow.record_day("owner", "field", water(day="2026-07-11"), cycle_id=cycle, request_id="water")
    with pytest.raises(ValueError, match="precedes a current actual"):
        close(records, cycle)
    rejected = workflow.record_day("owner", "field", water(day="2026-07-11", quality="rejected"),
                                   cycle_id=cycle, request_id="reject", supersedes_id=row["id"])
    close(records, cycle)
    assert workflow.record_day("owner", "field", water(day="2026-07-11"), cycle_id=cycle, request_id="water") == row
    with pytest.raises(ValueError, match="after the crop cycle end"):
        workflow.record_day("owner", "field", water(day="2026-07-11", quality="checked"), cycle_id=cycle,
                            request_id="restore-water", supersedes_id=rejected["id"])
    workflow.record_day("owner", "field", water(day="2026-07-10"), cycle_id=cycle, request_id="last-water")
    # Unassigned field activity is not attributed to the ended planting.
    workflow.record_day("owner", "field", water(day="2026-07-11"), request_id="field-water")
    plan = workflow.save("owner", "field", "crop_water", inputs(), cycle_id=cycle, request_id="retrospective")
    assert workflow.get("owner", "field", plan["id"]) == plan


def test_typed_observations_use_local_dates_and_ignore_forecasts_and_rejected_evidence(setup):
    records, farms = setup
    records.db.execute("UPDATE farms SET timezone='Pacific/Kiritimati' WHERE id='farm'")
    cycle = create(records)["cycle"]["id"]
    at = datetime(2026, 7, 10, 12, 30, tzinfo=timezone.utc)  # Local day is July 11.

    def sample(key, provenance="measured", quality="unverified"):
        return Observation(key, "owner", "field", cycle, "rainfall", 5, "mm", at, at,
                           provenance, "Synthetic gauge", quality)

    farms.record_observation("owner", sample("forecast", "forecast"))
    farms.record_observation("owner", sample("rejected", quality="rejected"))
    closed = close(records, cycle)
    with pytest.raises(ValueError, match="after the crop cycle end"):
        farms.record_observation("owner", sample("actual"))
    reopened = reopen(records, cycle, closed["id"])
    farms.record_observation("owner", sample("actual"))
    with pytest.raises(ValueError, match="actual record on 2026-07-11"):
        close(records, cycle, revision=reopened["id"], request="end-again")
    close(records, cycle, day="2026-07-11", revision=reopened["id"], request="end-correct")


@pytest.mark.parametrize("unit", ["field", "hydro", "aqua"])
def test_owned_lifecycle_dispatch_export_and_erasure_across_domains(setup, unit):
    records, farms = setup
    cycle = create(records, unit)["cycle"]["id"]
    args = dict(cycle_id=cycle, ended_on="2026-07-10", source="Synthetic fixture", reason="Failed crop",
                request_id="close", expected_revision=None)
    for owner, target in (("other", unit), ("owner", "other"), ("owner", "hydro" if unit != "hydro" else "field")):
        with pytest.raises(ValueError):
            execute_crop_records(records.db, owner, target, "close_cycle", args)
    saved = execute_crop_records(records.db, "owner", unit, "close_cycle", args)
    for void in (records.void_harvest, records.void_observation):
        with pytest.raises(ValueError, match="record kind"):
            void("owner", unit, saved["id"], "Wrong interface", request_id="void")
    missing = dict(args)
    del missing["expected_revision"]
    with pytest.raises(ValueError, match="expected_revision"):
        execute_crop_records(records.db, "owner", unit, "close_cycle", missing)
    assert len(farms.export("owner")["crop_records"]) == 2
    farms.purge("owner")
    assert records.db.query("SELECT count(*) FROM crop_records")[0][0] == 0
    with pytest.raises(ValueError):
        reopen(records, cycle, saved["id"], unit=unit)


@pytest.mark.parametrize("changes", [dict(ended_on="2099-01-01"), dict(ended_on="2026-06-30"), dict(ended_on=None),
    dict(reason=" "), dict(source=""), dict(expected_revision="unknown"), dict(expected_revision=123)])
def test_invalid_closure_never_changes_cycle_state(setup, changes):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    args = dict(ended_on="2026-07-10", reason="Planting ended", source="Synthetic fixture", request_id="bad", expected_revision=None)
    with pytest.raises(ValueError):
        records.close_cycle("owner", "field", cycle, **dict(args, **changes))
    assert len(records.get("owner", "field", cycle)["records"]) == 1
    assert records.get("owner", "field", cycle)["lifecycle"]["status"] == "open"


def test_failed_append_and_conflicting_retry_preserve_closure_state(setup, monkeypatch):
    import agronaut_agent.crop_records as module

    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    closed = close(records, cycle)
    with pytest.raises(ValueError, match="different data"):
        close(records, cycle, day="2026-07-09")

    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic write failure")

    monkeypatch.setattr(module, "_save", fail)
    with pytest.raises(RuntimeError):
        reopen(records, cycle, closed["id"])
    assert records.get("owner", "field", cycle)["lifecycle"]["revision_id"] == closed["id"]
