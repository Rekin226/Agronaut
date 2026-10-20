"""Weather preview -> sourced calculation through the real UI, without an LLM."""

from datetime import date

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from agent.tests.test_field_ui import _SCRIPT, setup  # noqa: E402
from agronaut_agent import weather_provider as provider  # noqa: E402
from agronaut_agent.tests.test_weather_provider import bundle  # noqa: E402


def open_preview(tmp_path, monkeypatch):
    brain, unit, before = setup(tmp_path)
    calls = []
    def fetch(request):
        calls.append(request)
        return bundle()
    monkeypatch.setattr(provider, "fetch_weather", fetch)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.date_input(key="field_intake_start").set_value(date(2026, 7, 6))
    at.selectbox(key="field_intake_weather_input").set_value("Fetch Open-Meteo").run(timeout=15)
    assert not at.exception and not calls
    at.number_input(key="field_provider_latitude").set_value(5.6)
    at.number_input(key="field_provider_longitude").set_value(-0.2)
    next(b for b in at.button if b.label == "Fetch weather preview").click().run(timeout=15)
    assert not at.exception and not at.error and len(calls) == 1
    assert calls[0] == dict(latitude=5.6, longitude=-0.2, timezone="UTC", start="2026-07-06", end="2026-07-06")
    return at, brain, unit, before, calls


def test_provider_preview_then_save_crop_demand_preserves_snapshot(tmp_path, monkeypatch):
    at, brain, _, before, calls = open_preview(tmp_path, monkeypatch)
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 1
    at.number_input(key="field_intake_area").set_value(100.0)
    at.number_input(key="field_intake_kc").set_value(1.2)
    for key, value in dict(crop="tomato", stage="mid-season", kc_source="Synthetic local coefficient").items():
        at.text_input(key="field_intake_" + key).set_value(value)
    next(b for b in at.button if b.label == "Calculate and save field water").click().run(timeout=15)
    assert not at.exception and not at.error
    assert len(calls) == 1
    selected = at.selectbox(key="field_plan").value
    assert selected != before["id"]
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=selected))
    assert saved["inputs"]["weather_sources"] == bundle()["weather_sources"]
    assert saved["result"]["result"]["days"][0]["demand"]["values"]["crop_demand_l_day"] == pytest.approx(720)
    assert brain.field_workflow("web", "operator", "list", {})["water_days"] == []


def test_changing_window_invalidates_preview_and_fetch_failure_discards_stale_data(tmp_path, monkeypatch):
    at, brain, _, _, calls = open_preview(tmp_path, monkeypatch)
    at.date_input(key="field_intake_start").set_value(date(2026, 7, 7)).run(timeout=15)
    assert not at.exception and len(calls) == 1
    assert "field_provider_bundle" not in at.session_state
    next(b for b in at.button if b.label == "Calculate and save field water").click().run(timeout=15)
    assert not at.exception and "Fetch and review" in at.error[0].value
    def fail(request):
        raise ValueError("Provider unavailable in this synthetic test")
    monkeypatch.setattr(provider, "fetch_weather", fail)
    next(b for b in at.button if b.label == "Fetch weather preview").click().run(timeout=15)
    assert not at.exception and "Provider unavailable" in at.error[0].value
    assert "field_provider_bundle" not in at.session_state
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 1


def test_unit_switch_clears_coordinates_and_preview(tmp_path, monkeypatch):
    at, brain, _, _, calls = open_preview(tmp_path, monkeypatch)
    brain.create_unit("web", "operator", {"farm": {"name": "Other", "timezone": "UTC"},
        "unit": {"name": "Second", "production_system": "soil", "medium": "soil",
                 "environment": "open_field", "water_strategy": "rainfed"}})
    at.run(timeout=15)
    assert not at.exception and len(calls) == 1
    assert "field_provider_bundle" not in at.session_state
    assert "field_provider_latitude" not in at.session_state


def test_provider_budget_uses_explicit_events_and_initialized_soil(tmp_path, monkeypatch):
    at, brain, _, _, calls = open_preview(tmp_path, monkeypatch)
    at.selectbox(key="field_intake_task").set_value("root_zone_budget").run(timeout=15)
    for key, value in dict(area=100.0, kc=1.2, fc=32.0, wp=15.0, root_depth=0.8,
                           readily_available=40.0, efficiency=80.0, depletion=60.0,
                           weather_0_irrigation=0.0, weather_0_runoff=0.0).items():
        at.number_input(key="field_intake_" + key).set_value(value)
    for key, value in dict(crop="tomato", stage="mid-season", kc_source="Synthetic coefficient",
                           soil_source="Synthetic soil", efficiency_source="Synthetic efficiency",
                           initial_source="Synthetic initial depletion",
                           weather_0_event_source="Explicit scenario: no irrigation or runoff").items():
        at.text_input(key="field_intake_" + key).set_value(value)
    next(b for b in at.button if b.label == "Calculate and save field water").click().run(timeout=15)
    assert not at.exception and not at.error and len(calls) == 1
    selected = at.selectbox(key="field_plan").value
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=selected))
    result = saved["result"]["result"]["days"][0]
    assert result["balance"]["values"]["depletion_mm"] == pytest.approx(55.2)
    assert result["requirement"]["values"]["gross_irrigation_l"] == pytest.approx(6900)
    assert brain.field_workflow("web", "operator", "list", {})["water_days"] == []
