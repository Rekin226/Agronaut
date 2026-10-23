"""Native layer rows, missing readings and account/unit isolation."""

import csv
from io import StringIO

from streamlit.testing.v1 import AppTest

from agent.tests.test_field_ui import _SCRIPT, setup
from agro_model.tests.test_soil_profile import example


def filled(tmp_path):
    brain, _, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    data = example()
    p = data["profile"]
    for key, value in dict(name=p["name"], observed=p["observed_at"], root=p["root_depth_m"], source=p["source"]).items():
        at.text_input(key="field_profile_"+key).set_value(value)
    at.text_area(key="field_profile_representation").set_value(p["representation_note"])
    at.selectbox(key="field_profile_kind").set_value("observed")
    rows = StringIO()
    writer = csv.writer(rows)
    for layer in data["layers"]:
        writer.writerow([layer[key] for key in ("id", "top_m", "bottom_m", "field_capacity", "wilting_point", "water_content",
                                               "soil_source", "water_source", "provenance", "quality")])
    at.text_area(key="field_profile_layers").set_value(rows.getvalue())
    return at, brain, rows.getvalue()


def submit(at):
    next(b for b in at.button if b.label == "Assess and save soil profile").click().run(timeout=15)
    assert not at.exception


def test_native_assessment_save_retry_and_missing_layer_report(tmp_path):
    at, brain, rows = filled(tmp_path)
    submit(at)
    assert not at.error
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert saved["result"]["result"]["root_zone"]["bounded_depletion_mm"]["exact"] == "40/1"
    submit(at)
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 2
    at.text_area(key="field_profile_layers").set_value(rows.replace(",0.35,", ",?,"))
    submit(at)
    assert not at.error
    assert any("unknown / withheld" in item.value for item in at.text)
    assert any(item.label == "Download calculation and evidence" for item in at.get("download_button"))
    export = brain._farms.export(saved["owner_id"])
    assert export["water_days"] == [] and export["observations"] == []


def test_invalid_gap_or_csv_does_not_save_and_unit_switch_clears_draft(tmp_path):
    at, brain, rows = filled(tmp_path)
    at.text_area(key="field_profile_layers").set_value(rows.replace("upper,0,0.2", "upper,0,0.3"))
    submit(at)
    assert any("contiguous" in error.value for error in at.error)
    at.text_area(key="field_profile_layers").set_value("too,few,columns")
    submit(at)
    assert at.error
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 1
    brain.create_unit("web", "operator", dict(farm=dict(name="Other", timezone="UTC"), unit=dict(name="Other field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    assert not at.exception
    assert at.text_area(key="field_profile_layers").value == ""
    assert at.text_input(key="field_profile_root").value == ""
