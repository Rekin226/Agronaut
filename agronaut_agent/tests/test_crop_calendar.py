"""Crop work plans never manufacture performed actions and retain their revision evidence."""

from datetime import datetime, timezone

import pytest

from agro_model.farm import ProductionCycle
from agronaut_agent.crop_records import execute_crop_records
from agronaut_agent.field_workflow import FieldWorkflow
from agronaut_agent.tests.test_crop_establishment import correction
from agronaut_agent.tests.test_crop_records import create, harvest
from agronaut_agent.tests.test_crop_records import setup as setup
from agronaut_agent.tests.test_field_workflow import water


def task(**changes):
    return dict(dict(entry="work-1", title="Synthetic harvest inspection", activity="harvest",
        due=dict(basis="planting", start_offset_days=5, end_offset_days=7), location="Synthetic row 1",
        notes="Operator-supplied work window; no crop schedule recommendation", source="Synthetic work notebook", priority="normal"), **changes)


def resolve(records, row, status="completed", *, request="resolve", day="2026-07-06", evidence=None):
    return records.resolve_task("owner", row["unit_id"], row["id"], status, "Synthetic operator update", "Synthetic work log",
        request_id=request, completed_on=day if status == "completed" else None, completion_record=evidence)


def test_work_plan_is_not_an_actual_record_and_relative_changes_require_explicit_review(setup):
    records, farms = setup
    cycle = create(records)["cycle"]["id"]
    planned = records.plan_task("owner", "field", cycle, task(), request_id="plan")
    assert planned["data"]["planned_window"] == dict(start="2026-07-06", end="2026-07-08")
    assert records.plan_task("owner", "field", cycle, task(), request_id="plan") == planned
    assert FieldWorkflow(records.db).list("owner", "field")["water_days"] == []
    assert farms.observations("owner", "field") == []
    journal = records.get("owner", "field", cycle)
    assert journal["harvest_totals"] == [] and journal["fertilizer"]["groups"] == []
    assert journal["calendar"]["tasks"][0]["timing"] == "overdue"
    correction(records, cycle, planted_on="2026-07-02", crop="pepper")
    view = records.get("owner", "field", cycle)["calendar"]["tasks"][0]
    assert view["original_window"]["start"] == "2026-07-06"
    assert view["effective_window"]["start"] == "2026-07-07" and view["needs_review"]
    confirmed = records.plan_task("owner", "field", cycle, task(source="Synthetic reviewed plan"),
                                  request_id="confirm", supersedes_id=planned["id"])
    view = records.get("owner", "field", cycle)["calendar"]["tasks"][0]
    assert not view["needs_review"] and view["original_window"]["start"] == "2026-07-07"
    assert confirmed["data"]["cycle_snapshot"]["crop"] == "pepper"
    assert records.get("owner", "field", cycle)["records"][1]["data"] == planned["data"]


