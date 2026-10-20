"""Manual nutrient intake uses no model and preserves unknown quantities and basis."""

from datetime import date

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from agent.tests.test_field_ui import _SCRIPT, setup  # noqa: E402


def fill(at, prefix):
    at.text_input(key=prefix+"crop").set_value("tomato")
    at.text_input(key=prefix+"stage").set_value("Illustrative planning")
    at.number_input(key=prefix+"area").set_value(5000.0)
    at.date_input(key=prefix+"start").set_value(date(2026, 7, 1))
    at.date_input(key=prefix+"end").set_value(date(2026, 7, 31))
    at.checkbox(key=prefix+"remaining").set_value(True)
    for n, value in (("n", 60.0), ("p2o5", 20.0), ("k2o", 40.0)):
        at.number_input(key=prefix+n+"_target").set_value(value)
    at.text_input(key=prefix+"target_source").set_value("Synthetic target")
    at.text_input(key=prefix+"target_scope").set_value("Arithmetic fixture, not a crop recommendation")
    p = prefix+"product_0_"
    at.text_input(key=p+"name").set_value("Synthetic 10-10-20")
    at.number_input(key=p+"quantity").set_value(100.0)
    at.selectbox(key=p+"unit").set_value("kg")
    for n, value in (("n", 10.0), ("p2o5", 10.0), ("k2o", 20.0)):
        at.number_input(key=p+n).set_value(value)
    at.text_input(key=p+"source").set_value("Synthetic product label")


def test_nutrient_accounting_saves_and_renders_without_logging_an_application(tmp_path):
    brain, unit, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    assert not at.exception
    prefix = f"field_nutrient_{unit['id']}_none_"
    assert at.number_input(key=prefix+"area").value is None
    assert at.number_input(key=prefix+"n_target").value is None
    fill(at, prefix)
    next(b for b in at.button if b.label == "Calculate and save nutrient budget").click().run(timeout=15)
    assert not at.exception and not at.error
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert saved["task"] == "nutrient_budget"
    assert saved["result"]["result"]["nutrients"]["n"]["shortfall_kg"] == 20
    assert any("not an application recommendation" in item.value for item in at.text)
    assert brain.field_workflow("web", "operator", "list", {})["water_days"] == []


def test_unknown_grade_is_not_filled_and_target_basis_is_required(tmp_path):
    brain, unit, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    prefix = f"field_nutrient_{unit['id']}_none_"
    fill(at, prefix)
    at.checkbox(key=prefix+"remaining").set_value(False)
    next(b for b in at.button if b.label == "Calculate and save nutrient budget").click().run(timeout=15)
    assert not at.exception and at.error
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 1
    at.checkbox(key=prefix+"remaining").set_value(True)
    at.number_input(key=prefix+"product_0_n").set_value(None)
    next(b for b in at.button if b.label == "Calculate and save nutrient budget").click().run(timeout=15)
    assert not at.exception and not at.error
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert saved["result"]["result"]["nutrients"]["n"]["shortfall_kg"] is None


def test_unit_switch_does_not_carry_nutrient_targets_into_another_field(tmp_path):
    brain, unit, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    prefix = f"field_nutrient_{unit['id']}_none_"
    fill(at, prefix)
    another = brain.create_unit("web", "operator", {"farm": {"name": "Other", "timezone": "UTC"},
        "unit": {"name": "Other field", "production_system": "soil", "medium": "soil", "environment": "open_field", "water_strategy": "rainfed"}})
    at.run(timeout=15)
    assert not at.exception
    assert at.number_input(key=f"field_nutrient_{another['id']}_none_n_target").value is None
