"""Planting amendments and transplant evidence retain history without moving actual events."""

import sqlite3
from datetime import datetime, timezone

import pytest

from agro_model.farm import Observation, ProductionCycle
from agronaut_agent.crop_records import execute_crop_records
from agronaut_agent.field_workflow import FieldWorkflow
from agronaut_agent.tests.test_crop_records import create, harvest, observation
from agronaut_agent.tests.test_crop_records import setup as setup
from agronaut_agent.tests.test_fertilizer_records import application
from agronaut_agent.tests.test_field_workflow import inputs, water
from agronaut_agent.tests.test_nutrition_cli import inputs as nutrient_inputs


def correction(records, cycle, **changes):
    journal = records.get("owner", "field", cycle)
    data = dict(crop=journal["cycle"]["crop"], planted_on=journal["cycle"]["planted_on"],
                source="Synthetic amended notebook", reason="Corrected transcription",
                request_id="correction", expected_revision=journal["planting"]["revision_id"])
    data.update(changes)
    return records.correct_planting("owner", "field", cycle, **data)


def transplant(**changes):
    return dict(dict(entry="transfer-1", day="2026-07-06", location="Synthetic row 1", origin="Synthetic nursery batch A",
                     plant_count=120, source="Synthetic transfer notebook", provenance="reported"), **changes)


def test_planting_correction_keeps_original_history_and_retry_results(setup):
    records, farms = setup
    original = create(records)
    cycle = original["cycle"]["id"]
    revision = records.get("owner", "field", cycle)["planting"]["revision_id"]
    assert revision == original["record"]["id"]
    saved = correction(records, cycle, crop="pepper", planted_on="2026-07-02", expected_revision=revision)
    assert saved["previous_cycle"] == original["cycle"]
    assert saved["supersedes_id"] == revision
    assert create(records) == original  # A retried creation never changes its response or reapplies the old date.
    assert correction(records, cycle, crop="pepper", planted_on="2026-07-02", expected_revision=revision) == saved
    current = records.get("owner", "field", cycle)
    assert current["cycle"] == dict(original["cycle"], crop="pepper", planted_on="2026-07-02")
    assert current["planting"]["revision_id"] == saved["id"]
    assert current["records"][0]["data"]["crop"] == "tomato" and current["records"][0]["superseded"]
    assert farms.list_cycles("owner", "field")[0]["planted_on"] == "2026-07-02"
    assert records.list("owner", "field")["cycles"][0]["crop"] == "pepper"
    with pytest.raises(ValueError, match="planting changed"):
        correction(records, cycle, request_id="stale", expected_revision=revision)
    with pytest.raises(ValueError, match="different data"):
        correction(records, cycle, crop="lettuce", expected_revision=revision)


def test_legacy_cycle_without_planting_record_gets_before_snapshot_and_can_correct_future_date(setup):
    records, farms = setup
    original = ProductionCycle("legacy", "field", "tomato", "2099-01-01")
    farms.create_cycle("owner", original)
    assert records.get("owner", "field", "legacy")["planting"]["revision_id"] is None
    saved = correction(records, "legacy", planted_on="2026-07-01")
    assert saved["previous_cycle"]["planted_on"] == "2099-01-01"
    assert saved["supersedes_id"] is None
    assert records.get("owner", "field", "legacy")["cycle"]["planted_on"] == "2026-07-01"
    assert saved in farms.export("owner")["crop_records"]


@pytest.mark.parametrize("kind", ["stage", "scouting", "harvest", "transplant", "water", "fertilizer"])
def test_later_planting_cannot_orphan_current_in_crop_activity(setup, kind):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    if kind == "water":
        row = FieldWorkflow(records.db).record_day("owner", "field", water(), cycle_id=cycle, request_id="activity")
    else:
        data = {"stage": observation(), "scouting": observation("scouting"), "harvest": harvest(),
                "transplant": transplant(), "fertilizer": application()}[kind]
        row = getattr(records, "record_"+kind)("owner", "field", cycle, data, request_id="activity")
    with pytest.raises(ValueError, match=f"current {kind} record {row['id']}"):
        correction(records, cycle, planted_on="2026-07-07")
    assert records.get("owner", "field", cycle)["cycle"]["planted_on"] == "2026-07-01"


