"""The native form saves an explicit scenario and exposes withheld predictions."""

from streamlit.testing.v1 import AppTest

from agent.tests.test_field_ui import _SCRIPT, setup
from agronaut_agent.tests.test_surface_advance_workflow import inputs

P = "field_advance_"


def fill(at, data):
    at.selectbox(key=P+"method").set_value(data["geometry"]["method"]).run(timeout=15)
    at.selectbox(key=P+"basis").set_value(data["infiltration"]["basis"])
    for name in ("geometry", "hydraulics", "infiltration", "limits", "conditions"):
        for key, value in data[name].items():
            if key in ("method", "basis", "min_opportunity_min"):
                continue
            widget = P+name+"_"+key
            if key == "source":
                at.text_input(key=widget).set_value(value)
            elif isinstance(value, bool):
                at.checkbox(key=widget).set_value(value)
            elif name != "hydraulics" or key not in ("section_p1", "section_p2") or data["geometry"]["method"] == "furrow":
                at.number_input(key=widget).set_value(float(value))


def test_native_form_saves_and_renders_advance_without_recording_water(tmp_path):
    brain, _, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    assert at.number_input(key=P+"hydraulics_inflow_lps").value is None
    assert not at.checkbox(key=P+"conditions_constant_inflow").value
    fill(at, inputs())
    at.button(key=P+"save").click().run(timeout=15)
    assert not at.exception and not at.error
    assert any("65.012 min" in item.value and "not a cutoff time" in item.value for item in at.text)
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert saved["task"] == "surface_advance" and saved["result"]["result"]["event_kind"] == "scenario"
    assert brain.field_workflow("web", "operator", "list", {})["water_days"] == []


def test_unconfirmed_conditions_do_not_save_and_constraints_remain_visible(tmp_path):
    brain, _, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    data = inputs()
    data["conditions"]["constant_inflow"] = False
    fill(at, data)
    at.button(key=P+"save").click().run(timeout=15)
    assert not at.exception and any("constant_inflow" in item.value for item in at.error)
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 1
    at.checkbox(key=P+"conditions_constant_inflow").set_value(True)
    at.number_input(key=P+"limits_max_advance_min").set_value(10.0)
    at.button(key=P+"save").click().run(timeout=15)
    assert not at.exception and not at.error
    assert any("prediction withheld" in item.value and "time_envelope" in item.value for item in at.text)


def test_unit_change_clears_the_hydraulic_evidence(tmp_path):
    brain, _, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    fill(at, inputs())
    at.run(timeout=15)
    brain.create_unit("web", "operator", {
        "farm": {"name": "Other site", "timezone": "Asia/Taipei"},
        "unit": {"name": "Second field", "production_system": "soil", "medium": "soil",
                 "environment": "open_field", "water_strategy": "supplemental", "irrigation_method": "surface"}})
    at.run(timeout=15)
    assert not at.exception
    assert at.selectbox(key=P+"method").value is None
    assert at.number_input(key=P+"hydraulics_inflow_lps").value is None
    assert at.text_input(key=P+"hydraulics_source").value == ""


def test_border_form_uses_explicit_wide_rectangle_geometry(tmp_path):
    brain, _, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    data = inputs()
    data["geometry"].update(method="border", width_m=10)
    data["hydraulics"].update(inflow_lps=25, section_p1=1, section_p2=5/3)
    data["infiltration"].update(basis="depth_mm", k=3.24, steady_rate=.174)
    data["limits"].update(max_inlet_flow_area_m2=2, max_inlet_velocity_m_s=1)
    fill(at, data)
    at.button(key=P+"save").click().run(timeout=15)
    assert not at.exception and not at.error
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert saved["inputs"]["hydraulics"]["section_p2"] == 5/3
    assert any("depth/width" in item.value for item in at.text)
