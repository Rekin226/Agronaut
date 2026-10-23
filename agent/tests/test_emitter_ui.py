"""Native sample rows retain unknowns, collection times and scoped state."""

import pytest
from streamlit.testing.v1 import AppTest

from agent.tests.test_field_ui import _SCRIPT, setup

ROWS = 'E1,L1,"Head, west",10,30,?\nE2,L1,Middle,20,60,150\nE3,L2,Head,30,30,155\nE4,L2,Tail,40,60,150'


def filled(tmp_path):
    brain, _, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    for key, value in dict(zone="Zone A", start="2026-07-07T09:00:00Z", end="2026-07-07T09:30:00Z",
        conditions="Synthetic stable conditions", sampling="Synthetic head/tail sampling", source="Synthetic cylinder, timer and gauge").items():
        at.text_input(key="field_emitter_"+key).set_value(value)
    at.selectbox(key="field_emitter_kind").set_value("observed")
    at.number_input(key="field_emitter_count").set_value(4)
    for key in ("stable", "same", "equal"):
        at.checkbox(key="field_emitter_"+key).set_value(True)
    at.text_area(key="field_emitter_samples").set_value(ROWS)
    return at, brain


def submit(at):
    next(b for b in at.button if b.label == "Evaluate and save emitter test").click().run(timeout=15)
    assert not at.exception


def test_native_form_normalizes_times_preserves_quotes_and_unknowns_and_retries(tmp_path):
    at, brain = filled(tmp_path)
    submit(at)
    assert not at.error
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    result = saved["result"]["result"]
    assert result["statistics"]["mean_flow_l_h"] == 2.1
    assert result["statistics"]["low_quarter_du_pct"] == pytest.approx(400/7)
    assert result["samples"][0]["position"] == "Head, west" and result["samples"][0]["pressure_kpa"] is None
    assert brain._farms.export(saved["owner_id"])["water_days"] == []
    submit(at)
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 2
    at.text_area(key="field_emitter_samples").set_value(ROWS.replace(",10,30,?", ",?,30,?"))
    submit(at)
    assert not at.error
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert saved["result"]["result"]["statistics"]["mean_flow_l_h"] is None
    assert any("Known individual flows remain visible" in item.value for item in at.text)
    assert any(b.label == "Download calculation and evidence" for b in at.get("download_button"))


def test_missing_rows_or_unconfirmed_operation_fail_and_unit_switch_clears(tmp_path):
    at, brain = filled(tmp_path)
    at.text_area(key="field_emitter_samples").set_value(ROWS.splitlines()[0])
    submit(at)
    assert at.error
    at.text_area(key="field_emitter_samples").set_value(ROWS)
    at.checkbox(key="field_emitter_stable").set_value(False)
    submit(at)
    assert any("stable_operation" in e.value for e in at.error)
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 1
    brain.create_unit("web", "operator", dict(farm=dict(name="Other", timezone="UTC"), unit=dict(name="Other field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    assert not at.exception
    assert at.text_area(key="field_emitter_samples").value == ""
    assert at.number_input(key="field_emitter_count").value is None