def test_preplant_entries_set_the_other_date_boundary_and_voided_entries_do_not_block(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    data = application(phase="preplant", day="2026-06-30")
    row = records.record_fertilizer("owner", "field", cycle, data, request_id="fertilizer")
    with pytest.raises(ValueError, match="current fertilizer"):
        correction(records, cycle, planted_on="2026-06-29")
    void = records.void_fertilizer("owner", "field", row["id"], "Wrong date", request_id="void")
    correction(records, cycle, planted_on="2026-06-29")
    with pytest.raises(ValueError, match="preplant application must be on or before"):
        records.record_fertilizer("owner", "field", cycle, data, request_id="restore", supersedes_id=void["id"])


def test_rejected_water_can_remain_historical_but_cannot_be_restored_before_current_planting(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    workflow = FieldWorkflow(records.db)
    first = workflow.record_day("owner", "field", water(), cycle_id=cycle, request_id="actual")
    rejected = workflow.record_day("owner", "field", water(quality="rejected"), cycle_id=cycle,
                                   request_id="reject", supersedes_id=first["id"])
    correction(records, cycle, planted_on="2026-07-07")
    with pytest.raises(ValueError, match="precedes the crop cycle"):
        workflow.record_day("owner", "field", water(), cycle_id=cycle, request_id="restore", supersedes_id=rejected["id"])
    review = records.season_review("owner", "field", cycle)
    assert review["water"]["totals"] == [] and len(review["water"]["history"]) == 2


def test_typed_measurements_may_predate_planting_and_never_get_redated(setup):
    records, farms = setup
    cycle = create(records)["cycle"]["id"]
    at = datetime(2026, 7, 2, 12, tzinfo=timezone.utc)
    original = Observation("sample", "owner", "field", cycle, "rainfall", 2, "mm", at, at, "measured", "Synthetic soil field measurement")
    farms.record_observation("owner", original)
    before = farms.observations("owner", "field")
    correction(records, cycle, planted_on="2026-07-03")
    assert farms.observations("owner", "field") == before


def test_correction_cannot_cross_cycle_end_and_failure_rolls_back_both_record_and_cycle(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    records.close_cycle("owner", "field", cycle, "2026-07-05", "Finished", "Synthetic log", request_id="close", expected_revision=None)
    with pytest.raises(ValueError, match="after the cycle end"):
        correction(records, cycle, planted_on="2026-07-06")
    records.db.execute("CREATE TRIGGER fail_cycle_update BEFORE UPDATE ON production_cycles BEGIN SELECT RAISE(ABORT,'test update failure'); END")
    before = records.get("owner", "field", cycle)
    with pytest.raises(sqlite3.IntegrityError, match="test update failure"):
        correction(records, cycle, planted_on="2026-07-02")
    assert records.get("owner", "field", cycle) == before


@pytest.mark.parametrize("change", [dict(planted_on="2099-01-01"), dict(crop=""), dict(reason=""),
                                    dict(source=""), dict(expected_revision=True), dict(planted_on="2026-7-1")])
def test_invalid_planting_corrections_do_not_change_cycle(setup, change):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    before = records.get("owner", "field", cycle)
    with pytest.raises(ValueError):
        correction(records, cycle, **change)
    assert records.get("owner", "field", cycle) == before


def test_saved_calculations_retain_original_cycle_snapshot_and_report_current_conflicts(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    workflow = FieldWorkflow(records.db)
    saved = workflow.save("owner", "field", "root_zone_budget", inputs(), cycle_id=cycle, request_id="water")
    nutrient = workflow.save("owner", "field", "nutrient_budget", nutrient_inputs(), cycle_id=cycle, request_id="nutrient")
    assert saved["cycle_snapshot"]["crop"] == "tomato"
    correction(records, cycle, crop="pepper", planted_on="2026-07-07")
    assert workflow.get("owner", "field", saved["id"]) == saved
    review = records.season_review("owner", "field", cycle, saved["id"])
    assert review["comparison"]["calculation"] == saved
    assert review["comparison"]["cycle_review"]["changed_fields"] == ["crop", "planted_on"]
    assert len(review["comparison"]["cycle_review"]["conflicts"]) == 2
    assert all(r["cycle_snapshot_available"] for r in review["calculations"])
    assert len(next(r for r in review["calculations"] if r["id"] == nutrient["id"])["cycle_conflicts"]) == 1
    with pytest.raises(ValueError, match="crop must match"):
        workflow.save("owner", "field", "root_zone_budget", inputs(), cycle_id=cycle, request_id="new")
    assert workflow.save("owner", "field", "root_zone_budget", inputs(), cycle_id=cycle, request_id="water") == saved


def test_older_calculations_without_cycle_snapshots_and_surface_local_day_are_reviewable():
    from agronaut_agent.crop_establishment import calculation_cycle_review

    cycle = dict(crop="tomato", planted_on="2026-07-07")
    old = dict(task="root_zone_budget", inputs=inputs())
    reviewed = calculation_cycle_review(old, cycle, "UTC")
    assert not reviewed["snapshot_available"] and not reviewed["changed_fields"]
    assert reviewed["conflicts"] == ["calculation weather starts before current planting date"]
    surface = dict(task="surface_evaluation", inputs=dict(event=dict(started_at="2026-07-06T12:30:00+00:00")))
    assert not calculation_cycle_review(surface, cycle, "Pacific/Kiritimati")["conflicts"]
    assert calculation_cycle_review(surface, cycle, "UTC")["conflicts"] == ["surface event starts before current planting date"]


@pytest.mark.parametrize("unit", ["field", "hydro", "aqua"])
def test_transplant_records_across_domains_preserve_counts_and_revision_history(setup, unit):
    records, _ = setup
    cycle = create(records, unit)["cycle"]["id"]
    original = records.record_transplant("owner", unit, cycle, transplant(), request_id="transplant")
    revised = records.record_transplant("owner", unit, cycle, transplant(plant_count=None),
                                       request_id="correct", supersedes_id=original["id"])
    assert records.record_transplant("owner", unit, cycle, transplant(), request_id="transplant") == original
    void = records.void_transplant("owner", unit, revised["id"], "Wrong origin", request_id="void")
    assert records.void_transplant("owner", unit, revised["id"], "Wrong origin", request_id="void") == void
    records.record_transplant("owner", unit, cycle, transplant(plant_count=115), request_id="restore", supersedes_id=void["id"])
    records.record_transplant("owner", unit, cycle, transplant(entry="replacements", plant_count=10), request_id="replacement")
    journal = records.get("owner", unit, cycle)
    assert journal["cycle"]["planted_on"] == "2026-07-01"
    assert "transplant_totals" not in journal
    assert journal["records"][2]["data"]["plant_count"] is None
    assert records.season_review("owner", unit, cycle)["current_record_counts"]["transplant"] == 2
    with pytest.raises(ValueError, match="latest revision"):
        records.void_transplant("owner", unit, original["id"], "Stale", request_id="bad")
    with pytest.raises(ValueError, match="record kind"):
        records.void_observation("owner", unit, journal["records"][-1]["id"], "Wrong operation", request_id="bad")


@pytest.mark.parametrize("change", [dict(plant_count=True), dict(plant_count=0), dict(plant_count=1.5),
    dict(plant_count=-1), dict(plant_count=1_000_000_001), dict(plant_count="120"), dict(origin=""),
    dict(day="2099-01-01"), dict(day="2026-06-30"), dict(provenance="forecast"), dict(unit_id="other")])
def test_invalid_transplants_do_not_write(setup, change):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    with pytest.raises(ValueError):
        records.record_transplant("owner", "field", cycle, transplant(**change), request_id="bad")
    assert len(records.get("owner", "field", cycle)["records"]) == 1


def test_transplant_identity_ownership_closure_and_missing_count(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    other = create(records, request="other")["cycle"]["id"]
    original = records.record_transplant("owner", "field", cycle, transplant(), request_id="transplant")
    for owner, unit in (("other", "field"), ("owner", "other")):
        with pytest.raises(ValueError):
            execute_crop_records(records.db, owner, unit, "correct_planting", dict(cycle_id=cycle, crop="pepper",
                planted_on="2026-07-02", source="Synthetic", reason="Typo", request_id="bad", expected_revision=None))
        with pytest.raises(ValueError):
            records.record_transplant(owner, unit, cycle, transplant(), request_id="bad")
    for target, data in ((other, transplant()), (cycle, transplant(entry="renamed"))):
        with pytest.raises(ValueError, match="preserve the cycle and entry"):
            records.record_transplant("owner", "field", target, data, request_id="bad", supersedes_id=original["id"])
    missing = transplant()
    del missing["plant_count"]
    with pytest.raises(ValueError, match="plant_count is required"):
        records.record_transplant("owner", "field", cycle, missing, request_id="bad")
    with pytest.raises(ValueError, match="precedes a current actual"):
        records.close_cycle("owner", "field", cycle, "2026-07-05", "Finished", "Synthetic", request_id="close", expected_revision=None)
    void = records.void_transplant("owner", "field", original["id"], "Wrong date", request_id="void")
    records.close_cycle("owner", "field", cycle, "2026-07-05", "Finished", "Synthetic", request_id="close", expected_revision=None)
    with pytest.raises(ValueError, match="after the crop cycle end"):
        records.record_transplant("owner", "field", cycle, transplant(), request_id="restore", supersedes_id=void["id"])


def test_planting_and_transplants_share_the_operator_local_date_and_erase_with_account(setup, monkeypatch):
    import agronaut_agent.crop_records as module

    records, farms = setup
    cycle = create(records)["cycle"]["id"]
    records.db.execute("UPDATE farms SET timezone='Pacific/Kiritimati' WHERE id='farm'")
    fixed = datetime(2026, 7, 6, 12, 30, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", Clock)
    amended = correction(records, cycle, planted_on="2026-07-07")
    transfer = records.record_transplant("owner", "field", cycle, transplant(day="2026-07-07"), request_id="transfer")
    exported = farms.export("owner")
    assert amended in exported["crop_records"] and transfer in exported["crop_records"]
    assert exported["cycles"][0]["planted_on"] == "2026-07-07"
    farms.purge("owner")
    assert all(not rows for rows in farms.export("owner").values())
    with pytest.raises(ValueError):
        records.record_transplant("owner", "field", cycle, transplant(), request_id="late")
