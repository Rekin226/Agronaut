"""Owned coupled comparisons through real forms with review and scope guards."""

from datetime import date

from streamlit.testing.v1 import AppTest

from agent.tests.test_field_ui import _SCRIPT, setup
from agent.tests.test_soil_state_ui import record
from agronaut_agent.observation_quality import ObservationReviews
from agronaut_agent.tests.test_coupled_water_workflow import example

PREFIX = "field_coupled_compare_"


def filled(tmp_path):
    brain, unit, old = setup(tmp_path)
    reading = record(brain)
    plan = brain.field_workflow("web", "operator", "save", dict(task="coupled_soil_water", inputs=example(), request_id="coupled"))
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key="field_plan").set_value(plan["id"]).run(timeout=15)
    at.multiselect(key=PREFIX+"readings").set_value([reading["id"]]).run(timeout=15)
    at.selectbox(key=PREFIX+"kind").set_value("synthetic")
    at.text_area(key=PREFIX+"note").set_value("Synthetic comparison, no field evidence")
    p = PREFIX+reading["id"]+"_"
    at.date_input(key=p+"day").set_value(date(2026, 7, 6))
    at.selectbox(key=p+"patch").set_value("plot")
    at.text_input(key=p+"top").set_value("0")
    at.text_input(key=p+"bottom").set_value("1")
    at.text_input(key=p+"representation").set_value("Synthetic patch and full profile mean")
    at.number_input(key=p+"adjustment").set_value(0.)
    at.text_input(key=p+"source").set_value("Synthetic zero timing adjustment")
    at.selectbox(key=p+"independence").set_value("held_out")
    return at, brain, unit, old, plan, reading


def submit(at):
    next(b for b in at.button if b.label == "Compare coupled predictions without changing state").click().run(timeout=15)
    assert not at.exception


def test_native_comparison_and_download_leave_all_farm_records_unchanged(tmp_path):
    at, brain, _, _, plan, _ = filled(tmp_path)
    before = brain._farms.export(plan["owner_id"])
    submit(at)
    assert not at.error
    assert brain._farms.export(plan["owner_id"]) == before
    report = at.session_state[PREFIX+"report"]["result"]
    assert report["metrics"]["count"] == 1 and report["metrics"]["unit"] == "m3/m3"
    assert any("coupled soil prediction comparison — DIAGNOSTIC" in t.value for t in at.text)
    assert any(b.label == "Download coupled soil comparison and evidence" for b in at.get("download_button"))


def test_review_change_between_display_and_click_rejects_stale_submission(tmp_path):
    at, brain, unit, _, plan, reading = filled(tmp_path)
    ObservationReviews(brain._farms.db).review(plan["owner_id"], unit["id"], reading["id"], "checked", "Reviewed",
        "Synthetic review", request_id="review", expected_revision=None)
    before = brain._farms.export(plan["owner_id"])
    submit(at)
    assert any("review changed" in e.value for e in at.error)
    assert brain._farms.export(plan["owner_id"]) == before
    submit(at)
    assert not at.error
    assert at.session_state[PREFIX+"report"]["result"]["groups"][0]["quality"] == "checked"


def test_changed_reviews_or_declarations_hide_previous_diagnostic(tmp_path):
    at, brain, unit, _, plan, reading = filled(tmp_path)
    submit(at)
    reviews = ObservationReviews(brain._farms.db)
    checked = reviews.review(plan["owner_id"], unit["id"], reading["id"], "checked", "Reviewed",
        "Synthetic", request_id="checked", expected_revision=None)
    at.run(timeout=15)
    assert not any("coupled soil prediction comparison — DIAGNOSTIC" in t.value for t in at.text)
    assert any("Run the coupled comparison again" in c.value for c in at.caption)
    submit(at)
    at.selectbox(key=PREFIX+reading["id"]+"_independence").set_value("used_for_calibration").run(timeout=15)
    assert not at.exception
    assert not any("coupled soil prediction comparison — DIAGNOSTIC" in t.value for t in at.text)
    submit(at)
    assert at.session_state[PREFIX+"report"]["result"]["metrics"]["count"] == 0
    reviews.review(plan["owner_id"], unit["id"], reading["id"], "rejected", "Sensor fault", "Synthetic",
        request_id="rejected", expected_revision=checked["id"])
    at.run(timeout=15)
    assert not any("coupled soil prediction comparison — DIAGNOSTIC" in t.value for t in at.text)


def test_missing_interval_fails_and_switching_calculations_or_units_clears_draft(tmp_path):
    at, brain, _, old, plan, reading = filled(tmp_path)
    at.text_input(key=PREFIX+reading["id"]+"_top").set_value("")
    submit(at)
    assert at.error
    at.selectbox(key="field_plan").set_value(old["id"]).run(timeout=15)
    at.selectbox(key="field_plan").set_value(plan["id"]).run(timeout=15)
    assert at.multiselect(key=PREFIX+"readings").value == []
    assert at.text_area(key=PREFIX+"note").value == ""
    brain.create_unit("web", "operator", dict(farm=dict(name="Other", timezone="UTC"), unit=dict(name="Other field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    assert not at.exception
    assert PREFIX+"report" not in at.session_state


def test_switching_through_a_cycle_without_calculations_does_not_restore_old_comparison(tmp_path):
    at, brain, _, _, _, _ = filled(tmp_path)
    submit(at)
    cycle = brain.crop_records("web", "operator", "create", dict(crop="tomato", planted_on="2026-07-01",
        source="Synthetic cycle", request_id="empty-cycle"))["cycle"]["id"]
    at.run(timeout=15)
    at.selectbox(key="field_cycle").set_value(cycle).run(timeout=15)
    at.selectbox(key="field_cycle").set_value(None).run(timeout=15)
    assert not at.exception
    assert at.multiselect(key=PREFIX+"readings").value == []
    assert at.text_area(key=PREFIX+"note").value == ""
    assert PREFIX+"report" not in at.session_state
