"""Season and cross-season pages expose attributable event evidence and corrections."""

from agent.tests.test_crop_ui import planting
from agro_model.farm import ProductionCycle
from agronaut_agent.field_workflow import FieldWorkflow
from agronaut_agent.observation_quality import ObservationReviews
from agronaut_agent.tests.test_season_outcomes import event_review


def prepared(tmp_path):
    at, brain, unit, cycle = planting(tmp_path)
    owner = brain._conv.get_or_create_user("web", "operator")
    service, plan, first = event_review(FieldWorkflow(brain._farms.db), owner=owner, farm=unit["farm_id"], unit=unit["id"], cycle=cycle)
    second = service.save_outcome(owner, unit["farm_id"], plan["id"], "irrigate", dict(first["inputs"], source="Corrected attribution"),
        request_id="correction", supersedes_id=first["id"])
    at.run(timeout=15)
    assert not at.exception and not at.error
    return at, brain, owner, unit, cycle, first, second


def test_season_exposes_current_comparison_history_quality_flags_and_unit_scope(tmp_path):
    at, brain, owner, unit, cycle, first, second = prepared(tmp_path)
    table = next(f.value for f in at.dataframe if "review_id" in f.value.columns)
    assert list(table["review_id"]) == [second["id"], second["id"]]
    assert list(table["observed"]) == ["1000", "Unknown"]
    selector = "crop_outcome_history_"+cycle
    at.selectbox(key=selector).set_value(first["id"]).run(timeout=15)
    assert any("Selected review is superseded" in c.value for c in at.caption)
    ObservationReviews(brain._farms.db).review(owner, unit["id"], first["inputs"]["water"]["observation_id"], "rejected",
        "Wrong event", "Synthetic", request_id="reject", expected_revision=None)
    at.run(timeout=15)
    assert any("Some current irrigation comparisons" in w.value for w in at.warning)
    table = next(f.value for f in at.dataframe if "review_id" in f.value.columns)
    assert table["needs_review"].all()
    assert any("Water logs cover 0 of" in m.value for m in at.markdown)
    assert any(b.label == "Download season review" for b in at.get("download_button"))
    brain.create_unit("web", "operator", dict(farm=dict(name="Other", timezone="UTC"), unit=dict(name="Other field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    assert not at.exception
    assert selector not in at.session_state


def test_cross_season_page_keeps_current_reviews_separate_from_unknown_history(tmp_path):
    at, brain, owner, unit, cycle, first, second = prepared(tmp_path)
    brain._farms.create_cycle(owner, ProductionCycle("empty", unit["id"], "tomato", "2026-07-01"))
    at.run(timeout=15)
    at.multiselect(key="crop_compare_cycles").set_value([cycle, "empty"]).run(timeout=15)
    assert not at.exception and not at.error
    periods = next(f.value for f in at.dataframe if "current_irrigation_reviews" in f.value.columns)
    assert list(periods["current_irrigation_reviews"]) == [1, 0]
    table = next(f.value for f in at.dataframe if "cycle_id" in f.value.columns and "review_id" in f.value.columns)
    assert set(table["review_id"]) == {second["id"]}
    assert set(table["cycle_id"]) == {cycle}
    assert first["id"] not in set(table["review_id"])
    assert any("Review counts are not event counts" in c.value for c in at.caption)
    assert any(b.label == "Download season comparison" for b in at.get("download_button"))
