"""Saved designs can be revised through native forms without a chat model."""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agent.tests.test_field_ui import _SCRIPT, setup


@pytest.mark.parametrize("method", ["drip", "sprinkler"])
def test_preview_invalidates_on_edit_and_save_selects_child(tmp_path, method):
    brain, _, other = setup(tmp_path)
    inputs = json.loads((Path(__file__).resolve().parents[2] / f"examples/{method}_design.json").read_text())
    parent = brain.field_workflow("web", "operator", "save",
        dict(task=method + "_design", inputs=inputs, request_id="equipment"))
    before = brain.field_workflow("web", "operator", "list", {})
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key="field_plan").set_value(parent["id"]).run(timeout=15)
    assert not at.exception and not at.error
    prefix = "field_equipment_revision_"
    at.multiselect(key=prefix + "sections").set_value(["supply"]).run(timeout=15)
    at.number_input(key=prefix + "supply_daily_available_l").set_value(0.0).run(timeout=15)
    at.text_area(key=prefix + "supply_source").set_value("Synthetic allowance reviewed").run(timeout=15)
    at.text_area(key=prefix + "reason").set_value("Synthetic source unavailable").run(timeout=15)
    at.button(key=prefix + "preview_button").click().run(timeout=15)
    assert not at.exception and not at.error
    assert at.session_state[prefix + "preview"]["result"]["feasible"] is False
    assert brain.field_workflow("web", "operator", "list", {}) == before
    at.number_input(key=prefix + "supply_daily_available_l").set_value(1.0).run(timeout=15)
    assert not any(b.key == prefix + "save" for b in at.button)

    at.button(key=prefix + "preview_button").click().run(timeout=15)
    at.button(key=prefix + "save").click().run(timeout=15)
    assert not at.exception and not at.error
    identifier = at.selectbox(key="field_plan").value
    assert identifier != parent["id"]
    child = brain.field_workflow("web", "operator", "get", dict(calculation_id=identifier))
    assert child["parent_id"] == parent["id"]
    assert child["inputs"]["supply"]["daily_available_l"] == 1
    assert child["result"]["feasible"] is False
    assert brain.field_workflow("web", "operator", "get", dict(calculation_id=parent["id"])) == parent
    assert brain.field_workflow("web", "operator", "list", {})["water_days"] == []
    assert at.multiselect(key=prefix + "sections").value == []
    at.selectbox(key="field_plan").set_value(other["id"]).run(timeout=15)
    assert not any(b.key == prefix + "save" for b in at.button)

def test_linked_target_requires_an_explicit_source_to_detach(tmp_path):
    from agronaut_agent.tests.test_equipment_consultation import answers

    brain, _, base = setup(tmp_path)
    inputs = base["inputs"]
    inputs["area_m2"] = 500
    budget = brain.field_workflow("web", "operator", "save",
        dict(task="root_zone_budget", inputs=inputs, request_id="budget"))
    supplied = dict(answers("drip_design"), application_input="budget", target_calculation=budget["id"],
        target_day="2026-07-06", target_reason="Synthetic full-area target", target_accepted="use")
    def call(op, data):
        return brain.field_workflow("web", "operator", op, data)
    draft = call("start_intake", {})
    while draft["status"] != "ready":
        draft = call("answer_intake", dict(answer=supplied[draft["next_field"]], expected_revision=draft["revision"]))
    parent = call("save_intake", dict(draft_id=draft["draft_id"], expected_revision=draft["revision"]))["calculation"]
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key="field_plan").set_value(parent["id"]).run(timeout=15)
    prefix = "field_equipment_revision_"
    at.selectbox(key=prefix + "policy").set_value("detach").run(timeout=15)
    at.text_area(key=prefix + "reason").set_value("Synthetic independent sizing study").run(timeout=15)
    at.button(key=prefix + "preview_button").click().run(timeout=15)
    assert not at.exception and any("source" in error.value for error in at.error)
    at.text_area(key=prefix + "detached_source").set_value("Synthetic independent target; keep amount for comparison").run(timeout=15)
    at.button(key=prefix + "preview_button").click().run(timeout=15)
    assert not at.exception and not at.error
    assert at.session_state[prefix + "preview"]["water_target"] is None
    at.button(key=prefix + "save").click().run(timeout=15)
    assert not at.exception and not at.error
    child = call("get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert child["parent_id"] == parent["id"] and "water_target" not in child
    assert call("get", dict(calculation_id=parent["id"])) == parent
