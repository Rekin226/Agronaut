"""Whole-farm allocation via real forms without a model provider."""

from datetime import date

from streamlit.testing.v1 import AppTest

from agent.tests.test_field_ui import setup

SCRIPT = """
from agent.tests.test_field_ui import _SHARED
from agent.resource_ui import render_farm_resources
render_farm_resources(_SHARED['brain'], 'operator')
"""


def click(at, label):
    next(b for b in at.button if b.label == label).click().run(timeout=15)
    assert not at.exception


def start(tmp_path):
    brain, field, _ = setup(tmp_path)
    hydro = brain.create_unit("web", "operator", dict(farm=dict(id=field["farm_id"]), unit=dict(name="Hydro",
        production_system="hydroponics", medium="water", environment="greenhouse", water_strategy="fully_irrigated")))
    at = AppTest.from_string(SCRIPT).run(timeout=15)
    at.selectbox(key="resource_farm").set_value(field["farm_id"]).run(timeout=15)
    assert not at.exception
    at.selectbox(key="resource_pool_new_kind").set_value("water").run(timeout=15)
    for key, value in dict(id="shared-water", available="1000", source="Synthetic net water supply").items():
        at.text_input(key="resource_pool_new_"+key).set_value(value)
    click(at, "Keep resource in draft")
    return at, brain, field, hydro


def add_job(at, unit, name, priority, amount):
    at.selectbox(key="resource_job_new_unit_id").set_value(unit["id"]).run(timeout=15)
    for key, value in dict(id=name, title=name, block_label="One synthetic work event", source="Synthetic requirements",
                           **{"amount_shared-water": amount}).items():
        at.text_input(key="resource_job_new_"+key).set_value(value)
    for key, value in dict(priority=priority, requested_blocks=1, minimum_blocks=1).items():
        at.number_input(key="resource_job_new_"+key).set_value(value)
    click(at, "Keep work in draft")


def fill_period(at):
    at.text_input(key="resource_title").set_value("Synthetic mixed-farm allocation")
    at.text_input(key="resource_source").set_value("Operator supplied priorities and needs")
    at.date_input(key="resource_start").set_value(date(2026, 10, 20))
    at.date_input(key="resource_end").set_value(date(2026, 10, 20))


def test_two_units_share_one_budget_and_saved_alternatives_can_be_compared(tmp_path):
    at, brain, field, hydro = start(tmp_path)
    add_job(at, field, "field-work", 1, "700")
    add_job(at, hydro, "hydro-work", 2, "500")
    fill_period(at)
    click(at, "Evaluate draft allocation")
    assert not at.error
    assert brain.farm_resources("web", "operator", field["farm_id"], "list", {})["plans"] == []
    table = next(frame.value for frame in at.dataframe if "allocated_blocks" in frame.value.columns)
    assert list(table["allocated_blocks"]) == [1, 0]
    click(at, "Save resource scenario")
    assert not at.error
    listing = brain.farm_resources("web", "operator", field["farm_id"], "list", {})["plans"]
    first = listing[0]["id"]
    at.selectbox(key="resource_saved").set_value(first).run(timeout=15)
    click(at, "Use as starting scenario")
    at.selectbox(key="resource_pool_choice").set_value("shared-water").run(timeout=15)
    at.text_input(key="resource_pool_shared-water_available").set_value("2000")
    click(at, "Keep resource in draft")
    at.text_input(key="resource_title").set_value("More available water")
    click(at, "Save resource scenario")
    assert not at.error
    listing = brain.farm_resources("web", "operator", field["farm_id"], "list", {})["plans"]
    assert len(listing) == 2 and listing[1]["parent_id"] == first
    at.multiselect(key="resource_compare").set_value([p["id"] for p in listing]).run(timeout=15)
    assert not at.exception and not at.error
    table = next(frame.value for frame in at.dataframe if "scenario" in frame.value.columns)
    assert list(table["assigned"]) == ["700", "1200"]
    assert any(b.label == "Download resource comparison" for b in at.get("download_button"))
    saved = brain.farm_resources("web", "operator", field["farm_id"], "get", dict(plan_id=first))
    assert brain._farms.export(saved["owner_id"])["water_days"] == []
    assert brain._farms.export(saved["owner_id"])["crop_records"] == []


def test_unknown_requirement_cannot_be_evaluated_and_farm_switch_clears_draft(tmp_path):
    at, brain, field, _ = start(tmp_path)
    add_job(at, field, "unknown-work", 1, "")
    fill_period(at)
    click(at, "Save resource scenario")
    assert at.error
    assert brain.farm_resources("web", "operator", field["farm_id"], "list", {})["plans"] == []
    other = brain.create_unit("web", "operator", dict(farm=dict(name="Other", timezone="UTC"), unit=dict(name="Other field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    at.selectbox(key="resource_farm").set_value(other["farm_id"]).run(timeout=15)
    assert not at.exception
    assert at.session_state["resource_jobs"] == {} and at.session_state["resource_pools"] == {}
    assert at.text_input(key="resource_title").value == ""
    assert "resource_result" not in at.session_state


def test_full_app_resource_mode_is_available_without_model(tmp_path):
    setup(tmp_path)
    script = """
import streamlit as st
import app
from agent.tests.test_field_ui import _SHARED
st.session_state.agent = _SHARED['brain']
st.session_state.web_user = 'operator'
st.session_state.app_mode = 'Farm resources'
app.main()
"""
    at = AppTest.from_string(script).run(timeout=20)
    assert not at.exception
    assert at.selectbox(key="resource_farm").value is None


def test_changing_resource_dimension_requires_new_work_amounts(tmp_path):
    at, brain, field, _ = start(tmp_path)
    add_job(at, field, "field-work", 1, "700")
    fill_period(at)
    click(at, "Evaluate draft allocation")
    assert not at.error
    at.selectbox(key="resource_pool_choice").set_value("shared-water").run(timeout=15)
    at.selectbox(key="resource_pool_shared-water_kind").set_value("cash").run(timeout=15)
    at.text_input(key="resource_pool_shared-water_currency").set_value("USD")
    click(at, "Keep resource in draft")
    assert at.session_state["resource_jobs"]["field-work"]["requirements"]["shared-water"] is None
    click(at, "Save resource scenario")
    assert at.error and not at.exception
    assert brain.farm_resources("web", "operator", field["farm_id"], "list", {})["plans"] == []
    at.selectbox(key="resource_job_choice").set_value("field-work").run(timeout=15)
    assert not at.exception
    assert at.text_input(key="resource_job_field-work_amount_shared-water").value is None
    at.text_input(key="resource_job_field-work_amount_shared-water").set_value("10")
    click(at, "Keep work in draft")
    click(at, "Save resource scenario")
    assert not at.error
