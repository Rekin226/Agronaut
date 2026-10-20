"""Daily root-depth entry, revisions and measurement-depth checks in real forms."""

from datetime import date

import pytest
from streamlit.testing.v1 import AppTest

from agent.tests.test_field_intake import fill, form, selected, submit
from agent.tests.test_field_ui import _SCRIPT, setup


def root_fields(at, prefix, index, depth, water=None):
    key = f"{prefix}root_day_{index}_"
    at.number_input(key=key + "depth").set_value(depth)
    at.text_input(key=key + "source").set_value("Supplied synthetic rooting depth")
    if water is not None:
        at.number_input(key=key + "water").set_value(water)
        at.text_input(key=key + "layer_source").set_value("Synthetic layer mean immediately before growth")


def test_intake_explicit_growth_and_date_change_clears_dated_values(tmp_path):
    at, brain, _, _ = form(tmp_path, budget=True, days=2)
    at.checkbox(key="field_intake_daily_roots").set_value(True).run(timeout=15)
    fill(at, budget=True, days=2)
    root_fields(at, "field_intake_", 0, .8)
    root_fields(at, "field_intake_", 1, .9, .25)
    submit(at)
    assert not at.error
    saved = selected(at, brain)
    assert [r["root_depth_m"] for r in saved["inputs"]["root_zone_schedule"]] == [.8, .9]
    assert saved["result"]["result"]["days"][1]["balance"]["values"]["total_available_mm"] == pytest.approx(153)
    at.date_input(key="field_intake_start").set_value(date(2026, 7, 7)).run(timeout=15)
    assert not at.exception
    assert at.number_input(key="field_intake_root_day_1_water").value is None
    assert at.number_input(key="field_intake_root_day_1_depth").value is None


def test_revision_requires_layer_water_and_restores_fixed_depth_in_new_version(tmp_path):
    brain, _, base = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    prefix = f"field_roots_{base['id']}_"
    at.checkbox(key=prefix + "enabled").set_value(True).run(timeout=15)
    root_fields(at, prefix, 0, .9)
    at.text_input(key=prefix + "reason").set_value("Supplied growth assumptions")
    next(b for b in at.button if b.label == "Save root-depth revision").click().run(timeout=15)
    assert not at.exception and any("added_layer_water_content" in e.value for e in at.error)
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 1
    root_fields(at, prefix, 0, .9, .25)
    next(b for b in at.button if b.label == "Save root-depth revision").click().run(timeout=15)
    assert not at.exception and not at.error
    child = selected(at, brain)
    assert child["parent_id"] == base["id"]
    assert not any(w.key == prefix + "enabled" for w in at.checkbox)
    prefix = f"field_roots_{child['id']}_"
    at.checkbox(key=prefix + "enabled").set_value(False).run(timeout=15)
    at.text_input(key=prefix + "reason").set_value("Restore original depth")
    next(b for b in at.button if b.label == "Save root-depth revision").click().run(timeout=15)
    assert not at.exception and not at.error
    assert selected(at, brain)["result"] == base["result"]
