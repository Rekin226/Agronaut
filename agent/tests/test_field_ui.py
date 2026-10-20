"""Field log and replay through rendered forms, without a language model."""

import json
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from agronaut_agent.core import AgronautAgent  # noqa: E402

_SHARED = {}
_SCRIPT = """
from agent.tests.test_field_ui import _SHARED
from agent.field_ui import render_field_records
render_field_records(_SHARED['brain'], 'operator')
"""


class NoModel:
    def bind_tools(self, tools):
        return self

    def invoke(self, *args, **kwargs):
        raise AssertionError("field records must not call a model")


def setup(tmp_path):
    brain = AgronautAgent(db_path=tmp_path/"web.sqlite3", chat_model=NoModel())
    spec = {"farm": {"name": "Home", "timezone": "UTC"},
            "unit": {"name": "Field", "production_system": "soil", "medium": "soil",
                     "environment": "open_field", "water_strategy": "supplemental", "irrigation_method": "surface"}}
    unit = brain.create_unit("web", "operator", spec)
    data = json.loads((Path(__file__).resolve().parents[2]/"examples/field_water.json").read_text())
    data["site"]["timezone"] = "UTC"
    data["weather"][0]["kind"] = "observed"
    plan = brain.field_workflow("web", "operator", "save",
                                dict(task="root_zone_budget", inputs=data, request_id="initial"))
    _SHARED["brain"] = brain
    return brain, unit, plan


def test_adopted_equipment_target_and_changed_evidence_are_visible(tmp_path):
    from agronaut_agent.tests.test_equipment_consultation import answers

    brain, _, initial = setup(tmp_path)
    data = json.loads(json.dumps(initial["inputs"]))
    data["area_m2"] = 500
    parent = brain.field_workflow("web", "operator", "save", dict(task="root_zone_budget", inputs=data, request_id="target"))
    water = dict(day="2026-07-06", rainfall_mm=0, net_irrigation_mm=0, runoff_mm=0, source="Synthetic log",
                 provenance="reported", quality="unverified")
    record = brain.field_workflow("web", "operator", "record_day", dict(data=water, request_id="day"))
    parent = brain.field_workflow("web", "operator", "replay", dict(calculation_id=parent["id"], water_record_ids=[record["id"]], request_id="replay"))
    supplied = dict(answers("drip_design"), application_input="budget", target_calculation=parent["id"],
                    target_day="2026-07-06", target_reason="Synthetic whole-area sizing scenario", target_accepted="use")
    view = brain.field_workflow("web", "operator", "start_intake", {})
    while view["status"] != "ready":
        view = brain.field_workflow("web", "operator", "answer_intake", dict(answer=supplied[view["next_field"]], expected_revision=view["revision"]))
    saved = brain.field_workflow("web", "operator", "save_intake", dict(draft_id=view["draft_id"], expected_revision=view["revision"]))["calculation"]
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key="field_plan").set_value(saved["id"]).run(timeout=15)
    assert not at.exception and not at.error
    assert any("END-OF-DAY" in item.value and parent["id"] in item.value for item in at.caption)
    assert not any("adopted water target needs review" in item.value for item in at.warning)
    brain.field_workflow("web", "operator", "record_day", dict(data=dict(water, rainfall_mm=1), supersedes_id=record["id"], request_id="correction"))
    at.run(timeout=15)
    assert not at.exception and not at.error
    assert any("adopted water target needs review" in item.value for item in at.warning)
    assert brain.field_workflow("web", "operator", "get", dict(calculation_id=saved["id"])) == saved


