"""Planting through harvest correction/void in the real no-model web path."""

from datetime import date
from decimal import Decimal

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from agent.tests.test_field_ui import setup  # noqa: E402
from agro_model.farm import ProductionCycle  # noqa: E402

SCRIPT = """
from agent.tests.test_field_ui import _SHARED
from agent.crop_ui import render_crop_records
render_crop_records(_SHARED['brain'], 'operator')
"""


def planting(tmp_path):
    brain, unit, _ = setup(tmp_path)
    at = AppTest.from_string(SCRIPT).run(timeout=15)
    assert not at.exception
    at.text_input(key="crop_name").set_value("tomato")
    at.date_input(key="crop_planted").set_value(date(2026, 7, 1))
    at.text_input(key="crop_planting_source").set_value("Synthetic planting record")
    next(b for b in at.button if b.label == "Save planting").click().run(timeout=15)
    assert not at.exception and not at.error
    return at, brain, unit, at.selectbox(key="crop_cycle").value


def fill_harvest(at, cycle):
    prefix = f"crop_harvest_{cycle}_new_"
    at.text_input(key=prefix+"batch").set_value("morning-1")
    at.date_input(key=prefix+"day").set_value(date(2026, 7, 6))
    at.number_input(key=prefix+"quantity").set_value(2.5)
    at.selectbox(key=prefix+"unit").set_value("kg")
    at.selectbox(key=prefix+"basis").set_value("fresh")
    at.selectbox(key=prefix+"provenance").set_value("measured")
    at.text_input(key=prefix+"product").set_value("fruit")
    at.text_input(key=prefix+"grade").set_value("ungraded")
    at.text_input(key=prefix+"source").set_value("Synthetic weighing")


def test_planting_harvest_correction_and_void_without_a_model(tmp_path):
    at, brain, unit, cycle = planting(tmp_path)
    assert brain.list_cycles("web", "operator", unit["id"])[0]["id"] == cycle
    assert at.number_input(key=f"crop_harvest_{cycle}_new_quantity").value is None
    fill_harvest(at, cycle)
    next(b for b in at.button if b.label == "Save harvest batch").click().run(timeout=15)
    assert not at.exception and not at.error
    journal = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))
    original = journal["records"][-1]["id"]
    assert Decimal(journal["harvest_totals"][0]["quantity"]) == Decimal("2.5")
    at.selectbox(key=f"crop_correction_{cycle}").set_value(original).run(timeout=15)
    prefix = f"crop_harvest_{cycle}_{original}_"
    assert at.text_input(key=prefix+"batch").disabled
    at.number_input(key=prefix+"quantity").set_value(3.0)
    at.text_input(key=prefix+"source").set_value("Synthetic reweighing")
    next(b for b in at.button if b.label == "Save harvest batch").click().run(timeout=15)
    assert not at.exception and not at.error
    journal = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))
    assert Decimal(journal["harvest_totals"][0]["quantity"]) == Decimal("3")
    assert journal["records"][1]["superseded"]
    at.text_input(key=f"crop_void_reason_{cycle}").set_value("Entered against wrong planting")
    at.button(key="crop_void").click().run(timeout=15)
    assert not at.exception and not at.error
    journal = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))
    assert journal["harvest_totals"] == []
    assert journal["records"][-1]["voided"]


def test_unknown_quantity_never_becomes_zero(tmp_path):
    at, brain, _, cycle = planting(tmp_path)
    fill_harvest(at, cycle)
    at.number_input(key=f"crop_harvest_{cycle}_new_quantity").set_value(None)
    next(b for b in at.button if b.label == "Save harvest batch").click().run(timeout=15)
    assert not at.exception and at.error
    assert len(brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["records"]) == 1


