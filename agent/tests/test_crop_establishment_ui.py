"""Planting amendments and transplant batches through the real model-free crop page."""

from datetime import date

from agent.tests.test_crop_ui import planting
from agronaut_agent.tests.test_field_workflow import inputs


def fill_correction(at, brain, cycle, *, crop="pepper", day=date(2026, 7, 2)):
    revision = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["planting"]["revision_id"]
    prefix = f"crop_planting_correction_{cycle}_{revision or 'initial'}_"
    at.text_input(key=prefix+"crop").set_value(crop)
    at.date_input(key=prefix+"planted_on").set_value(day)
    at.text_input(key=prefix+"source").set_value("Synthetic corrected notebook")
    at.text_input(key=prefix+"reason").set_value("Transcription error")
    return revision


def test_planting_correction_updates_current_cycle_and_flags_preserved_plan(tmp_path):
    at, brain, unit, cycle = planting(tmp_path)
    plan = brain.field_workflow("web", "operator", "save", dict(task="root_zone_budget", inputs=inputs(),
                                cycle_id=cycle, request_id="plan"))
    fill_correction(at, brain, cycle, day=date(2026, 7, 7))
    next(b for b in at.button if b.label == "Save planting correction").click().run(timeout=15)
    assert not at.exception and not at.error
    journal = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))
    assert journal["cycle"]["crop"] == "pepper" and journal["cycle"]["planted_on"] == "2026-07-07"
    assert at.selectbox(key="crop_cycle").value == cycle
    assert brain.list_cycles("web", "operator", unit["id"])[0]["crop"] == "pepper"
    assert any("differ from the current planting" in w.value for w in at.warning)
    assert brain.field_workflow("web", "operator", "get", dict(calculation_id=plan["id"])) == plan
    assert at.date_input(key=f"crop_transplant_{cycle}_new_day").min == date(2026, 7, 7)
    assert journal["records"][0]["superseded"]


def test_stale_planting_correction_cannot_overwrite_another_client(tmp_path):
    at, brain, _, cycle = planting(tmp_path)
    revision = fill_correction(at, brain, cycle)
    brain.crop_records("web", "operator", "correct_planting", dict(cycle_id=cycle, crop="tomato", planted_on="2026-07-03",
        source="Another synthetic notebook", reason="Other client correction", request_id="other", expected_revision=revision))
    next(b for b in at.button if b.label == "Save planting correction").click().run(timeout=15)
    assert not at.exception and any("planting changed" in e.value for e in at.error)
    assert brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["cycle"]["planted_on"] == "2026-07-03"


def test_correcting_a_second_cycle_keeps_that_cycle_selected(tmp_path):
    at, brain, _, first = planting(tmp_path)
    second = brain.crop_records("web", "operator", "create", dict(crop="lettuce", planted_on="2026-07-03",
        source="Synthetic second planting", request_id="second"))["cycle"]["id"]
    at.run(timeout=15)
    at.selectbox(key="crop_cycle").set_value(second).run(timeout=15)
    fill_correction(at, brain, second)
    next(b for b in at.button if b.label == "Save planting correction").click().run(timeout=15)
    assert not at.exception and not at.error
    assert at.selectbox(key="crop_cycle").value == second
    assert brain.crop_records("web", "operator", "get", dict(cycle_id=first))["cycle"]["crop"] == "tomato"


def test_transplant_correction_void_restore_and_planting_date_conflict(tmp_path):
    at, brain, _, cycle = planting(tmp_path)
    prefix = f"crop_transplant_{cycle}_new_"
    at.text_input(key=prefix+"entry").set_value("transfer-1")
    at.date_input(key=prefix+"day").set_value(date(2026, 7, 6))
    at.text_input(key=prefix+"location").set_value("Synthetic row 1")
    at.text_input(key=prefix+"origin").set_value("Synthetic nursery tray A")
    at.selectbox(key=prefix+"provenance").set_value("reported")
    at.text_input(key=prefix+"source").set_value("Synthetic log")
    assert at.number_input(key=prefix+"plant_count").value is None
    next(b for b in at.button if b.label == "Save transplant").click().run(timeout=15)
    assert not at.exception and not at.error
    first = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["records"][-1]
    assert first["data"]["plant_count"] is None
    fill_correction(at, brain, cycle, crop="tomato", day=date(2026, 7, 7))
    next(b for b in at.button if b.label == "Save planting correction").click().run(timeout=15)
    assert not at.exception and any("current transplant" in e.value for e in at.error)
    at.selectbox(key=f"crop_transplant_correction_{cycle}").set_value(first["id"]).run(timeout=15)
    prefix = f"crop_transplant_{cycle}_{first['id']}_"
    assert at.text_input(key=prefix+"entry").disabled
    at.number_input(key=prefix+"plant_count").set_value(120)
    at.text_input(key=prefix+"source").set_value("Synthetic count correction")
    next(b for b in at.button if b.label == "Save transplant").click().run(timeout=15)
    assert not at.exception and not at.error
    at.text_input(key=f"crop_transplant_void_{cycle}_reason").set_value("Wrong tray")
    at.button(key=f"crop_transplant_void_{cycle}_submit").click().run(timeout=15)
    assert not at.exception and not at.error
    void = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["records"][-1]
    assert void["voided"]
    at.selectbox(key=f"crop_transplant_correction_{cycle}").set_value(void["id"]).run(timeout=15)
    prefix = f"crop_transplant_{cycle}_{void['id']}_"
    at.text_input(key=prefix+"source").set_value("Synthetic restoration")
    next(b for b in at.button if b.label == "Save transplant").click().run(timeout=15)
    assert not at.exception and not at.error
    review = brain.crop_records("web", "operator", "season_review", dict(cycle_id=cycle))
    assert review["current_record_counts"]["transplant"] == 1
    assert review["journal"]["records"][-1]["data"]["plant_count"] == 120


def test_corrected_crop_clears_unsaved_water_coefficients_and_nutrient_targets(tmp_path):
    from streamlit.testing.v1 import AppTest

    from agent.tests.test_field_ui import _SCRIPT, setup

    brain, unit, _ = setup(tmp_path)
    planted = brain.crop_records("web", "operator", "create", dict(crop="tomato", planted_on="2026-07-01",
        source="Synthetic planting", request_id="plant"))
    cycle = planted["cycle"]["id"]
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key="field_cycle").set_value(cycle).run(timeout=15)
    prefix = f"field_nutrient_{unit['id']}_{cycle}_"
    at.number_input(key="field_intake_kc").set_value(0.8)
    at.number_input(key="field_intake_area").set_value(5000.0)
    at.number_input(key=prefix+"n_target").set_value(60.0)
    at.text_input(key=prefix+"target_source").set_value("Synthetic old crop target")
    at.run(timeout=15)
    brain.crop_records("web", "operator", "correct_planting", dict(cycle_id=cycle, crop="pepper", planted_on="2026-07-02",
        source="Synthetic amended notebook", reason="Transcription error", request_id="correct",
        expected_revision=planted["record"]["id"]))
    at.run(timeout=15)
    assert not at.exception and not at.error
    assert at.text_input(key="field_intake_crop").value == "pepper"
    assert at.text_input(key=prefix+"crop").value == "pepper"
    assert at.number_input(key="field_intake_kc").value is None
    assert at.number_input(key="field_intake_area").value is None
    assert at.number_input(key=prefix+"n_target").value is None
    assert at.text_input(key=prefix+"target_source").value == ""