def test_web_daily_totals_and_replay_create_new_result_without_model(tmp_path):
    brain, _, before = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    assert not at.exception
    assert at.number_input(key="field_rain").value is None
    at.date_input(key="field_day").set_value(date(2026, 7, 6))
    at.number_input(key="field_rain").set_value(5.0)
    at.number_input(key="field_irrigation").set_value(3.0)
    at.number_input(key="field_runoff").set_value(1.0)
    at.text_input(key="field_source").set_value("Synthetic measurement fixture")
    next(b for b in at.button if b.label == "Save daily totals").click().run(timeout=15)
    assert not at.exception and not at.error
    records = brain.field_workflow("web", "operator", "list", {})
    assert len(records["water_days"]) == 1
    record_id = records["water_days"][0]["id"]
    at.multiselect(key="field_replay_days").set_value([record_id])
    at.button(key="field_replay").click().run(timeout=15)
    assert not at.exception and not at.error
    records = brain.field_workflow("web", "operator", "list", {})
    assert len(records["calculations"]) == 2
    selected = at.selectbox(key="field_plan").value
    assert selected != before["id"]
    after = brain.field_workflow("web", "operator", "get", {"calculation_id": selected})
    assert after["parent_id"] == before["id"]
    assert after["inputs"]["rainfall_overrides"][0]["rainfall_mm"] == 5


def test_web_does_not_turn_unknown_amounts_into_zero(tmp_path):
    brain, _, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.text_input(key="field_source").set_value("Unknown amounts")
    next(b for b in at.button if b.label == "Save daily totals").click().run(timeout=15)
    assert not at.exception
    assert at.error
    assert brain.field_workflow("web", "operator", "list", {})["water_days"] == []


def test_web_weather_revision_selects_new_version_and_preserves_original(tmp_path):
    brain, _, before = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    patch = dict(weather=[dict(before["inputs"]["weather"][0], net_radiation_mj_m2=16,
                               source="Synthetic corrected weather")])
    at.text_area(key=f"field_weather_patch_{before['id']}").set_value(json.dumps(patch))
    at.button(key="field_revise_weather").click().run(timeout=15)
    assert not at.exception and not at.error
    selected = at.selectbox(key="field_plan").value
    updated = brain.field_workflow("web", "operator", "get", {"calculation_id": selected})
    assert updated["parent_id"] == before["id"]
    assert updated["inputs"]["weather"][0]["net_radiation_mj_m2"] == 16
    assert brain.field_workflow("web", "operator", "get", {"calculation_id": before["id"]}) == before
    assert at.text_area(key=f"field_weather_patch_{selected}").value == ""


def test_web_weather_revision_reports_invalid_extension_without_saving(tmp_path):
    brain, _, before = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    patch = dict(weather=[dict(before["inputs"]["weather"][0], day="2026-07-07")])
    at.text_area(key=f"field_weather_patch_{before['id']}").set_value(json.dumps(patch))
    at.button(key="field_revise_weather").click().run(timeout=15)
    assert not at.exception and at.error
    assert "events must cover exactly" in at.error[0].value
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 1


def test_stale_web_unit_cannot_save_into_new_selection(tmp_path):
    brain, first, _ = setup(tmp_path)
    brain.create_unit("web", "operator", {"farm": {"name": "Other", "timezone": "UTC"},
        "unit": {"name": "Second", "production_system": "soil", "medium": "soil",
                 "environment": "open_field", "water_strategy": "rainfed"}})
    with pytest.raises(ValueError, match="selected unit changed"):
        brain.field_workflow("web", "operator", "list", {}, expected_unit_id=first["id"])


def test_external_unit_switch_clears_unsaved_water_form(tmp_path):
    brain, _, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.number_input(key="field_rain").set_value(42.0).run(timeout=15)
    brain.create_unit("web", "operator", {"farm": {"name": "Other", "timezone": "UTC"},
        "unit": {"name": "Second", "production_system": "soil", "medium": "soil",
                 "environment": "open_field", "water_strategy": "rainfed"}})
    at.run(timeout=15)
    assert not at.exception
    assert at.number_input(key="field_rain").value is None
    assert not at.selectbox(key="field_cycle").value


