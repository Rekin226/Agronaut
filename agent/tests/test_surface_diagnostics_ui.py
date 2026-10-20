"""Read-only surface comparisons through native forms, without an LLM."""

from streamlit.testing.v1 import AppTest

from agent.tests.test_field_ui import _SCRIPT, setup
from agronaut_agent.tests.test_surface_advance_workflow import inputs
from agronaut_agent.tests.test_surface_diagnostics import event_inputs

P = "field_advance_compare_"


def prepare(tmp_path, missing_half=False):
    brain, _, _ = setup(tmp_path)
    pred = brain.field_workflow("web", "operator", "save", dict(task="surface_advance", inputs=inputs(), request_id="predict"))
    data = event_inputs()
    if missing_half:
        data["stations"].pop(1)
    event = brain.field_workflow("web", "operator", "save", dict(task="surface_evaluation", inputs=data, request_id="event"))
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key="field_plan").set_value(pred["id"]).run(timeout=15)
    at.multiselect(key=P+"events").set_value([event["id"]]).run(timeout=15)
    return brain, pred, event, at


def fill(at, event, independence="held_out"):
    at.selectbox(key=P+"kind").set_value("synthetic")
    at.text_area(key=P+"note").set_value("Synthetic workflow test selection")
    at.text_input(key=P+event["id"]+"_representation").set_value("Synthetic identical strip and conditions")
    at.selectbox(key=P+event["id"]+"_independence").set_value(independence)
    at.button(key=P+"run").click().run(timeout=15)


def test_comparison_keeps_missing_readings_and_does_not_change_records(tmp_path):
    brain, pred, event, at = prepare(tmp_path, missing_half=True)
    before = brain.field_workflow("web", "operator", "list", {})
    assert at.selectbox(key=P+"kind").value is None
    assert at.selectbox(key=P+event["id"]+"_independence").value is None
    fill(at, event)
    assert not at.exception and not at.error
    report = at.session_state[P+"report"]["result"]
    assert report["calculation"] == pred
    assert report["metrics_by_anchor"]["half_length"]["rmse_min"] is None
    assert report["metrics_by_anchor"]["full_length"]["count"] == 1
    assert any("half_length: no eligible" in text.value for text in at.text)
    assert brain.field_workflow("web", "operator", "list", {}) == before


def test_calibration_events_are_visible_but_unscored_and_changed_drafts_hide_reports(tmp_path):
    _, _, event, at = prepare(tmp_path)
    fill(at, event, "used_for_calibration")
    assert not at.exception and not at.error
    report = at.session_state[P+"report"]["result"]
    assert report["included_event_count"] == 0
    assert report["comparisons"][0]["anchors"][1]["error_min"] is not None
    at.text_area(key=P+"note").set_value("Changed selection rationale").run(timeout=15)
    assert not any("Surface advance comparison — DIAGNOSTIC" in text.value for text in at.text)
    assert any("Run the comparison again" in text.value for text in at.caption)
    at.selectbox(key="field_plan").set_value(event["id"]).run(timeout=15)
    assert P+"report" not in at.session_state


def test_missing_declarations_fail_without_leaving_an_old_report(tmp_path):
    _, _, event, at = prepare(tmp_path)
    fill(at, event)
    at.text_area(key=P+"note").set_value("")
    at.button(key=P+"run").click().run(timeout=15)
    assert not at.exception and at.error
    assert P+"report" not in at.session_state


def test_stale_callback_rejects_changed_calculation_or_cycle():
    script = '''
import streamlit as st
from agent.surface_diagnostics_ui import _compare
st.session_state.field_plan = 'new-plan'
st.session_state.field_cycle = None
def must_not_call(*args):
    raise AssertionError('stale callback reached backend')
_compare(must_not_call, 'old-plan', None, [])
'''
    at = AppTest.from_string(script).run(timeout=15)
    assert not at.exception
    assert "changed" in at.session_state[P+"error"]
