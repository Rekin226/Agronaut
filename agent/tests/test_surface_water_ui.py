"""Native actual-water intake and explicit replay never substitute root-zone logs."""

from datetime import date

from streamlit.testing.v1 import AppTest

from agent.tests.test_field_ui import _SCRIPT, setup
from agronaut_agent.tests.test_surface_water_records import inputs, water

P = "field_surface_water_"
D = P+"draft_"


def filled(tmp_path):
    brain, unit, old = setup(tmp_path)
    plan = brain.field_workflow("web", "operator", "save", dict(task="coupled_soil_water", inputs=inputs(), request_id="coupled"))
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key="field_plan").set_value(plan["id"]).run(timeout=15)
    fill(at)
    return at, brain, unit, old, plan


def fill(at, irrigation="3"):
    at.date_input(key=D+"day").set_value(date(2026, 7, 6))
    at.text_area(key=D+"rows").set_value(f"plot,5,1,{irrigation},Synthetic patch source")
    at.text_input(key=D+"source").set_value("Synthetic daily log")
    at.text_input(key=D+"coverage").set_value("Synthetic whole patch and day coverage")
    at.selectbox(key=D+"provenance").set_value("measured")
    at.selectbox(key=D+"quality").set_value("unverified")


def click(at, label):
    next(b for b in at.button if b.label == label).click().run(timeout=15)
    assert not at.exception


def review(brain, plan):
    return brain.field_workflow("web", "operator", "review_surface_water", dict(calculation_id=plan["id"]))


def test_native_record_retry_replay_and_export_preserve_parent(tmp_path):
    at, brain, _, _, plan = filled(tmp_path)
    click(at, "Save actual surface-water record")
    assert not at.error
    click(at, "Save actual surface-water record")
    assert not at.error
    records = review(brain, plan)["records"]
    assert len(records) == 1 and at.selectbox(key="field_plan").value == plan["id"]
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 2
    assert any(b.label == "Download surface-water records and review" for b in at.get("download_button"))
    at.multiselect(key=P+"records").set_value([records[0]["id"]])
    click(at, "Replay actual surface water as a new calculation")
    assert not at.error
    child = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert child["parent_id"] == plan["id"]
    assert child["result"]["result"]["totals"]["irrigation_at_surface_l"]["exact"] == "300/1"
    assert at.text_area(key=D+"rows").value == ""
    assert brain.field_workflow("web", "operator", "list", {})["water_days"] == []
    assert brain.field_workflow("web", "operator", "get", dict(calculation_id=plan["id"])) == plan


def test_correction_after_selection_rejects_stale_replay(tmp_path):
    at, brain, _, _, plan = filled(tmp_path)
    click(at, "Save actual surface-water record")
    original = review(brain, plan)["records"][0]
    at.multiselect(key=P+"records").set_value([original["id"]])
    corrected = brain.field_workflow("web", "operator", "record_surface_day", dict(calculation_id=plan["id"], data=water(quality="checked"),
        request_id="correction", supersedes_id=original["id"], reason="Synthetic review"))
    click(at, "Replay actual surface water as a new calculation")
    assert any("superseded" in e.value for e in at.error)
    assert at.selectbox(key="field_plan").value == plan["id"]
    at.multiselect(key=P+"records").set_value([corrected["id"]])
    click(at, "Replay actual surface water as a new calculation")
    assert not at.error


def test_native_correction_has_reason_and_latest_revision_guard(tmp_path):
    at, brain, _, _, plan = filled(tmp_path)
    click(at, "Save actual surface-water record")
    original = review(brain, plan)["records"][0]
    at.selectbox(key=P+"correction").set_value(original["id"]).run(timeout=15)
    fill(at, "8")
    click(at, "Save actual surface-water record")
    assert any("reason" in e.value for e in at.error)
    at.text_input(key=D+"reason").set_value("Corrected synthetic meter reading")
    click(at, "Save actual surface-water record")
    assert not at.error
    records = review(brain, plan)["records"]
    assert len(records) == 2 and records[0]["superseded"]
    assert records[1]["data"]["patches"][0]["irrigation_at_surface_mm"] == "8"


def test_missing_amount_and_switching_scope_keep_drafts_separate(tmp_path):
    at, brain, _, old, plan = filled(tmp_path)
    at.text_area(key=D+"rows").set_value("plot,5,1,,Synthetic")
    click(at, "Save actual surface-water record")
    assert at.error and review(brain, plan)["records"] == []
    at.selectbox(key="field_plan").set_value(old["id"]).run(timeout=15)
    at.selectbox(key="field_plan").set_value(plan["id"]).run(timeout=15)
    assert at.text_area(key=D+"rows").value == ""
    fill(at)
    cycle = brain.crop_records("web", "operator", "create", dict(crop="tomato", planted_on="2026-07-01", source="Synthetic", request_id="cycle"))["cycle"]["id"]
    at.run(timeout=15)
    at.selectbox(key="field_cycle").set_value(cycle).run(timeout=15)
    at.selectbox(key="field_cycle").set_value(None).run(timeout=15)
    assert at.text_area(key=D+"rows").value == ""


def test_stale_surface_form_cannot_save_after_cycle_selection_changes(tmp_path):
    at, brain, _, _, plan = filled(tmp_path)
    cycle = brain.crop_records("web", "operator", "create", dict(crop="tomato", planted_on="2026-07-01", source="Synthetic", request_id="new-cycle"))["cycle"]["id"]
    at.run(timeout=15)
    before = brain._farms.export(plan["owner_id"])
    at.selectbox(key="field_cycle").set_value(cycle)
    click(at, "Save actual surface-water record")
    assert any("crop cycle changed" in e.value for e in at.error)
    assert brain._farms.export(plan["owner_id"]) == before