def test_full_app_routes_to_field_records_with_no_chat_model(tmp_path):
    setup(tmp_path)
    script = """
import streamlit as st
import app
from agent.tests.test_field_ui import _SHARED
st.session_state.agent = _SHARED['brain']
st.session_state.web_user = 'operator'
st.session_state.app_mode = 'Field records'
app.main()
"""
    at = AppTest.from_string(script).run(timeout=20)
    assert not at.exception
    assert at.number_input(key="field_rain").value is None
    assert at.selectbox(key="field_plan").value


def test_saved_sprinkler_design_renders_method_report_without_water_replay(tmp_path):
    brain, _, _ = setup(tmp_path)
    data = json.loads((Path(__file__).resolve().parents[2]/"examples/sprinkler_design.json").read_text())
    saved = brain.field_workflow("web", "operator", "save",
                                 dict(task="sprinkler_design", inputs=data, request_id="sprinkler"))
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    assert not at.exception
    assert at.selectbox(key="field_plan").value == saved["id"]
    assert any("Agronaut sprinkler design" in block.value for block in at.text)
    assert not any(b.key in ("field_replay", "field_revise_weather") for b in at.button)
    assert brain.field_workflow("web", "operator", "list", {})["water_days"] == []
    with pytest.raises(ValueError, match="weather revisions require"):
        brain.field_workflow("web", "operator", "revise_weather", dict(
            calculation_id=saved["id"], weather=[{
                "day": "2026-07-06", "t_min_c": 20, "t_max_c": 30, "rh_min_pct": 40, "rh_max_pct": 80,
                "wind_m_s": 2, "wind_height_m": 2, "elevation_m": 100, "net_radiation_mj_m2": 12,
                "rainfall_mm": 0, "source": "Synthetic test", "kind": "scenario"}], request_id="wrong"))


def test_saved_surface_event_renders_evaluation_without_a_runtime_or_replay(tmp_path):
    brain, _, _ = setup(tmp_path)
    data = json.loads((Path(__file__).resolve().parents[2]/"examples/surface_event.json").read_text())
    saved = brain.field_workflow("web", "operator", "save",
                                 dict(task="surface_evaluation", inputs=data, request_id="surface"))
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    assert not at.exception
    assert at.selectbox(key="field_plan").value == saved["id"]
    assert any("Agronaut surface-irrigation event" in block.value for block in at.text)
    assert not any(b.key in ("field_replay", "field_revise_weather") for b in at.button)
    assert brain.field_workflow("web", "operator", "list", {})["water_days"] == []


def test_closed_cycle_is_visible_and_blocks_later_actual_water_from_app(tmp_path):
    brain, _, _ = setup(tmp_path)
    cycle = brain.crop_records("web", "operator", "create", dict(crop="tomato", planted_on="2026-07-01",
        source="Synthetic planting", request_id="plant"))["cycle"]["id"]
    brain.crop_records("web", "operator", "close_cycle", dict(cycle_id=cycle, ended_on="2026-07-10",
        reason="Planting ended", source="Synthetic notebook", request_id="end", expected_revision=None))
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key="field_cycle").set_value(cycle).run(timeout=15)
    assert not at.exception
    assert any("cycle ended on 2026-07-10" in info.value for info in at.info)
    at.date_input(key="field_day").set_value(date(2026, 7, 11))
    at.number_input(key="field_rain").set_value(0.0)
    at.number_input(key="field_irrigation").set_value(5.0)
    at.number_input(key="field_runoff").set_value(0.0)
    at.text_input(key="field_source").set_value("Synthetic fixture")
    next(b for b in at.button if b.label == "Save daily totals").click().run(timeout=15)
    assert not at.exception and any("after the crop cycle end" in e.value for e in at.error)
    assert brain.field_workflow("web", "operator", "list", {})["water_days"] == []
    at.date_input(key="field_day").set_value(date(2026, 7, 10))
    next(b for b in at.button if b.label == "Save daily totals").click().run(timeout=15)
    assert not at.exception and not at.error
    assert brain.field_workflow("web", "operator", "list", {})["water_days"][0]["day"] == "2026-07-10"
