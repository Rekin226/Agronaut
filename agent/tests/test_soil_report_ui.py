"""Native laboratory transcriptions, corrections and seasonal visibility."""

from datetime import date

from streamlit.testing.v1 import AppTest

from agent.tests.test_crop_ui import planting
from agent.tests.test_field_ui import _SCRIPT, setup
from agro_model.farm import ProductionCycle

ROWS = 'p,Phosphorus,<,0.010,mg/kg,Olsen\nph,pH,=,6.20,pH,"water, 1:1"\nn,Nitrogen,NR,?,?,?\ntexture,Texture class,TEXT,Sandy loam,?,Laboratory description'


def fill(at):
    p = "field_soilreport_new_"
    for key, value in dict(entry="soil-A", lab_name="Synthetic lab", lab_report_id="R1", sample_id="A1", location="North field",
        sampling_source="Synthetic composite procedure", report_source="Synthetic report page 1").items():
        at.text_input(key=p+key).set_value(value)
    at.date_input(key=p+"sampled_on").set_value(date(2026,6,20))
    at.date_input(key=p+"reported_on").set_value(date(2026,6,25))
    at.number_input(key=p+"depth_top_cm").set_value(0)
    at.number_input(key=p+"depth_bottom_cm").set_value(15)
    at.text_area(key=p+"results").set_value(ROWS)


def click(at, label):
    next(b for b in at.button if b.label == label).click().run(timeout=15)
    assert not at.exception


def test_save_correction_void_restore_preserve_qualifiers_and_history(tmp_path):
    brain, _, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    fill(at)
    click(at, "Save soil report")
    assert not at.error
    first = at.selectbox(key="field_soilreport_choice").value
    section = brain.field_workflow("web", "operator", "list_soil_tests", {})
    assert section["current"][0]["data"]["results"][0]["value"] == "0.010"
    assert section["current"][0]["data"]["results"][1]["method"] == "water, 1:1"
    assert section["current"][0]["data"]["results"][2]["value"] is None
    click(at, "Save soil report")
    assert len(brain.field_workflow("web", "operator", "list_soil_tests", {})["history"]) == 1
    p = "field_soilreport_"+first+"_"
    at.text_area(key=p+"results").set_value(ROWS.replace("6.20", "6.30"))
    at.text_input(key=p+"reason").set_value("Correct pH transcription")
    click(at, "Save soil report")
    assert not at.error
    second = at.selectbox(key="field_soilreport_choice").value
    at.selectbox(key="field_soilreport_history").set_value(first).run(timeout=15)
    table = next(f.value for f in at.dataframe if "analyte" in f.value.columns)
    assert table.loc[table["analyte"] == "pH", "value"].iloc[0] == "6.20"
    at.text_input(key="field_soilreport_"+second+"_reason").set_value("Wrong report association")
    click(at, "Void soil report")
    assert not at.error
    void = at.selectbox(key="field_soilreport_choice").value
    assert brain.field_workflow("web", "operator", "list_soil_tests", {})["current"] == []
    at.text_input(key="field_soilreport_"+void+"_reason").set_value("Association confirmed")
    click(at, "Save soil report")
    assert not at.error
    section = brain.field_workflow("web", "operator", "list_soil_tests", {})
    assert len(section["history"]) == 4 and len(section["current"]) == 1
    assert any(b.label == "Download soil report history" for b in at.get("download_button"))


def test_invalid_transcription_fails_and_unit_switch_clears_report_draft(tmp_path):
    brain, _, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    fill(at)
    at.text_area(key="field_soilreport_new_results").set_value("p,Phosphorus,ND,0,mg/kg,Olsen")
    click(at, "Save soil report")
    assert at.error
    assert brain.field_workflow("web", "operator", "list_soil_tests", {})["history"] == []
    brain.create_unit("web", "operator", dict(farm=dict(name="Other", timezone="UTC"), unit=dict(name="Other field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    assert not at.exception
    assert at.text_input(key="field_soilreport_new_entry").value == ""
    assert at.text_area(key="field_soilreport_new_results").value == ""


def test_assigned_preplant_report_appears_in_season_and_cross_season_views(tmp_path):
    crop, brain, unit, cycle = planting(tmp_path)
    field = AppTest.from_string(_SCRIPT).run(timeout=15)
    field.selectbox(key="field_cycle").set_value(cycle).run(timeout=15)
    fill(field)
    click(field, "Save soil report")
    assert not field.error
    crop.run(timeout=15)
    assert not crop.exception
    table = next(f.value for f in crop.dataframe if "analyte" in f.value.columns)
    assert list(table["qualifier"]) == ["<", "=", "NR", "TEXT"]
    assert list(table["value"]) == ["0.010", "6.20", "Unknown / not numeric", "Sandy loam"]
    owner = brain._conv.get_or_create_user("web", "operator")
    brain._farms.create_cycle(owner, ProductionCycle("empty-soil-cycle", unit["id"], "tomato", "2026-07-01"))
    crop.run(timeout=15)
    crop.multiselect(key="crop_compare_cycles").set_value([cycle, "empty-soil-cycle"]).run(timeout=15)
    assert not crop.exception
    periods = next(f.value for f in crop.dataframe if "current_soil_reports" in f.value.columns)
    assert list(periods["current_soil_reports"]) == [1, 0]
    assert brain._farms.export(owner)["water_days"] == []