def test_completion_links_exact_actual_revision_and_later_correction_is_flagged(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    planned = records.plan_task("owner", "field", cycle, task(), request_id="plan")
    actual = records.record_harvest("owner", "field", cycle, harvest(), request_id="harvest")
    reference = dict(kind="crop_record", id=actual["id"])
    complete = resolve(records, planned, evidence=reference)
    assert complete["data"]["resolution"]["completion_record"]["snapshot"] == actual
    view = records.get("owner", "field", cycle)["calendar"]["tasks"][0]
    assert view["status"] == "completed" and view["completion_timing"] == "within_window"
    assert view["completion_evidence_state"] == "current" and not view["needs_review"]
    updated = records.record_harvest("owner", "field", cycle, harvest(quantity=12), request_id="correct", supersedes_id=actual["id"])
    assert resolve(records, planned, evidence=reference) == complete  # Retry never silently relinks to a newer actual.
    view = records.get("owner", "field", cycle)["calendar"]["tasks"][0]
    assert view["status"] == "completed" and view["completion_evidence_state"] == "superseded" and view["needs_review"]
    with pytest.raises(ValueError, match="current, accepted"):
        resolve(records, complete, request="stale-evidence", evidence=reference)
    corrected = resolve(records, complete, request="new-evidence", evidence=dict(kind="crop_record", id=updated["id"]))
    assert corrected["data"]["resolution"]["completion_record"]["snapshot"] == updated
    assert records.get("owner", "field", cycle)["harvest_totals"][0]["quantity"] == "12"


def test_completion_without_evidence_cancel_reopen_and_plan_revision_preserve_history(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    planned = records.plan_task("owner", "field", cycle, task(activity="maintenance"), request_id="plan")
    complete = resolve(records, planned, day="2026-07-10")
    assert records.get("owner", "field", cycle)["calendar"]["tasks"][0]["completion_timing"] == "late"
    assert records.get("owner", "field", cycle)["calendar"]["tasks"][0]["completion_evidence_state"] == "not_linked"
    with pytest.raises(ValueError, match="reopen"):
        records.plan_task("owner", "field", cycle, task(), request_id="bad", supersedes_id=complete["id"])
    reopened = resolve(records, complete, "planned", request="reopen")
    assert reopened["data"]["resolution"]["completed_on"] is None
    cancelled = resolve(records, reopened, "cancelled", request="cancel")
    reopened2 = resolve(records, cancelled, "planned", request="reopen2")
    revised = records.plan_task("owner", "field", cycle, task(due=dict(basis="date", start="2026-08-01", end="2026-08-02")),
                               request_id="revise", supersedes_id=reopened2["id"])
    assert revised["data"]["status"] == "planned"
    journal = records.get("owner", "field", cycle)
    assert len(journal["records"]) == 7 and journal["harvest_totals"] == []
    assert all(r["superseded"] for r in journal["records"][1:-1])
    with pytest.raises(ValueError, match="already planned"):
        resolve(records, revised, "planned", request="no-op")
    with pytest.raises(ValueError, match="latest revision"):
        resolve(records, planned, request="stale")


def test_dated_windows_do_not_shift_and_resolved_relative_windows_keep_original_dates(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    dated = records.plan_task("owner", "field", cycle, task(due=dict(basis="date", start="2026-07-06", end="2026-07-07")), request_id="date")
    relative = records.plan_task("owner", "field", cycle, task(entry="relative"), request_id="relative")
    resolve(records, relative)
    correction(records, cycle, planted_on="2026-07-03")
    views = {t["entry"]: t for t in records.get("owner", "field", cycle)["calendar"]["tasks"]}
    assert views["work-1"]["effective_window"] == dated["data"]["planned_window"] and views["work-1"]["needs_review"]
    assert views["relative"]["effective_window"] == relative["data"]["planned_window"]
    assert views["relative"]["cycle_changed_fields"] == ["planted_on"]


@pytest.mark.parametrize("change", [dict(title=""), dict(source=""), dict(notes=""), dict(priority="urgent"), dict(activity="pesticide_advice"),
    dict(due=dict(basis="date", start="2026-07-08", end="2026-07-06")), dict(due=dict(basis="date", start="2026-07-06")),
    dict(due=dict(basis="planting", start_offset_days=True, end_offset_days=7)),
    dict(due=dict(basis="planting", start_offset_days=1.0, end_offset_days=7)),
    dict(due=dict(basis="planting", start_offset_days=-366, end_offset_days=7)),
    dict(due=dict(basis="planting", start_offset_days=1, end_offset_days=3651)),
    dict(due=dict(basis="date", start="2026-07-06", end="2026-07-08", start_offset_days=1)), dict(status="completed")])
def test_invalid_plans_do_not_write(setup, change):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    with pytest.raises(ValueError):
        records.plan_task("owner", "field", cycle, task(**change), request_id="bad")
    assert len(records.get("owner", "field", cycle)["records"]) == 1


def test_stale_plan_ids_duplicate_entries_and_reused_requests_are_rejected(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    first = records.plan_task("owner", "field", cycle, task(), request_id="first")
    with pytest.raises(ValueError, match="already exists"):
        records.plan_task("owner", "field", cycle, task(), request_id="duplicate")
    with pytest.raises(ValueError, match="different data"):
        records.plan_task("owner", "field", cycle, task(priority="high"), request_id="first")
    records.plan_task("owner", "field", cycle, task(priority="high"), request_id="correct", supersedes_id=first["id"])
    with pytest.raises(ValueError, match="latest revision"):
        records.plan_task("owner", "field", cycle, task(), request_id="stale", supersedes_id=first["id"])


def test_completion_evidence_must_match_cycle_activity_and_day(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    other = create(records, request="other")["cycle"]["id"]
    planned = records.plan_task("owner", "field", cycle, task(), request_id="plan")
    wrong_cycle = records.record_harvest("owner", "field", other, harvest(), request_id="other-harvest")
    actual = records.record_harvest("owner", "field", cycle, harvest(), request_id="harvest")
    for reference, day in ((dict(kind="crop_record", id=wrong_cycle["id"]), "2026-07-06"),
                           (dict(kind="crop_record", id=planned["id"]), "2026-07-06"),
                           (dict(kind="crop_record", id=actual["id"]), "2026-07-07")):
        with pytest.raises(ValueError):
            resolve(records, planned, request="bad", day=day, evidence=reference)
    with pytest.raises(ValueError, match="today"):
        resolve(records, planned, request="future", day="2099-01-01")
    with pytest.raises(ValueError, match="only completed"):
        records.resolve_task("owner", "field", planned["id"], "cancelled", "Not needed", "Synthetic", request_id="bad", completed_on="2026-07-06")


def test_irrigation_link_needs_positive_accepted_water_and_rejection_is_flagged(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    planned = records.plan_task("owner", "field", cycle, task(activity="irrigation"), request_id="plan")
    workflow = FieldWorkflow(records.db)
    zero = workflow.record_day("owner", "field", water(net_irrigation_mm=0), cycle_id=cycle, request_id="zero")
    with pytest.raises(ValueError, match="match the task activity"):
        resolve(records, planned, request="bad", evidence=dict(kind="water_day", id=zero["id"]))
    actual = workflow.record_day("owner", "field", water(), cycle_id=cycle, request_id="actual", supersedes_id=zero["id"])
    complete = resolve(records, planned, evidence=dict(kind="water_day", id=actual["id"]))
    assert complete["data"]["resolution"]["completion_record"]["snapshot"] == actual
    workflow.record_day("owner", "field", water(quality="rejected"), cycle_id=cycle, request_id="reject", supersedes_id=actual["id"])
    assert records.get("owner", "field", cycle)["calendar"]["tasks"][0]["needs_review"]
    assert records.get("owner", "field", cycle)["calendar"]["completion_candidates"] == []


def test_tasks_do_not_constrain_closure_but_pending_crop_work_after_end_requires_review(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    records.plan_task("owner", "field", cycle, task(), request_id="plan")
    cleanup = records.plan_task("owner", "field", cycle, task(entry="cleanup", activity="maintenance"), request_id="cleanup")
    resolve(records, cleanup, day="2026-07-10")
    records.close_cycle("owner", "field", cycle, "2026-07-05", "Crop ended", "Synthetic", request_id="close", expected_revision=None)
    journal = records.get("owner", "field", cycle)
    pending = next(t for t in journal["calendar"]["tasks"] if t["status"] == "planned")
    assert pending["needs_review"] and "cycle end" in pending["review_reasons"][0]
    assert journal["calendar"]["counts"]["completed"] == 1 and journal["harvest_totals"] == []


@pytest.mark.parametrize("unit", ["field", "hydro", "aqua"])
def test_domain_support_scope_identity_export_and_erasure(setup, unit):
    records, farms = setup
    cycle = create(records, unit)["cycle"]["id"]
    other = create(records, unit, request="other")["cycle"]["id"]
    planned = execute_crop_records(records.db, "owner", unit, "plan_task", dict(cycle_id=cycle, data=task(), request_id="plan"))
    for target, data in ((other, task()), (cycle, task(entry="renamed"))):
        with pytest.raises(ValueError, match="preserve the cycle and entry"):
            records.plan_task("owner", unit, target, data, request_id="bad", supersedes_id=planned["id"])
    with pytest.raises(ValueError):
        records.plan_task("other", unit, cycle, task(), request_id="bad")
    with pytest.raises(ValueError):
        records.resolve_task("other", unit, planned["id"], "cancelled", "Wrong owner", "Synthetic", request_id="bad")
    completed = resolve(records, planned)
    assert completed in farms.export("owner")["crop_records"]
    compared = records.compare_seasons("owner", unit, [cycle, other])
    assert compared["seasons"][0]["calendar"]["counts"]["completed"] == 1
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())


def test_local_today_classification_and_completion_use_farm_timezone(setup, monkeypatch):
    import agronaut_agent.crop_records as module

    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    records.db.execute("UPDATE farms SET timezone='Pacific/Kiritimati' WHERE id='farm'")
    fixed = datetime(2026, 7, 6, 12, 30, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", Clock)
    for index, offsets in enumerate(((0, 1), (6, 6), (7, 9))):
        records.plan_task("owner", "field", cycle, task(entry=f"task-{index}",
            due=dict(basis="planting", start_offset_days=offsets[0], end_offset_days=offsets[1])), request_id=f"plan-{index}")
    calendar = records.get("owner", "field", cycle)["calendar"]
    assert calendar["as_of"] == "2026-07-07"
    assert [t["timing"] for t in calendar["tasks"]] == ["overdue", "due", "upcoming"]
    records.resolve_task("owner", "field", calendar["tasks"][1]["record_id"], "completed", "Done", "Synthetic",
                         request_id="complete", completed_on="2026-07-07")


def test_relative_window_overflow_is_rejected_and_old_plans_remain_readable_after_legacy_date_correction(setup):
    records, farms = setup
    farms.create_cycle("owner", ProductionCycle("early", "field", "tomato", "0001-01-01"))
    with pytest.raises(ValueError, match="outside supported"):
        records.plan_task("owner", "field", "early", task(due=dict(basis="planting", start_offset_days=-1, end_offset_days=0)), request_id="bad")
    cycle = create(records)["cycle"]["id"]
    records.plan_task("owner", "field", cycle, task(due=dict(basis="planting", start_offset_days=-365, end_offset_days=0)), request_id="plan")
    correction(records, cycle, planted_on="0001-01-01")
    view = records.get("owner", "field", cycle)["calendar"]["tasks"][0]
    assert view["effective_window"] is None and view["timing"] == "unknown" and view["needs_review"]
