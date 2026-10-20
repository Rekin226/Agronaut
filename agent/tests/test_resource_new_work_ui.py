"""Repeat-work forms retain historical use and distinguish alternatives from new work."""

from datetime import date

from agent.tests.test_resource_ledger_ui import ledger, with_plan
from agent.tests.test_resource_settlement_ui import fill, reserved
from agent.tests.test_resource_ui import click, fill_period
from agronaut_agent.tests.test_resource_ledger import clock as clock


def plans(brain, field):
    return brain.farm_resources("web", "operator", field["farm_id"], "list", {})["plans"]


def test_repeat_settled_work_requires_new_details_and_preserves_use_and_retry(tmp_path):
    at, brain, field, prior, _ = reserved(tmp_path)
    fill(at, prior)
    click(at, "Record usage settlement")
    old = plans(brain, field)[0]
    at.selectbox(key="resource_saved").set_value(old["id"]).run(timeout=15)
    click(at, "Start separate work from this scenario")
    assert at.date_input(key="resource_start").value is None
    assert at.date_input(key="resource_end").value is None
    assert at.text_input(key="resource_source").value == ""
    assert at.session_state["resource_parent"] is None
    fill_period(at)
    at.date_input(key="resource_start").set_value(date(2026, 10, 21))
    at.date_input(key="resource_end").set_value(date(2026, 10, 21))
    click(at, "Save resource scenario")
    assert at.error and len(plans(brain, field)) == 1
    at.text_input(key="resource_new_work_reason").set_value("Second field event after the previous work stopped")
    at.text_input(key="resource_title").set_value("  Second event  ")
    click(at, "Save resource scenario")
    assert not at.error
    fresh = plans(brain, field)[-1]
    assert fresh["parent_id"] is None and fresh["work_template_id"] == old["id"]
    assert at.session_state["resource_parent"] == fresh["id"]
    assert at.selectbox(key="resource_saved").value == fresh["id"]
    click(at, "Save resource scenario")
    click(at, "Evaluate draft allocation")
    click(at, "Save resource scenario")
    assert not at.error and len(plans(brain, field)) == 2
    at.text_input(key="resource_title").set_value("Alternative for second event")
    click(at, "Save resource scenario")
    assert plans(brain, field)[-1]["parent_id"] == fresh["id"]
    balance = ledger(brain, field)["budgets"][0]
    assert (balance["recorded_use"], balance["reserved"], balance["remaining"]) == ("450", "0", "550")
    assert len(ledger(brain, field)["commitments"]) == 2


def test_switch_to_alternative_or_blank_clears_new_work_mode_without_deleting_history(tmp_path):
    at, brain, field, _, saved = with_plan(tmp_path)
    at.selectbox(key="resource_saved").set_value(saved["id"]).run(timeout=15)
    click(at, "Start separate work from this scenario")
    at.text_input(key="resource_new_work_reason").set_value("Another event").run(timeout=15)
    click(at, "Use as starting scenario")
    assert "resource_new_work_template" not in at.session_state
    assert "resource_new_work_reason" not in at.session_state
    assert at.session_state["resource_parent"] == saved["id"]
    assert at.date_input(key="resource_start").value == date(2026, 10, 20)
    click(at, "Start separate work from this scenario")
    at.session_state["timing_result"] = {"old": "draft"}
    click(at, "Start blank work plan")
    assert at.session_state["resource_jobs"] == {} and at.session_state["resource_pools"] == {}
    for key in ("resource_parent", "resource_new_work_template", "resource_new_work_reason", "timing_result"):
        assert key not in at.session_state
    assert len(plans(brain, field)) == 1
    assert at.text_input(key="resource_title").value == ""


def test_farm_switch_clears_template_and_does_not_mutate_saved_inputs(tmp_path):
    at, brain, field, _, saved = with_plan(tmp_path)
    at.selectbox(key="resource_saved").set_value(saved["id"]).run(timeout=15)
    click(at, "Start separate work from this scenario")
    original = brain.farm_resources("web", "operator", field["farm_id"], "get", dict(plan_id=saved["id"]))
    at.selectbox(key="resource_pool_choice").set_value("shared-water").run(timeout=15)
    at.text_input(key="resource_pool_shared-water_available").set_value("123")
    click(at, "Keep resource in draft")
    assert brain.farm_resources("web", "operator", field["farm_id"], "get", dict(plan_id=saved["id"])) == original
    other = brain.create_unit("web", "operator", dict(farm=dict(name="Another farm", timezone="UTC"), unit=dict(name="Other field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    at.selectbox(key="resource_farm").set_value(other["farm_id"]).run(timeout=15)
    assert not at.exception and "resource_new_work_template" not in at.session_state
    assert at.session_state["resource_jobs"] == {} and at.session_state["resource_pools"] == {}