def test_unit_switch_clears_crop_forms_and_stale_writes_are_rejected(tmp_path):
    at, brain, unit, cycle = planting(tmp_path)
    brain.create_unit("web", "operator", {"farm": {"name": "Other", "timezone": "UTC"},
        "unit": {"name": "Hydro", "production_system": "hydroponics", "medium": "water",
                 "environment": "greenhouse", "water_strategy": "fully_irrigated"}})
    with pytest.raises(ValueError, match="selected unit changed"):
        brain.crop_records("web", "operator", "get", dict(cycle_id=cycle), expected_unit_id=unit["id"])
    at.run(timeout=15)
    assert not at.exception
    assert at.text_input(key="crop_name").value == ""
    assert at.date_input(key="crop_planted").value is None
    assert not any(w.key == "crop_cycle" for w in at.selectbox)


def test_full_app_crop_records_route_is_available_without_model(tmp_path):
    setup(tmp_path)
    script = """
import streamlit as st
import app
from agent.tests.test_field_ui import _SHARED
st.session_state.agent = _SHARED['brain']
st.session_state.web_user = 'operator'
st.session_state.app_mode = 'Crop records'
app.main()
"""
    at = AppTest.from_string(script).run(timeout=20)
    assert not at.exception
    assert at.text_input(key="crop_name").value == ""


def test_existing_future_cycle_can_be_viewed_without_invalid_harvest_date_widget(tmp_path):
    brain, unit, before = setup(tmp_path)
    brain._farms.create_cycle(before["owner_id"], ProductionCycle("legacy-future", unit["id"], "tomato", "2099-01-01"))
    at = AppTest.from_string(SCRIPT).run(timeout=15)
    assert not at.exception
    assert any("future planting date" in item.value for item in at.info)
    assert not any(b.label == "Save harvest batch" for b in at.button)


@pytest.mark.parametrize("kind,label", [("stage", "growth stage"), ("scouting", "scouting")])
def test_observation_record_correction_void_and_restore_without_model(tmp_path, kind, label):
    at, brain, _, cycle = planting(tmp_path)
    prefix = f"crop_{kind}_{cycle}_new_"
    at.text_input(key=prefix+"entry").set_value("inspection-1")
    at.date_input(key=prefix+"day").set_value(date(2026, 7, 6))
    at.text_input(key=prefix+"location").set_value("Row 1")
    at.text_input(key=prefix+"source").set_value("Synthetic inspection")
    at.selectbox(key=prefix+"provenance").set_value("observed")
    if kind == "stage":
        at.text_input(key=prefix+"stage").set_value("flowering")
    else:
        assert at.number_input(key=prefix+"inspected_count").value is None
        assert at.number_input(key=prefix+"affected_count").value is None
        at.text_area(key=prefix+"symptoms").set_value("yellowing leaves")
    next(b for b in at.button if b.label == f"Save {label}").click().run(timeout=15)
    assert not at.exception and not at.error
    journal = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))
    original = journal["records"][-1]
    if kind == "scouting":
        assert original["data"]["inspected_count"] is None
    at.selectbox(key=f"crop_{kind}_correction_{cycle}").set_value(original["id"]).run(timeout=15)
    prefix = f"crop_{kind}_{cycle}_{original['id']}_"
    assert at.text_input(key=prefix+"entry").disabled
    at.text_input(key=prefix+"source").set_value("Corrected synthetic inspection")
    at.text_input(key=prefix+"location").set_value("Row 2")
    if kind == "scouting":
        at.number_input(key=prefix+"inspected_count").set_value(10)
        at.number_input(key=prefix+"affected_count").set_value(2)
    next(b for b in at.button if b.label == f"Save {label}").click().run(timeout=15)
    assert not at.exception and not at.error
    void_key = f"crop_{kind}_void_{cycle}"
    at.text_input(key=void_key+"_reason").set_value("Wrong planting")
    at.button(key=void_key+"_submit").click().run(timeout=15)
    assert not at.exception and not at.error
    journal = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))
    voided = journal["records"][-1]
    assert voided["voided"]
    assert journal["latest_stage_reports"] == []
    at.selectbox(key=f"crop_{kind}_correction_{cycle}").set_value(voided["id"]).run(timeout=15)
    prefix = f"crop_{kind}_{cycle}_{voided['id']}_"
    at.text_input(key=prefix+"source").set_value("Confirmed planting identity")
    next(b for b in at.button if b.label == f"Save {label}").click().run(timeout=15)
    assert not at.exception and not at.error
    journal = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))
    assert not journal["records"][-1]["voided"]
    assert journal["records"][-1]["data"]["location"] == "Row 2"
    assert len(journal["records"]) == 5


