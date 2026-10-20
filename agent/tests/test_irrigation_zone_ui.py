"""Native zone creation keeps the selected identity and its records explicit."""

import pytest

from agent.tests.test_farm_ui import NoModel, run
from agronaut_agent.core import AgronautAgent
from agronaut_agent.tests.test_irrigation_zones import zone
from agronaut_agent.unit_context import UnitContexts


def prepared(tmp_path):
    brain = AgronautAgent(db_path=tmp_path/"zone-ui.sqlite3", chat_model=NoModel())
    parent = brain.create_unit("web", "operator", dict(farm=dict(name="Home", timezone="UTC"), unit=dict(name="North field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at = run(brain)
    at.selectbox(key="farm_zone_parent").set_value(parent["id"]).run(timeout=15)
    assert not at.exception
    return at, brain, parent


def fill(at):
    for key, value in dict(key="north-drip", name="Tomato drip zone", area="500.25", source="Operator layout notebook").items():
        at.text_input(key="farm_zone_"+key).set_value(value)
    at.text_area(key="farm_zone_boundary").set_value("Inside the north fence, rows 1–10")
    at.selectbox(key="farm_zone_strategy").set_value("fully_irrigated")
    at.selectbox(key="farm_zone_method").set_value("drip")


def click(at):
    next(b for b in at.button if b.label == "Create and use zone").click().run(timeout=15)
    assert not at.exception


def test_create_select_retry_and_parent_switch_clear_unit_data(tmp_path):
    at, brain, parent = prepared(tmp_path)
    at.session_state["messages"] = [dict(role="assistant", content="Advice for the whole field")]
    at.session_state["field_plan"] = "parent-plan"
    fill(at)
    click(at)
    assert not at.error
    units = brain.list_units("web", "operator")
    selected = next(u for u in units if u["active"])
    assert selected["parent_unit_id"] == parent["id"] and selected["name"] == "Tomato drip zone"
    assert selected["irrigation_method"] == "drip" and selected["environment"] == parent["environment"]
    assert at.session_state["messages"] == [] and "field_plan" not in at.session_state
    assert any("Irrigation zone of North field" in c.value for c in at.caption)
    details = brain.field_workflow("web", "operator", "list_zones", {}, expected_unit_id=selected["id"])
    assert details["zone"]["data"]["ground_area_m2"] == "500.25"
    click(at)
    assert not at.error and len(brain.list_units("web", "operator")) == 2
    at.selectbox(key="farm_unit_choice").set_value(parent["id"])
    at.button(key="farm_use_unit").click().run(timeout=15)
    assert not at.exception
    assert next(u for u in brain.list_units("web", "operator") if u["active"])["id"] == parent["id"]
    assert brain.list_cycles("web", "operator", selected["id"]) == []


def test_invalid_zone_does_not_switch_parent_and_changing_parent_clears_boundary_draft(tmp_path):
    at, brain, parent = prepared(tmp_path)
    fill(at)
    at.text_input(key="farm_zone_area").set_value("0")
    click(at)
    assert at.error and len(brain.list_units("web", "operator")) == 1
    assert next(u for u in brain.list_units("web", "operator") if u["active"])["id"] == parent["id"]
    other = brain.create_unit("web", "operator", dict(farm=dict(id=parent["farm_id"]), unit=dict(name="Other field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="supplemental", irrigation_method="sprinkler")))
    at.run(timeout=15)
    at.selectbox(key="farm_zone_parent").set_value(other["id"]).run(timeout=15)
    assert not at.exception
    assert at.text_input(key="farm_zone_name").value == ""
    assert at.text_area(key="farm_zone_boundary").value == ""
    assert at.text_input(key="farm_zone_area").value == ""


def test_zone_creation_and_selection_roll_back_together_and_stale_scope_rejects(tmp_path, monkeypatch):
    _, brain, parent = prepared(tmp_path)
    owner = brain._contexts.active(brain._conv.get_or_create_user("web", "operator")).owner_id
    before = brain._farms.export(owner)
    original = UnitContexts.select

    def fail(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("Synthetic interrupted zone selection")

    monkeypatch.setattr(UnitContexts, "select", fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        brain.create_zone("web", "operator", parent["id"], zone(), "zone")
    assert brain._farms.export(owner) == before
    assert brain._contexts.active(owner).unit_id == parent["id"]
    monkeypatch.setattr(UnitContexts, "select", original)
    created = brain.create_zone("web", "operator", parent["id"], zone(), "zone")
    assert brain._contexts.active(owner).unit_id == created["id"]
    with pytest.raises(ValueError, match="selected unit changed"):
        brain.field_workflow("web", "operator", "create_zone", dict(data=zone(zone_key="south"), request_id="south"), expected_unit_id=parent["id"])
    assert len(brain.list_units("web", "operator")) == 2
