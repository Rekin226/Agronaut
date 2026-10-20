"""Dated coefficient intake and journal-linked revisions through rendered forms."""

from datetime import date

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from agent.tests.test_field_ui import _SCRIPT, setup  # noqa: E402
from agro_model.farm import ProductionCycle  # noqa: E402


def test_dated_intake_calculates_each_days_coefficient(tmp_path):
    brain, _, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.date_input(key="field_intake_start").set_value(date(2026, 7, 6))
    at.number_input(key="field_intake_count").set_value(3)
    at.selectbox(key="field_intake_coefficient_mode").set_value("Dated coefficients").run(timeout=15)
    assert not at.exception
    assert at.number_input(key="field_intake_kc_period_0_start_value").value is None
    for key, value in dict(area=100.0, elevation=100.0, wind_height=10.0,
                           kc_period_0_start_value=0.5, kc_period_0_end_value=1.1).items():
        at.number_input(key="field_intake_" + key).set_value(value)
    at.text_input(key="field_intake_crop").set_value("tomato")
    at.text_input(key="field_intake_kc_period_0_stage").set_value("Synthetic development")
    at.text_input(key="field_intake_kc_period_0_source").set_value("Synthetic dated endpoints")
    at.date_input(key="field_intake_kc_period_0_start").set_value(date(2026, 7, 6))
    at.date_input(key="field_intake_kc_period_0_end").set_value(date(2026, 7, 8))
    for index in range(3):
        prefix = f"field_intake_weather_{index}_"
        at.selectbox(key=prefix + "kind").set_value("historical")
        for key, value in dict(t_min_c=12.3, t_max_c=21.5, rh_min_pct=63.0, rh_max_pct=84.0,
                               wind_m_s=10 / 3.6, radiation=13.28).items():
            at.number_input(key=prefix + key).set_value(value)
        at.text_input(key=prefix + "source").set_value("Synthetic meteorology")
    next(b for b in at.button if b.label == "Calculate and save field water").click().run(timeout=15)
    assert not at.exception and not at.error
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert "coefficient" not in saved["inputs"]["crop"]
    assert [r["demand"]["coefficients"]["kc"]["value"] for r in saved["result"]["result"]["days"]] == pytest.approx([0.5, 0.8, 1.1])
    # A date change must not relabel these previously supplied dated phases.
    at.date_input(key="field_intake_start").set_value(date(2026, 7, 7)).run(timeout=15)
    assert at.date_input(key="field_intake_kc_period_0_start").value is None
    assert at.number_input(key="field_intake_kc_period_0_start_value").value is None


def test_revision_links_stage_evidence_and_flags_later_correction(tmp_path):
    brain, unit, original = setup(tmp_path)
    owner = brain._conv.get_or_create_user("web", "operator")
    brain._farms.create_cycle(owner, ProductionCycle("crop-cycle", unit["id"], "tomato", "2026-07-01"))
    original = brain.field_workflow("web", "operator", "save", dict(task="root_zone_budget",
        inputs=original["inputs"], cycle_id="crop-cycle", request_id="cycle-plan"))
    data = dict(entry="north", day="2026-07-06", location="North rows", stage="Development",
                source="Synthetic operator report", provenance="reported")
    report = brain.crop_records("web", "operator", "record_stage",
                                dict(cycle_id="crop-cycle", data=data, request_id="stage"))
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key="field_cycle").set_value("crop-cycle").run(timeout=15)
    assert not at.exception
    prefix = f"field_crop_{original['id']}_"
    at.number_input(key=prefix + "kc").set_value(0.7)
    at.text_input(key=prefix + "kc_source").set_value("Synthetic locally supplied Kc")
    at.text_input(key=prefix + "reason").set_value("Review after a supplied stage report")
    at.multiselect(key=prefix + "stage_records").set_value([report["id"]])
    next(b for b in at.button if b.label == "Save crop coefficient revision").click().run(timeout=15)
    assert not at.exception and not at.error
    selected = at.selectbox(key="field_plan").value
    assert selected != original["id"]
    updated = brain.field_workflow("web", "operator", "get", dict(calculation_id=selected))
    assert updated["crop_stage_evidence"] == [report]
    assert updated["inputs"]["crop"]["coefficient"]["value"] == 0.7
    assert updated["inputs"]["initial_state"] == original["inputs"]["initial_state"]
    brain.crop_records("web", "operator", "record_stage", dict(cycle_id="crop-cycle",
        data=dict(data, stage="Corrected stage"), supersedes_id=report["id"], request_id="corrected-stage"))
    at.run(timeout=15)
    assert not at.exception
    assert any("corrected or voided" in warning.value for warning in at.warning)
    assert next(e for e in at.expander if e.label == "Review growth stages and revise crop coefficients").proto.expanded
    assert brain.field_workflow("web", "operator", "get", dict(calculation_id=selected)) == updated


def test_revision_requires_a_reason_and_cannot_save_invalid_curve(tmp_path):
    brain, _, original = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    next(b for b in at.button if b.label == "Save crop coefficient revision").click().run(timeout=15)
    assert not at.exception and "reason" in at.error[0].value
    prefix = f"field_crop_{original['id']}_"
    at.selectbox(key=prefix + "coefficient_mode").set_value("Dated coefficients").run(timeout=15)
    at.text_input(key=prefix + "reason").set_value("Deliberately incomplete test curve")
    next(b for b in at.button if b.label == "Save crop coefficient revision").click().run(timeout=15)
    assert not at.exception and at.error
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 1


def test_dated_revision_reads_submitted_values_and_renders_only_the_child(tmp_path):
    brain, _, original = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    prefix = f"field_crop_{original['id']}_"
    at.selectbox(key=prefix + "coefficient_mode").set_value("Dated coefficients").run(timeout=15)
    at.date_input(key=prefix + "kc_period_0_start").set_value(date(2026, 7, 6))
    at.date_input(key=prefix + "kc_period_0_end").set_value(date(2026, 7, 8))
    at.number_input(key=prefix + "kc_period_0_start_value").set_value(.5)
    at.number_input(key=prefix + "kc_period_0_end_value").set_value(1.1)
    at.text_input(key=prefix + "kc_period_0_stage").set_value("Synthetic development")
    at.text_input(key=prefix + "kc_period_0_source").set_value("Supplied test curve")
    at.text_input(key=prefix + "reason").set_value("Explicit dated revision")
    next(b for b in at.button if b.label == "Save crop coefficient revision").click().run(timeout=15)
    assert not at.exception and not at.error
    selected = at.selectbox(key="field_plan").value
    assert selected != original["id"]
    assert not any(w.key == prefix + "coefficient_mode" for w in at.selectbox)
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=selected))
    assert saved["inputs"]["crop"]["coefficient_schedule"] == [dict(start="2026-07-06", end="2026-07-08",
        start_value=.5, end_value=1.1, stage="Synthetic development", source="Supplied test curve")]
    at.run(timeout=15)
    assert not at.exception
