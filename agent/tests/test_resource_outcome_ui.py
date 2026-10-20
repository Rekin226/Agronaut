"""Recorded totals, explicit attribution and immutable corrections through real forms."""

from datetime import date

from agent.tests import session_keys
from agent.tests.test_resource_design_ui import prepared
from agent.tests.test_resource_ui import click, fill_period
from agronaut_agent.observation_quality import ObservationReviews


def ready(tmp_path):
    at, brain, field, _ = prepared(tmp_path)
    at.date_input(key="resource_design_day").set_value(date(2026, 7, 7))
    click(at, "Preview irrigation job")
    click(at, "Keep reviewed irrigation job in draft")
    fill_period(at)
    at.date_input(key="resource_start").set_value(date(2026, 7, 7))
    at.date_input(key="resource_end").set_value(date(2026, 7, 7))
    click(at, "Save resource scenario")
    assert not at.error
    plan = at.session_state["resource_result"]
    record = brain.crop_records("web", "operator", "record_measurement", dict(request_id="event", data=dict(
        quantity="irrigation_volume", value=1000, unit="L", observed_at="2026-07-07T12:00:00Z",
        source="Synthetic whole-event water total", provenance="measured")))
    at.selectbox(key="resource_saved").set_value(plan["id"]).run(timeout=15)
    at.selectbox(key="resource_outcome_job").set_value("irrigate").run(timeout=15)
    at.date_input(key="resource_outcome_irrigate_day").set_value(date(2026, 7, 7))
    at.selectbox(key="resource_outcome_irrigate_water").set_value(record["id"])
    at.text_input(key="resource_outcome_irrigate_attribution").set_value("Complete event including maintenance on mapped source")
    at.text_input(key="resource_outcome_irrigate_source").set_value("Synthetic operator review")
    return at, brain, field, plan, record


def test_evaluation_is_readonly_unknown_stays_unknown_and_save_is_idempotent(tmp_path):
    at, brain, field, plan, record = ready(tmp_path)
    before = brain._farms.export(plan["owner_id"])
    click(at, "Evaluate event comparison")
    assert not at.error
    result = at.session_state["resource_outcome_result"]
    assert result["observations"]["water"]["id"] == record["id"]
    assert result["comparisons"][1]["relation"] == "unknown"
    assert brain._farms.export(plan["owner_id"]) == before
    click(at, "Save outcome review")
    assert not at.error
    after = brain._farms.export(plan["owner_id"])
    assert len(after["resource_outcomes"]) == 1
    assert {k: v for k, v in after.items() if k != "resource_outcomes"} == {k: v for k, v in before.items() if k != "resource_outcomes"}
    click(at, "Save outcome review")
    assert brain._farms.export(plan["owner_id"]) == after
    reviews = brain.farm_resources("web", "operator", field["farm_id"], "list_outcomes", dict(plan_id=plan["id"]))["reviews"]
    assert not reviews[0]["superseded"] and not reviews[0]["review"]["needs_review"]


def test_quality_changes_flag_original_and_explicit_save_retains_both_revisions(tmp_path):
    at, brain, field, plan, record = ready(tmp_path)
    click(at, "Save outcome review")
    first = at.session_state["resource_outcome_result"]
    ObservationReviews(brain._farms.db).review(plan["owner_id"], field["id"], record["id"], "checked",
        "Reviewed event attribution", "Synthetic operator", request_id="quality", expected_revision=None)
    at.run(timeout=15)
    assert any("current evidence" in w.value for w in at.warning)
    at.text_input(key="resource_outcome_irrigate_source").set_value("Explicitly adopt checked observation")
    click(at, "Save outcome review")
    assert not at.error
    reviews = brain.farm_resources("web", "operator", field["farm_id"], "list_outcomes", dict(plan_id=plan["id"]))["reviews"]
    assert len(reviews) == 2 and reviews[0]["superseded"]
    assert reviews[0]["comparisons"] == first["comparisons"]
    assert reviews[0]["observations"]["water"]["effective_quality"] == "unverified"
    assert reviews[1]["observations"]["water"]["effective_quality"] == "checked"
    assert reviews[1]["supersedes_id"] == first["id"]


def test_missing_attribution_is_rejected_and_farm_switch_clears_outcome_draft(tmp_path):
    at, brain, _, plan, _ = ready(tmp_path)
    at.text_input(key="resource_outcome_irrigate_attribution").set_value("")
    click(at, "Save outcome review")
    assert at.error
    assert brain._farms.export(plan["owner_id"])["resource_outcomes"] == []
    other = brain.create_unit("web", "operator", dict(farm=dict(name="Other", timezone="UTC"), unit=dict(name="Other field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    at.selectbox(key="resource_farm").set_value(other["farm_id"]).run(timeout=15)
    assert not at.exception
    assert not any(key.startswith("resource_outcome_") for key in session_keys(at))