def test_scouting_rejects_affected_count_above_inspected(tmp_path):
    at, brain, _, cycle = planting(tmp_path)
    prefix = f"crop_scouting_{cycle}_new_"
    at.text_input(key=prefix+"entry").set_value("sample")
    at.date_input(key=prefix+"day").set_value(date(2026, 7, 6))
    at.text_input(key=prefix+"location").set_value("Row 1")
    at.text_input(key=prefix+"source").set_value("Synthetic fixture")
    at.selectbox(key=prefix+"provenance").set_value("observed")
    at.text_area(key=prefix+"symptoms").set_value("yellow leaves")
    at.number_input(key=prefix+"inspected_count").set_value(10)
    at.number_input(key=prefix+"affected_count").set_value(11)
    next(b for b in at.button if b.label == "Save scouting").click().run(timeout=15)
    assert not at.exception and any("cannot exceed" in e.value for e in at.error)
    assert len(brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["records"]) == 1


def test_close_correct_and_reopen_cycle_through_no_model_app(tmp_path):
    at, brain, _, cycle = planting(tmp_path)
    fill_harvest(at, cycle)
    next(b for b in at.button if b.label == "Save harvest batch").click().run(timeout=15)
    assert brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["lifecycle"]["status"] == "open"
    prefix = f"crop_close_{cycle}_initial_"
    assert at.date_input(key=prefix+"ended_on").value is None
    at.date_input(key=prefix+"ended_on").set_value(date(2026, 7, 10))
    at.text_input(key=prefix+"reason").set_value("Planting cleared")
    at.text_input(key=prefix+"source").set_value("Synthetic notebook")
    next(b for b in at.button if b.label == "Close cycle").click().run(timeout=15)
    assert not at.exception and not at.error
    closed = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["lifecycle"]
    assert closed["status"] == "closed" and closed["ended_on"] == "2026-07-10"
    assert any("closed" in option for option in at.selectbox(key="crop_cycle").options)
    prefix = f"crop_close_{cycle}_{closed['revision_id']}_"
    at.date_input(key=prefix+"ended_on").set_value(date(2026, 7, 5))
    at.text_input(key=prefix+"reason").set_value("Correct end date")
    at.text_input(key=prefix+"source").set_value("Synthetic notebook correction")
    next(b for b in at.button if b.label == "Correct cycle end").click().run(timeout=15)
    assert not at.exception and any("precedes a current actual" in e.value for e in at.error)
    at.date_input(key=prefix+"ended_on").set_value(date(2026, 7, 8))
    next(b for b in at.button if b.label == "Correct cycle end").click().run(timeout=15)
    assert not at.exception and not at.error
    corrected = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["lifecycle"]
    assert corrected["ended_on"] == "2026-07-08"
    prefix = f"crop_reopen_{cycle}_{corrected['revision_id']}_"
    at.text_input(key=prefix+"reason").set_value("Crop still producing")
    at.text_input(key=prefix+"source").set_value("Synthetic operator correction")
    next(b for b in at.button if b.label == "Reopen cycle").click().run(timeout=15)
    assert not at.exception and not at.error
    journal = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))
    assert journal["lifecycle"]["status"] == "open"
    assert len([r for r in journal["records"] if r["kind"] == "lifecycle"]) == 3
    assert journal["harvest_totals"][0]["quantity"] == "2.5"


