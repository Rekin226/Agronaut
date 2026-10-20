"""Season review preserves evidence, reports unknown coverage and compares explicit inputs."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from agro_model.farm import ProductionCycle
from agronaut_agent.crop_records import execute_crop_records
from agronaut_agent.field_workflow import FieldWorkflow
from agronaut_agent.tests.test_crop_records import create, harvest, observation
from agronaut_agent.tests.test_crop_records import setup as setup
from agronaut_agent.tests.test_cycle_lifecycle import close
from agronaut_agent.tests.test_field_workflow import inputs, water


def calculation_inputs():
    data = inputs()
    weather, event = data["weather"][0], data["events"][0]
    data["weather"] = [dict(weather, day=f"2026-07-{day:02d}") for day in range(6, 12)]
    data["events"] = [dict(event, day=w["day"], net_irrigation_mm=1) for w in data["weather"]]
    return data


def test_season_review_counts_only_current_accepted_assigned_days_and_preserves_gaps(setup):
    records, _ = setup
    workflow = FieldWorkflow(records.db)
    cycle = create(records)["cycle"]["id"]
    other = create(records, request="other-planting")["cycle"]["id"]
    records.record_harvest("owner", "field", cycle, harvest(), request_id="harvest")
    first = workflow.record_day("owner", "field", water(rainfall_mm=0, net_irrigation_mm=2, runoff_mm=0,
        provenance="measured", quality="checked"), cycle_id=cycle, request_id="six")
    original = workflow.record_day("owner", "field", water(day="2026-07-07"), cycle_id=cycle, request_id="seven")
    correction = workflow.record_day("owner", "field", water(day="2026-07-07", net_irrigation_mm=4),
        cycle_id=cycle, request_id="seven-correct", supersedes_id=original["id"])
    rejected = workflow.record_day("owner", "field", water(day="2026-07-08", quality="rejected"), cycle_id=cycle, request_id="eight")
    workflow.record_day("owner", "field", water(day="2026-07-09"), request_id="unassigned")
    workflow.record_day("owner", "field", water(day="2026-07-10"), cycle_id=other, request_id="other-cycle")
    plan = workflow.save("owner", "field", "root_zone_budget", calculation_inputs(), cycle_id=cycle, request_id="plan")
    close(records, cycle)
    review = records.season_review("owner", "field", cycle, plan["id"])
    assert review["water"]["coverage"] == dict(start="2026-07-01", through="2026-07-10", expected_days=10,
        recorded_days=2, missing_days=8, missing_ranges=[dict(start="2026-07-01", end="2026-07-05", days=5),
                                                       dict(start="2026-07-08", end="2026-07-10", days=3)])
    totals = review["water"]["totals"]
    assert len(totals) == 2
    measured = next(t for t in totals if t["provenance"] == "measured")
    assert measured["rainfall_mm"] == "0" and measured["net_irrigation_mm"] == "2"
    assert measured["record_ids"] == [first["id"]]
    reported = next(t for t in totals if t["provenance"] == "reported")
    assert reported["net_irrigation_mm"] == "4" and reported["record_ids"] == [correction["id"]]
    assert len(review["water"]["history"]) == 4
    assert next(r for r in review["water"]["history"] if r["id"] == original["id"])["superseded"]
    compared = review["comparison"]["days"]
    assert compared[0]["recorded_minus_input"]["net_irrigation_mm"] == "1"
    assert compared[1]["recorded_minus_input"]["net_irrigation_mm"] == "3"
    assert compared[2]["status"] == "rejected" and compared[2]["record_id"] == rejected["id"]
    assert compared[2]["recorded_minus_input"] is None
    assert compared[3]["status"] == "missing" and compared[3]["recorded"] is None
    assert compared[5]["status"] == "outside_review_window" and compared[5]["recorded_minus_input"] is None
    assert review["journal"]["harvest_totals"][0]["quantity"] == "10"
    assert review["comparison"]["calculation"] == plan


def test_replay_comparison_labels_copied_actual_inputs_and_retains_old_revision_reference(setup):
    records, _ = setup
    workflow = FieldWorkflow(records.db)
    cycle = create(records)["cycle"]["id"]
    base = workflow.save("owner", "field", "root_zone_budget", inputs(), cycle_id=cycle, request_id="base")
    original = workflow.record_day("owner", "field", water(), cycle_id=cycle, request_id="original")
    replay = workflow.replay("owner", "field", base["id"], [original["id"]], request_id="replay")
    corrected = workflow.record_day("owner", "field", water(net_irrigation_mm=4), cycle_id=cycle,
        request_id="correct", supersedes_id=original["id"])
    review = records.season_review("owner", "field", cycle, replay["id"])
    row = review["comparison"]["days"][0]
    assert row["replayed_record_id"] == original["id"] and row["record_id"] == corrected["id"]
    assert row["calculation_inputs"]["net_irrigation_mm"] == 3
    assert row["recorded_minus_input"]["net_irrigation_mm"] == "1"
    assert review["calculations"][1]["parent_id"] == base["id"]
    assert review["calculations"][1]["applied_water_record_ids"] == [original["id"]]
    assert "not savings" in review["comparison"]["interpretation"]
    assert workflow.get("owner", "field", replay["id"]) == replay


def test_missing_records_are_unknown_and_scouting_samples_stay_separate(setup):
    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    for entry in ("a", "b"):
        records.record_scouting("owner", "field", cycle, observation("scouting", entry=entry), request_id=entry)
    close(records, cycle)
    review = records.season_review("owner", "field", cycle)
    assert review["comparison"] is None
    assert review["journal"]["harvest_totals"] == []
    assert review["water"]["totals"] == []
    assert review["water"]["coverage"]["missing_days"] == 10
    assert review["current_record_counts"]["scouting"] == 2
    assert "scouting_totals" not in review
    assert len([r for r in review["journal"]["records"] if r["kind"] == "scouting"]) == 2


@pytest.mark.parametrize("unit", ["hydro", "aqua"])
def test_aquatic_crop_reviews_do_not_claim_missing_soil_water_logs(setup, unit):
    records, _ = setup
    cycle = create(records, unit)["cycle"]["id"]
    records.record_harvest("owner", unit, cycle, harvest(), request_id="harvest")
    review = execute_crop_records(records.db, "owner", unit, "season_review", dict(cycle_id=cycle))
    assert not review["water"]["applicable"] and review["water"]["coverage"] is None
    assert review["journal"]["harvest_totals"][0]["quantity"] == "10"


def test_review_is_read_only_and_rejects_other_owners_units_cycles_and_calculation_types(setup):
    records, _ = setup
    workflow = FieldWorkflow(records.db)
    cycle = create(records)["cycle"]["id"]
    other = create(records, request="other")["cycle"]["id"]
    foreign_cycle_plan = workflow.save("owner", "field", "root_zone_budget", inputs(), cycle_id=other, request_id="other-plan")
    wrong_type = workflow.save("owner", "field", "crop_water", inputs(), cycle_id=cycle, request_id="water-demand")
    before = deepcopy(records.get("owner", "field", cycle))
    before_count = records.db.query("SELECT total_changes()")[0][0]
    records.season_review("owner", "field", cycle)
    assert records.db.query("SELECT total_changes()")[0][0] == before_count
    assert records.get("owner", "field", cycle) == before
    for owner, unit, target in (("other", "field", cycle), ("owner", "hydro", cycle), ("owner", "field", None)):
        with pytest.raises(ValueError):
            records.season_review(owner, unit, target)
    with pytest.raises(ValueError, match="does not belong"):
        records.season_review("owner", "field", cycle, foreign_cycle_plan["id"])
    with pytest.raises(ValueError, match="root_zone_budget"):
        records.season_review("owner", "field", cycle, wrong_type["id"])
    with pytest.raises(ValueError, match="unknown fields"):
        execute_crop_records(records.db, "owner", "field", "season_review", dict(cycle_id=cycle, owner_id="other"))


def test_open_cycle_coverage_stops_at_last_completed_farm_local_day(setup, monkeypatch):
    import agronaut_agent.crop_records as module

    records, _ = setup
    cycle = create(records)["cycle"]["id"]
    records.db.execute("UPDATE farms SET timezone='Pacific/Kiritimati' WHERE id='farm'")
    fixed = datetime(2026, 7, 6, 12, 30, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)

    monkeypatch.setattr(module, "datetime", Clock)
    review = records.season_review("owner", "field", cycle)
    assert review["period"]["through"] == "2026-07-07"
    assert review["water"]["coverage"]["through"] == "2026-07-06"
    assert review["water"]["coverage"]["expected_days"] == 6


def test_legacy_future_cycle_has_no_completed_days_and_long_gaps_are_compact(setup):
    records, farms = setup
    farms.create_cycle("owner", ProductionCycle("future", "field", "tomato", "2099-01-01"))
    review = records.season_review("owner", "field", "future")
    assert review["period"]["not_started"] and review["period"]["through"] is None
    assert review["water"]["coverage"]["expected_days"] == 0
    assert review["water"]["coverage"]["missing_ranges"] == []
    farms.create_cycle("owner", ProductionCycle("old", "field", "tomato", "2000-01-01"))
    review = records.season_review("owner", "field", "old")
    assert review["water"]["coverage"]["expected_days"] > 9000
    assert len(review["water"]["coverage"]["missing_ranges"]) == 1
