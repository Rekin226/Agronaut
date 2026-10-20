"""Diagnostic forms retain scope, read-only behavior and current review evidence."""

from datetime import date

from streamlit.testing.v1 import AppTest

from agent.tests.test_field_ui import _SCRIPT, setup
from agent.tests.test_soil_state_ui import record
from agronaut_agent.observation_quality import ObservationReviews


def filled(tmp_path):
    brain, unit, plan = setup(tmp_path)
    reading = record(brain)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    prefix = f"field_soil_compare_{plan['id']}_"
    at.multiselect(key=prefix+"readings").set_value([reading["id"]]).run(timeout=15)
    at.selectbox(key=prefix+"kind").set_value("synthetic")
    at.text_area(key=prefix+"note").set_value("Synthetic diagnostic sample; independence is not established")
    p = prefix+reading["id"]+"_"
    at.date_input(key=p+"day").set_value(date(2026, 7, 6))
    at.number_input(key=p+"depth").set_value(.8)
    at.text_input(key=p+"representation").set_value("Synthetic complete root-zone and field mean")
    at.number_input(key=p+"adjustment").set_value(2)
    at.text_input(key=p+"source").set_value("Synthetic timing adjustment")
    return at, brain, unit, plan, reading, prefix


def submit(at):
    next(button for button in at.button if button.label == "Compare without changing soil state").click().run(timeout=15)
    assert not at.exception


def test_comparison_report_and_download_do_not_change_farm_records(tmp_path):
    at, brain, _, plan, _, prefix = filled(tmp_path)
    before = brain._farms.export(plan["owner_id"])
    submit(at)
    assert not at.error
    assert brain._farms.export(plan["owner_id"]) == before
    assert any("DIAGNOSTIC" in block.value and "MAE" in block.value for block in at.text)
    assert any(button.label == "Download soil comparison and evidence" for button in at.get("download_button"))
    assert at.selectbox(key="field_plan").value == plan["id"]
    assert at.session_state[prefix+"report"]["result"]["metrics"]["count"] == 1
    at.text_area(key=prefix+"note").set_value("Changed coverage rationale").run(timeout=15)
    assert not at.exception
    assert not any("DIAGNOSTIC" in block.value for block in at.text)
    assert brain._farms.export(plan["owner_id"]) == before


def test_quality_review_changes_hide_stale_report_and_require_fresh_comparison(tmp_path):
    at, brain, unit, plan, reading, prefix = filled(tmp_path)
    submit(at)
    review = ObservationReviews(brain._farms.db).review(plan["owner_id"], unit["id"], reading["id"], "checked",
        "Reviewed", "Synthetic", request_id="review", expected_revision=None)
    at.run(timeout=15)
    assert not at.exception
    assert not any("DIAGNOSTIC" in block.value for block in at.text)
    assert any("Run the comparison again" in block.value for block in at.caption)
    before = brain._farms.export(plan["owner_id"])
    submit(at)
    assert not at.error and brain._farms.export(plan["owner_id"]) == before
    assert at.session_state[prefix+"report"]["result"]["groups"][0]["quality"] == "checked"
    ObservationReviews(brain._farms.db).review(plan["owner_id"], unit["id"], reading["id"], "rejected",
        "Sensor issue", "Synthetic", request_id="reject", expected_revision=review["id"])
    at.run(timeout=15)
    assert not at.exception
    assert not any("DIAGNOSTIC" in block.value for block in at.text)


def test_submit_uses_the_quality_review_displayed_before_click(tmp_path):
    at, brain, unit, plan, reading, _ = filled(tmp_path)
    ObservationReviews(brain._farms.db).review(plan["owner_id"], unit["id"], reading["id"], "checked",
        "Reviewed", "Synthetic", request_id="changed", expected_revision=None)
    before = brain._farms.export(plan["owner_id"])
    submit(at)
    assert any("review changed" in error.value for error in at.error)
    assert not any("DIAGNOSTIC" in block.value for block in at.text)
    assert brain._farms.export(plan["owner_id"]) == before
    submit(at)
    assert not at.error


def test_missing_declarations_fail_and_unit_switch_clears_the_report(tmp_path):
    at, brain, _, plan, _, prefix = filled(tmp_path)
    at.text_area(key=prefix+"note").set_value("")
    before = brain._farms.export(plan["owner_id"])
    submit(at)
    assert at.error and brain._farms.export(plan["owner_id"]) == before
    at.text_area(key=prefix+"note").set_value("Synthetic selection rationale")
    submit(at)
    assert not at.error
    brain.create_unit("web", "operator", dict(farm=dict(name="Other", timezone="UTC"), unit=dict(name="Other field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    assert not at.exception
    assert not any("DIAGNOSTIC" in block.value for block in at.text)
