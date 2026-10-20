"""Season evidence exposes event comparisons without counting alternatives as actuals."""

import pytest

from agro_model.farm import ProductionCycle
from agronaut_agent.crop_records import CropRecords
from agronaut_agent.farm_resources import FarmResources
from agronaut_agent.observation_quality import ObservationReviews
from agronaut_agent.tests.test_equipment_consultation import example
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_field_workflow import water
from agronaut_agent.tests.test_resource_designs import resource_plan, specification
from agronaut_agent.tests.test_resource_outcomes import measurement


def event_review(workflow, *, owner="owner", farm="farm", unit="field", cycle="cycle", key="first"):
    source = workflow.save(owner, unit, "drip_design", example("drip_design"), cycle_id=cycle, request_id=key)
    service = FarmResources(workflow.db)
    spec = specification(source)
    plan = service.save(owner, farm, resource_plan(service.design_job(owner, farm, spec), spec), request_id=key)
    observed = measurement(workflow, owner=owner, unit=unit, cycle=cycle, key=key)
    data = dict(actual_day="2026-07-07", water=dict(observation_id=observed["id"], expected_review_id=None), pump=None,
                attribution="Synthetic complete event including maintenance on mapped source", source="Synthetic operator")
    review = service.save_outcome(owner, farm, plan["id"], "irrigate", data, request_id=key)
    return service, plan, review


def test_history_and_alternatives_preserve_evidence_without_adding_water_coverage(setup):
    workflow, farms = setup
    service, plan, first = event_review(workflow)
    second = service.save_outcome("owner", "farm", plan["id"], "irrigate", dict(first["inputs"], source="Revised attribution source"),
        request_id="correction", supersedes_id=first["id"])
    alternative = service.save("owner", "farm", dict(plan["inputs"], title="Alternative"), request_id="alternative", parent_id=plan["id"])
    third = service.save_outcome("owner", "farm", alternative["id"], "irrigate", second["inputs"], request_id="alternative-review")
    workflow.record_day("owner", "field", water(), cycle_id="cycle", request_id="daily")
    before = farms.export("owner")
    season = CropRecords(workflow.db).season_review("owner", "field", "cycle")
    section = season["irrigation_outcomes"]
    assert section["counts"] == dict(reviews=3, current=2, superseded=1, current_needing_review=0)
    assert [r["id"] for r in section["reviews"]] == [first["id"], second["id"], third["id"]]
    assert [r["superseded"] for r in section["reviews"]] == [True, False, False]
    assert section["reviews"][0]["plan_snapshot"] == first["plan_snapshot"]
    assert section["reviews"][0]["observations"] == first["observations"]
    assert season["water"]["coverage"]["recorded_days"] == 1
    assert season["water"]["totals"][0]["net_irrigation_mm"] == "3"
    assert "totals" not in section and "coverage" not in section
    assert farms.export("owner") == before


def test_exact_cycle_unit_and_owner_scope_including_unassigned_evidence(setup):
    workflow, farms = setup
    farms.create_cycle("owner", ProductionCycle("next", "field", "tomato", "2026-07-01"))
    _, _, first = event_review(workflow)
    event_review(workflow, cycle="next", key="next")
    event_review(workflow, cycle=None, key="unassigned")
    event_review(workflow, farm="farm2", unit="field2", cycle="cycle2", key="other-unit")
    event_review(workflow, owner="other", farm="farm3", unit="field3", cycle="cycle3", key="other-owner")
    records = CropRecords(workflow.db)
    rows = records.season_review("owner", "field", "cycle")["irrigation_outcomes"]["reviews"]
    assert [r["id"] for r in rows] == [first["id"]]
    for owner, unit, cycle in (("other", "field", "cycle"), ("owner", "field", "cycle2"), ("owner", "field3", "cycle3")):
        with pytest.raises(ValueError):
            records.season_review(owner, unit, cycle)


def test_changed_quality_planting_and_timezone_flag_history_without_rewriting_it(setup):
    workflow, _ = setup
    _, _, saved = event_review(workflow)
    ObservationReviews(workflow.db).review("owner", "field", saved["inputs"]["water"]["observation_id"], "rejected",
        "Wrong event attribution", "Synthetic reviewer", request_id="reject", expected_revision=None)
    records = CropRecords(workflow.db)
    records.correct_planting("owner", "field", "cycle", "pepper", "2026-07-08", "Synthetic", "Correct date", request_id="planting", expected_revision=None)
    workflow.db.execute("UPDATE farms SET timezone='Pacific/Honolulu' WHERE id='farm'")
    season = records.season_review("owner", "field", "cycle")
    section = season["irrigation_outcomes"]
    assert section["counts"]["current_needing_review"] == 1
    row = section["reviews"][0]
    assert row["review"]["plan"]["needs_review"] and row["review"]["observations"][0]["needs_review"]
    assert row["actual_day"] == "2026-07-07" and row["timezone"] == "UTC"
    assert season["period"]["timezone"] == "Pacific/Honolulu"
    assert row["comparisons"] == saved["comparisons"] and row["observations"] == saved["observations"]
    assert season["water"]["totals"] == []


def test_season_comparison_keeps_current_alternatives_and_unknown_history_separate(setup):
    workflow, farms = setup
    farms.create_cycle("owner", ProductionCycle("empty", "field", "tomato", "2026-07-01"))
    service, plan, first = event_review(workflow)
    second = service.save_outcome("owner", "farm", plan["id"], "irrigate", dict(first["inputs"], source="Correction"),
        request_id="correct", supersedes_id=first["id"])
    result = CropRecords(workflow.db).compare_seasons("owner", "field", ["cycle", "empty"])
    a, b = [s["irrigation_outcomes"] for s in result["seasons"]]
    assert [r["id"] for r in a["reviews"]] == [second["id"]]
    assert a["reviews"][0]["supersedes_id"] == first["id"]
    assert a["counts"]["superseded"] == 1
    assert b["reviews"] == [] and b["counts"]["current"] == 0
    assert "totals" not in a and "totals" not in b
    assert all(s["water"]["coverage"]["recorded_days"] == 0 for s in result["seasons"])