@pytest.mark.parametrize("kind", ["stage", "scouting", "harvest"])
def test_voided_record_beyond_new_cycle_end_requires_explicit_date_before_restore(tmp_path, kind):
    from agronaut_agent.tests.test_crop_records import harvest, observation

    at, brain, _, cycle = planting(tmp_path)
    data = harvest(day="2026-07-11") if kind == "harvest" else observation(kind, day="2026-07-11")
    record = brain.crop_records("web", "operator", f"record_{kind}", dict(cycle_id=cycle, data=data, request_id="actual"))
    cancelled = brain.crop_records("web", "operator", "void_harvest" if kind == "harvest" else "void_observation",
                                   dict(record_id=record["id"], reason="Wrong date", request_id="void"))
    brain.crop_records("web", "operator", "close_cycle", dict(cycle_id=cycle, ended_on="2026-07-10",
        source="Synthetic fixture", reason="Planting ended", request_id="close", expected_revision=None))
    at.run(timeout=15)
    key = f"crop_correction_{cycle}" if kind == "harvest" else f"crop_{kind}_correction_{cycle}"
    at.selectbox(key=key).set_value(cancelled["id"]).run(timeout=15)
    assert not at.exception
    assert at.date_input(key=f"crop_{kind}_{cycle}_{cancelled['id']}_day").value is None


def test_stale_closure_form_cannot_overwrite_another_clients_cycle_change(tmp_path):
    at, brain, _, cycle = planting(tmp_path)
    prefix = f"crop_close_{cycle}_initial_"
    at.date_input(key=prefix+"ended_on").set_value(date(2026, 7, 10))
    at.text_input(key=prefix+"reason").set_value("Old form")
    at.text_input(key=prefix+"source").set_value("Synthetic fixture")
    newer = brain.crop_records("web", "operator", "close_cycle", dict(cycle_id=cycle, ended_on="2026-07-08",
        source="Another client", reason="Planting cleared", request_id="close-other", expected_revision=None))
    next(b for b in at.button if b.label == "Close cycle").click().run(timeout=15)
    assert not at.exception and any("lifecycle changed" in e.value for e in at.error)
    assert brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["lifecycle"]["revision_id"] == newer["id"]


def add_measurement(brain, unit, cycle=None):
    from datetime import datetime, timezone

    from agro_model.farm import Observation

    owner = next(f["owner_id"] for f in brain.list_farms("web", "operator") if f["id"] == unit["farm_id"])
    at = datetime(2026, 7, 11, 12, tzinfo=timezone.utc)
    brain._farms.record_observation(owner, Observation("gauge", owner, unit["id"], cycle,
        "rainfall", 5, "mm", at, at, "measured", "Synthetic gauge"))


def fill_measurement_review(at, revision=None, quality="rejected"):
    prefix = f"crop_measurement_review_gauge_{revision or 'initial'}_"
    at.selectbox(key=prefix+"quality").set_value(quality)
    at.text_input(key=prefix+"reason").set_value("Operator reviewed the date")
    at.text_input(key=prefix+"source").set_value("Synthetic notebook")


def test_measurement_review_resolves_closure_conflict_and_requires_reopening_to_restore(tmp_path):
    at, brain, unit, cycle = planting(tmp_path)
    add_measurement(brain, unit, cycle)
    at.run(timeout=15)
    at.selectbox(key="crop_measurement").set_value("gauge").run(timeout=15)
    fill_measurement_review(at)
    next(b for b in at.button if b.label == "Save measurement review").click().run(timeout=15)
    assert not at.exception and not at.error
    measurement = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["measurements"][0]
    revision = measurement["quality_review"]["id"]
    assert measurement["quality"] == "unverified" and measurement["effective_quality"] == "rejected"
    closed = brain.crop_records("web", "operator", "close_cycle", dict(cycle_id=cycle, ended_on="2026-07-10",
        source="Synthetic fixture", reason="Planting ended", request_id="close", expected_revision=None))
    at.run(timeout=15)
    fill_measurement_review(at, revision, "checked")
    next(b for b in at.button if b.label == "Save measurement review").click().run(timeout=15)
    assert not at.exception and any("after the crop cycle end" in e.value for e in at.error)
    brain.crop_records("web", "operator", "reopen_cycle", dict(cycle_id=cycle, reason="Actual activity continued",
        source="Synthetic correction", request_id="reopen", expected_revision=closed["id"]))
    next(b for b in at.button if b.label == "Save measurement review").click().run(timeout=15)
    assert not at.exception and not at.error
    measurement = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["measurements"][0]
    assert measurement["effective_quality"] == "checked" and len(measurement["quality_history"]) == 2
    assert measurement["provenance"] == "measured" and measurement["value"] == 5


