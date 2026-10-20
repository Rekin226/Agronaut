"""Native lateral scenarios preserve surveys, scope and unknown inputs."""

import csv
from io import StringIO

import pytest
from streamlit.testing.v1 import AppTest

from agent.drip_lateral_ui import COLUMNS, NUMBERS, P, parse_inlet_curve, parse_stations
from agent.tests.test_field_ui import _SCRIPT, setup
from agronaut_agent.tests.test_drip_lateral_workflow import inputs


def station_csv(data):
    stream = StringIO()
    writer = csv.DictWriter(stream, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(data["stations"])
    return stream.getvalue()


def fill(at, data):
    for record, fields in NUMBERS.items():
        for key in fields:
            if key in data[record]:
                at.number_input(key=P+record+"_"+key).set_value(float(data[record][key]))
        at.text_input(key=P+record+"_source").set_value(data[record]["source"])
    for key, value in data["conditions"].items():
        if key == "source":
            at.text_input(key=P+"conditions_source").set_value(value)
        else:
            at.checkbox(key=P+"conditions_"+key).set_value(value)
    at.text_area(key=P+"stations").set_value(station_csv(data))
    if data["schema_version"] == 2:
        curve = "flow_lph,pressure_head_m\n" + "\n".join(f"{r['flow_lph']},{r['pressure_head_m']}" for r in data["inlet"]["curve"])
        at.text_area(key=P+"inlet_curve").set_value(curve)
        at.text_input(key=P+"inlet_configuration").set_value(data["inlet"]["configuration"])


def test_native_form_saves_full_scenario_without_llm_or_water_log(tmp_path):
    brain, _, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    assert at.number_input(key=P+"emitter_exponent").value is None
    assert at.checkbox(key=P+"conditions_closed_tail").value is False
    data = inputs()
    data["stations"][0]["source"] = "Synthetic source, quoted comma"
    fill(at, data)
    at.button(key=P+"save").click().run(timeout=15)
    assert not at.exception and not at.error
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert saved["task"] == "drip_lateral"
    assert saved["inputs"]["stations"] == data["stations"]
    assert saved["result"]["result"]["prediction"] is not None
    assert any("Agronaut drip lateral" in block.value for block in at.text)
    assert brain.field_workflow("web", "operator", "list", {})["water_days"] == []


def test_curve_form_requires_pressure_reference_then_saves_operating_point(tmp_path):
    brain, _, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.radio(key=P+"mode").set_value("Pressure–flow curve").run(timeout=15)
    data = inputs("drip_lateral_curve.json")
    fill(at, data)
    at.button(key=P+"save").click().run(timeout=15)
    assert not at.exception
    assert any("Confirm" in e.value for e in at.error)
    at.checkbox(key=P+"inlet_reference").set_value(True)
    at.button(key=P+"save").click().run(timeout=15)
    assert not at.exception and not at.error
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert saved["inputs"]["schema_version"] == 2
    assert saved["inputs"]["inlet"] == data["inlet"]
    assert any("Operating inlet pressure" in block.value for block in at.text)
    assert brain.field_workflow("web", "operator", "list", {})["water_days"] == []
    at.radio(key=P+"mode").set_value("Fixed pressure").run(timeout=15)
    assert at.number_input(key=P+"inlet_running_head_m").value is None
    assert at.text_input(key=P+"inlet_source").value == ""
    assert not at.checkbox(key=P+"conditions_steady_inlet_head").value


def test_withheld_prediction_is_saved_and_displayed_as_a_scenario(tmp_path):
    brain, _, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    data = inputs()
    data["inlet"]["running_head_m"] = .01
    fill(at, data)
    at.button(key=P+"save").click().run(timeout=15)
    assert not at.exception and not at.error
    assert any("Prediction withheld" in block.value for block in at.text)
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert saved["result"]["result"]["feasible"] is None


def test_unit_change_clears_unsaved_curve_and_survey(tmp_path):
    brain, _, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    fill(at, inputs())
    at.run(timeout=15)
    brain.create_unit("web", "operator", {"farm": {"name": "Other", "timezone": "Asia/Taipei"},
        "unit": {"name": "Other field", "production_system": "soil", "medium": "soil", "environment": "open_field",
                 "water_strategy": "supplemental", "irrigation_method": "drip"}})
    at.run(timeout=15)
    assert not at.exception
    assert at.number_input(key=P+"emitter_reference_flow_lph").value is None
    assert at.text_area(key=P+"stations").value == ""
    assert not at.checkbox(key=P+"conditions_closed_tail").value


@pytest.mark.parametrize("content", ["", "identifier,distance_m\nA,1", ",".join(COLUMNS),
    ",".join(COLUMNS)+"\nA,1,,0,source", ",".join(COLUMNS)+"\nA,1,0,0,source,extra"])
def test_csv_does_not_fill_missing_or_shift_extra_fields(content):
    with pytest.raises(ValueError):
        parse_stations(content)


@pytest.mark.parametrize("content", ["", "flow_lph,pressure_head_m\n1,10", "flow_lph,pressure_head_m\n0,10\n1,",
                                   "flow_lph,pressure_head_m\n0,10\n1,9,extra"])
def test_curve_csv_requires_complete_explicit_points(content):
    with pytest.raises(ValueError):
        parse_inlet_curve(content)
