"""Real catch-grid entry, missing readings and unit switching without a model."""

from streamlit.testing.v1 import AppTest

from agent.tests.test_field_ui import _SCRIPT, setup


def filled(tmp_path):
    brain, unit, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    for key, value in dict(zone="Zone A", start="2026-07-07T09:00:00Z", conditions="Synthetic rain-free equal exposure",
                           layout_source="Synthetic equal-area square grid", source="Synthetic calibrated depths").items():
        at.text_input(key="field_catch_"+key).set_value(value)
    at.selectbox(key="field_catch_kind").set_value("observed")
    for key, value in dict(duration=1800, rows=2, columns=2, dx=2, dy=2, wind=1).items():
        at.number_input(key="field_catch_"+key).set_value(value)
    for key in ("rain_free", "exposure", "equal"):
        at.checkbox(key="field_catch_"+key).set_value(True)
    at.text_area(key="field_catch_readings").set_value("1,2\n3,4")
    return at, brain, unit


def submit(at):
    next(b for b in at.button if b.label == "Evaluate and save catch test").click().run(timeout=15)
    assert not at.exception


def test_form_saves_evidence_and_missing_depth_never_becomes_zero(tmp_path):
    at, brain, _ = filled(tmp_path)
    submit(at)
    assert not at.error
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert saved["task"] == "sprinkler_catch_test"
    assert saved["result"]["result"]["statistics"]["low_quarter_du_pct"] == 40
    assert any("mean catch rate mm h: 5" in item.value for item in at.text)
    assert brain._farms.export(saved["owner_id"])["water_days"] == []
    submit(at)
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 2  # initial budget plus one idempotent test
    at.text_area(key="field_catch_readings").set_value("?,2\n3,4")
    submit(at)
    assert not at.error
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert saved["result"]["result"]["statistics"]["mean_depth_mm"] is None
    assert any("Statistics are withheld" in item.value for item in at.text)
    assert any(b.label == "Download calculation and evidence" for b in at.get("download_button"))


def test_bad_grid_and_unconfirmed_conditions_cannot_save_and_unit_switch_clears(tmp_path):
    at, brain, _ = filled(tmp_path)
    at.text_area(key="field_catch_readings").set_value("1,2\n3")
    submit(at)
    assert at.error
    at.text_area(key="field_catch_readings").set_value("1,2\n3,4")
    at.checkbox(key="field_catch_rain_free").set_value(False)
    submit(at)
    assert any("rain_free" in e.value for e in at.error)
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 1
    brain.create_unit("web", "operator", dict(farm=dict(name="Other", timezone="UTC"), unit=dict(name="Other field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    assert not at.exception
    assert at.text_area(key="field_catch_readings").value == ""
    assert at.number_input(key="field_catch_duration").value is None
