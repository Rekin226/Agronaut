"""Native reviewed state adoption, current review guards and explicit withdrawal."""

import json
from fractions import Fraction

from streamlit.testing.v1 import AppTest

from agent.tests.test_field_ui import _SCRIPT, setup
from agent.tests.test_soil_state_ui import record
from agro_model.soil_snapshots import loads_soil_snapshot
from agronaut_agent.coupled_state_workflow import continuation_checkpoint
from agronaut_agent.observation_quality import ObservationReviews
from agronaut_agent.tests.test_coupled_water_workflow import example

P = "field_coupled_state_"


def filled(tmp_path):
    brain, unit, old = setup(tmp_path)
    sample = record(brain)
    parent = brain.field_workflow("web", "operator", "save", dict(task="coupled_soil_water", inputs=example(), request_id="coupled"))
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key="field_plan").set_value(parent["id"]).run(timeout=15)
    at.multiselect(key=P+"readings").set_value([sample["id"]]).run(timeout=15)
    at.selectbox(key=P+"kind").set_value("synthetic")
    at.text_input(key=P+"reason").set_value("Synthetic layer state review")
    q = P+sample["id"]+"_"
    at.selectbox(key=q+"layer").set_value(json.dumps(["plot", "upper"]))
    at.text_input(key=q+"coverage").set_value("Synthetic complete upper layer and patch coverage")
    at.number_input(key=q+"adjustment").set_value(0.)
    at.text_input(key=q+"timing").set_value("Synthetic no change to midnight")
    at.selectbox(key=P+"memory_mode").set_value("Retain reviewed current memories")
    at.text_input(key=P+"memory_source").set_value("Synthetic reviewed drying history")
    return at, brain, unit, old, parent, sample


def click(at, label="Save reviewed soil continuation state"):
    next(b for b in at.button if b.label == label).click().run(timeout=15)
    assert not at.exception


def test_native_adoption_displays_separate_adjustment_and_withdrawal_restores_model(tmp_path):
    at, brain, _, _, parent, _ = filled(tmp_path)
    click(at)
    assert not at.error
    child = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert child["parent_id"] == parent["id"] and child["result"] == parent["result"]
    adopted = loads_soil_snapshot(continuation_checkpoint(child), expected_kind="state")
    assert adopted.soil.patches[0].compartments[0].water_content == Fraction(1, 4)
    assert any("Continuation uses a reviewed soil state" in t.value for t in at.info)
    assert any("Water (m³/m³)" in frame.value.columns and "0.25" in frame.value["Water (m³/m³)"].tolist() for frame in at.dataframe)
    assert at.multiselect(key=P+"readings").value == []
    assert brain.field_workflow("web", "operator", "list", {})["water_days"] == []
    at.text_input(key=P+"remove_reason").set_value("Synthetic withdrawal")
    click(at, "Restore the modeled ending state in a new version")
    restored = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert "coupled_state_revision" not in restored
    assert continuation_checkpoint(restored) == continuation_checkpoint(parent)


def test_changed_review_before_click_blocks_adoption_then_allows_fresh_review(tmp_path):
    at, brain, unit, _, parent, sample = filled(tmp_path)
    ObservationReviews(brain._farms.db).review(parent["owner_id"], unit["id"], sample["id"], "checked", "Synthetic check", "Synthetic",
        request_id="checked", expected_revision=None)
    click(at)
    assert any("review changed" in e.value for e in at.error)
    assert at.selectbox(key="field_plan").value == parent["id"]
    click(at)
    assert not at.error and at.selectbox(key="field_plan").value != parent["id"]


def test_memory_review_cannot_be_blank_and_replacement_values_are_explicit(tmp_path):
    at, brain, _, _, parent, _ = filled(tmp_path)
    at.text_input(key=P+"memory_source").set_value("")
    click(at)
    assert at.error and at.selectbox(key="field_plan").value == parent["id"]
    at.selectbox(key=P+"memory_mode").set_value("Supply replacement memories")
    at.text_area(key=P+"memories").set_value("plot,0,0.15,0.5,Synthetic replacement history")
    click(at)
    assert not at.error
    child = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    memory = dict(loads_soil_snapshot(continuation_checkpoint(child), expected_kind="state").evaporation_memories)["plot"]
    assert memory.stage_one_remaining_mm == 0 and memory.stage_two_start_fraction == Fraction(1, 2)


def test_calculation_and_empty_cycle_switches_clear_adoption_drafts(tmp_path):
    at, brain, _, old, parent, _ = filled(tmp_path)
    at.selectbox(key="field_plan").set_value(old["id"]).run(timeout=15)
    at.selectbox(key="field_plan").set_value(parent["id"]).run(timeout=15)
    assert at.multiselect(key=P+"readings").value == [] and at.text_input(key=P+"reason").value == ""
    at.text_input(key=P+"reason").set_value("Unsubmitted draft")
    cycle = brain.crop_records("web", "operator", "create", dict(crop="tomato", planted_on="2026-07-01", source="Synthetic", request_id="empty"))["cycle"]["id"]
    at.run(timeout=15)
    at.selectbox(key="field_cycle").set_value(cycle).run(timeout=15)
    at.selectbox(key="field_cycle").set_value(None).run(timeout=15)
    assert at.text_input(key=P+"reason").value == ""


def test_cycle_switch_with_stale_form_submission_cannot_adopt_into_old_cycle(tmp_path):
    at, brain, _, _, parent, _ = filled(tmp_path)
    cycle = brain.crop_records("web", "operator", "create", dict(crop="tomato", planted_on="2026-07-01", source="Synthetic", request_id="new-cycle"))["cycle"]["id"]
    at.run(timeout=15)
    before = brain._farms.export(parent["owner_id"])
    at.selectbox(key="field_cycle").set_value(cycle)
    click(at)
    assert any("crop cycle changed" in e.value for e in at.error)
    assert brain._farms.export(parent["owner_id"]) == before
