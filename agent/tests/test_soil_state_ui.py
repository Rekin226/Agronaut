"""Real forms -> immutable measurements -> explicitly reconciled calculations."""

from datetime import date

import pytest
from streamlit.testing.v1 import AppTest

from agent.tests.test_field_ui import _SCRIPT, setup
from agronaut_agent.observation_quality import ObservationReviews


def record(brain):
    return brain.crop_records("web", "operator", "record_measurement", dict(request_id="soil", data=dict(
        quantity="soil_water_content", value=.25, unit="m3/m3", observed_at="2026-07-06T18:00:00+00:00",
        provenance="measured", source="Synthetic soil sample")))


def fill(at, prefix):
    at.date_input(key=prefix + "day").set_value(date(2026, 7, 6))
    at.number_input(key=prefix + "depth").set_value(.8)
    at.text_input(key=prefix + "representation").set_value("Synthetic full-depth/field mean")
    at.number_input(key=prefix + "adjustment").set_value(2.)
    at.text_input(key=prefix + "source").set_value("Supplied net depletion to midnight")
    at.text_input(key=prefix + "reason").set_value("Review with supplied sampling data")


def test_web_adoption_and_withdrawal_preserve_parent_and_water_log(tmp_path):
    brain, _, base = setup(tmp_path)
    measurement = record(brain)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    prefix = f"field_soil_{base['id']}_{measurement['id']}_"
    assert at.number_input(key=prefix + "depth").value is None
    assert at.number_input(key=prefix + "adjustment").value is None
    fill(at, prefix)
    next(b for b in at.button if b.label == "Save soil-state revision").click().run(timeout=15)
    assert not at.exception and not at.error
    child = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert child["parent_id"] == base["id"]
    assert child["result"]["result"]["days"][0]["soil_state_update"]["values"]["adopted_depletion_mm"] == pytest.approx(58)
    at.text_input(key=f"field_soil_{child['id']}_remove_reason").set_value("Withdraw supplied state")
    next(b for b in at.button if b.label == "Save revision without this soil update").click().run(timeout=15)
    assert not at.exception and not at.error
    restored = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert restored["result"] == base["result"]
    assert brain.field_workflow("web", "operator", "list", {})["water_days"] == []


def test_form_uses_displayed_quality_revision_and_flags_later_change(tmp_path):
    brain, unit, base = setup(tmp_path)
    measurement = record(brain)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    fill(at, f"field_soil_{base['id']}_{measurement['id']}_")
    owner = brain._conv.get_or_create_user("web", "operator")
    reviews = ObservationReviews(brain._farms.db)
    review = reviews.review(owner, unit["id"], measurement["id"], "checked", "Reviewed", "Fixture",
                            request_id="checked", expected_revision=None)
    next(b for b in at.button if b.label == "Save soil-state revision").click().run(timeout=15)
    assert not at.exception and any("review changed" in e.value for e in at.error)
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 1
    next(b for b in at.button if b.label == "Save soil-state revision").click().run(timeout=15)
    assert not at.exception and not at.error
    reviews.review(owner, unit["id"], measurement["id"], "rejected", "Calibration failure", "Fixture",
                   request_id="reject", expected_revision=review["id"])
    at.run(timeout=15)
    assert not at.exception
    assert any("quality review has changed" in w.value for w in at.warning)
    assert next(e for e in at.expander if e.label == "Reconcile soil state with a measurement").proto.expanded
    assert not any(b.label == "Save soil-state revision" for b in at.button)


def test_crop_form_records_reading_without_a_cycle_or_model(tmp_path):
    brain, _, _ = setup(tmp_path)
    script = """
from agent.tests.test_field_ui import _SHARED
from agent.crop_ui import render_crop_records
render_crop_records(_SHARED['brain'], 'operator')
"""
    at = AppTest.from_string(script).run(timeout=15)
    at.selectbox(key="crop_measure_quantity").set_value("soil_water_content").run(timeout=15)
    at.number_input(key="crop_measure_value_soil_water_content").set_value(.25)
    at.text_input(key="crop_measure_stamp").set_value("2026-07-06T18:00:00+00:00")
    at.text_input(key="crop_measure_source").set_value("Synthetic sample")
    next(b for b in at.button if b.label == "Save field measurement").click().run(timeout=15)
    assert not at.exception and not at.error
    records = brain.crop_records("web", "operator", "list_measurements", {})["measurements"]
    assert len(records) == 1 and records[0]["cycle_id"] is None
    assert records[0]["quality"] == "unverified"
    assert records[0]["value"] == .25