def test_unassigned_measurement_can_be_reviewed_without_a_crop_cycle(tmp_path):
    brain, unit, _ = setup(tmp_path)
    add_measurement(brain, unit)
    at = AppTest.from_string(SCRIPT).run(timeout=15)
    assert not at.exception
    at.selectbox(key="crop_measurement").set_value("gauge").run(timeout=15)
    fill_measurement_review(at)
    next(b for b in at.button if b.label == "Save measurement review").click().run(timeout=15)
    assert not at.exception and not at.error
    assert brain.crop_records("web", "operator", "list_measurements", {})["measurements"][0]["effective_quality"] == "rejected"
    assert brain.crop_records("web", "operator", "list", {})["cycles"] == []


def test_stale_measurement_review_form_cannot_replace_a_new_review(tmp_path):
    brain, unit, _ = setup(tmp_path)
    add_measurement(brain, unit)
    at = AppTest.from_string(SCRIPT).run(timeout=15)
    at.selectbox(key="crop_measurement").set_value("gauge").run(timeout=15)
    fill_measurement_review(at)
    saved = brain.crop_records("web", "operator", "review_measurement", dict(observation_id="gauge", quality="checked",
        reason="Another client review", source="Synthetic fixture", request_id="other-review", expected_revision=None))
    next(b for b in at.button if b.label == "Save measurement review").click().run(timeout=15)
    assert not at.exception and any("review changed" in e.value for e in at.error)
    assert brain.crop_records("web", "operator", "list_measurements", {})["measurements"][0]["quality_review"]["id"] == saved["id"]


def test_season_review_compares_explicit_calculation_with_logged_water_and_shows_missing_days(tmp_path):
    from agronaut_agent.tests.test_field_workflow import inputs, water

    at, brain, _, cycle = planting(tmp_path)
    payload = inputs()
    payload["events"][0]["net_irrigation_mm"] = 1
    plan = brain.field_workflow("web", "operator", "save",
        dict(task="root_zone_budget", inputs=payload, cycle_id=cycle, request_id="review-plan"))
    brain.field_workflow("web", "operator", "record_day", dict(cycle_id=cycle, data=water(), request_id="day"))
    brain.crop_records("web", "operator", "close_cycle", dict(cycle_id=cycle, ended_on="2026-07-10",
        reason="Planting ended", source="Synthetic notebook", request_id="close", expected_revision=None))
    at.run(timeout=15)
    assert not at.exception
    selector = f"crop_review_calculation_{cycle}"
    assert at.selectbox(key=selector).value is None
    at.selectbox(key=selector).set_value(plan["id"]).run(timeout=15)
    assert not at.exception and not at.error
    assert any("Water logs cover 1 of 10 completed days" in m.value for m in at.markdown)
    assert any("not savings" in c.value for c in at.caption)
    table = next(frame.value for frame in at.dataframe if "difference_net_irrigation_mm" in frame.value.columns)
    assert table.iloc[0]["input_net_irrigation_mm"] == 1
    assert table.iloc[0]["recorded_net_irrigation_mm"] == 3
    assert table.iloc[0]["difference_net_irrigation_mm"] == "2"
    assert table.iloc[0]["status"] == "matched"
