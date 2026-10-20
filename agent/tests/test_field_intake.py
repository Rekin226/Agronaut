"""Guided water intake through the rendered UI and real scoped calculation service."""

from datetime import date

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from agent.tests.test_field_ui import _SCRIPT, setup  # noqa: E402
from agro_model.farm import ProductionCycle  # noqa: E402
from agronaut_agent.domains import run_capability  # noqa: E402


def form(tmp_path, *, budget=False, days=1):
    brain, unit, before = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key="field_intake_task").set_value("root_zone_budget" if budget else "crop_water")
    at.date_input(key="field_intake_start").set_value(date(2026, 7, 6))
    at.number_input(key="field_intake_count").set_value(days).run(timeout=15)
    assert not at.exception
    return at, brain, unit, before


def fill(at, *, budget=False, days=1, initialized=True, solar=False):
    numbers = dict(area=1.0, kc=1.15, elevation=100.0, wind_height=10.0)
    if solar:
        numbers["latitude"] = 50.8
    texts = dict(crop="tomato", stage="mid-season", kc_source="Synthetic coefficient fixture")
    at.selectbox(key="field_intake_area_unit").set_value("hectares")
    if budget:
        numbers.update(fc=32.0, wp=15.0, root_depth=0.8, readily_available=40.0, efficiency=80.0)
        texts.update(soil_source="Synthetic soil fixture", efficiency_source="Synthetic efficiency fixture")
        if initialized:
            numbers["depletion"] = 60.0
            texts["initial_source"] = "Synthetic initial soil state"
    for index in range(days):
        prefix = f"weather_{index}_"
        at.selectbox(key=f"field_intake_{prefix}kind").set_value("observed" if index == 0 else "forecast")
        if index:
            texts[prefix + "issued_at"] = "2026-07-06T12:00:00+00:00"
        numbers.update({prefix + name: value for name, value in dict(
            t_min_c=12.3, t_max_c=21.5, rh_min_pct=63.0, rh_max_pct=84.0,
            wind_m_s=2.7777777777777777, **({"solar_radiation": 22.0} if solar else {"radiation": 13.28})).items()})
        texts[prefix + "source"] = "Synthetic weather/site fixture, not field evidence"
        if budget:
            numbers.update({prefix + name: 0.0 for name in ("rainfall", "irrigation", "runoff")})
            texts[prefix + "event_source"] = "Explicit scenario: no irrigation or runoff"
    for key, value in numbers.items():
        at.number_input(key="field_intake_" + key).set_value(value)
    for key, value in texts.items():
        at.text_input(key="field_intake_" + key).set_value(value)


def submit(at):
    next(b for b in at.button if b.label == "Calculate and save field water").click().run(timeout=15)
    assert not at.exception


def selected(at, brain):
    return brain.field_workflow("web", "operator", "get", {"calculation_id": at.selectbox(key="field_plan").value})


def test_guided_two_day_budget_uses_converted_units_and_explicit_sources(tmp_path):
    at, brain, _, before = form(tmp_path, budget=True, days=2)
    fill(at, budget=True, days=2)
    submit(at)
    assert not at.error
    saved = selected(at, brain)
    assert saved["id"] != before["id"]
    assert saved["inputs"]["area_m2"] == 10000
    assert saved["inputs"]["soil"]["field_capacity"] == 0.32
    assert saved["inputs"]["soil"]["wilting_point"] == 0.15
    assert saved["inputs"]["soil"]["depletion_fraction"] == 0.4
    assert saved["inputs"]["application_efficiency"]["value"] == 0.8
    assert saved["inputs"]["initial_state"]["as_of"] == "2026-07-05"
    assert [w["day"] for w in saved["inputs"]["weather"]] == ["2026-07-06", "2026-07-07"]
    assert [w["kind"] for w in saved["inputs"]["weather"]] == ["observed", "forecast"]
    assert saved["result"] == run_capability("soil", "root_zone_budget", saved["inputs"])
    records = brain.field_workflow("web", "operator", "list", {})
    assert records["water_days"] == []
    assert len(records["calculations"]) == 2
    submit(at)  # Same submitted form is an idempotent retry, not another plan.
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 2


def test_demand_form_keeps_unknown_rain_and_never_asks_for_soil_or_fish(tmp_path):
    at, brain, _, _ = form(tmp_path)
    assert all(w.value is None for w in at.number_input if w.key.startswith("field_intake_") and w.key != "field_intake_count")
    assert at.selectbox(key="field_intake_weather_0_kind").value is None
    assert not any("fish" in w.label.lower() for w in at.number_input)
    # Other independent forms on this page can collect soil properties. The
    # simple crop-demand intake must not require them to calculate demand.
    intake_numbers = [w for w in at.number_input if w.key.startswith("field_intake_")]
    assert not any("wilting" in w.label.lower() for w in intake_numbers)
    assert not {"field_intake_fc", "field_intake_wp", "field_intake_root_depth"} & {w.key for w in intake_numbers}
    fill(at)
    submit(at)
    assert not at.error
    saved = selected(at, brain)
    assert saved["task"] == "crop_water"
    assert saved["inputs"]["weather"][0]["rainfall_mm"] is None
    assert "events" not in saved["inputs"] and "soil" not in saved["inputs"]


def test_solar_intake_saves_original_energy_and_visible_estimation_trace(tmp_path):
    at, brain, _, _ = form(tmp_path)
    at.selectbox(key="field_intake_radiation_input").set_value("Solar radiation — estimate net radiation").run(timeout=15)
    fill(at, solar=True)
    submit(at)
    assert not at.error
    saved = selected(at, brain)
    weather = saved['inputs']['weather'][0]
    assert weather['input_type'] == 'solar_radiation_fao56'
    assert weather['solar_radiation_mj_m2'] == 22
    assert weather['latitude_deg'] == 50.8
    assert 'net_radiation_mj_m2' not in weather
    assert any('estimated net radiation' in block.value for block in at.text)
    assert brain.field_workflow('web', 'operator', 'list', {})['water_days'] == []


def test_switching_radiation_basis_clears_dated_inputs(tmp_path):
    at, _, _, _ = form(tmp_path)
    fill(at)
    submit(at)
    at.selectbox(key="field_intake_radiation_input").set_value("Solar radiation — estimate net radiation").run(timeout=15)
    assert at.number_input(key='field_intake_weather_0_solar_radiation').value is None
    assert at.number_input(key='field_intake_weather_0_t_min_c').value is None
    assert not any(w.key == 'field_intake_weather_0_radiation' for w in at.number_input)


def test_budget_without_initial_state_keeps_refill_unknown(tmp_path):
    at, brain, _, _ = form(tmp_path, budget=True)
    fill(at, budget=True, initialized=False)
    submit(at)
    assert not at.error
    saved = selected(at, brain)
    assert "initial_state" not in saved["inputs"]
    requirement = saved["result"]["result"]["days"][0]["requirement"]
    assert requirement["values"]["gross_irrigation_l"] is None
    assert requirement["missing_inputs"] == ["soil_state"]


@pytest.mark.parametrize("missing", ["rainfall", "irrigation", "runoff", "source", "kind"])
def test_missing_budget_inputs_fail_without_guessing_or_saving(tmp_path, missing):
    at, brain, _, _ = form(tmp_path, budget=True)
    fill(at, budget=True)
    key = "field_intake_weather_0_" + missing
    if missing in ("rainfall", "irrigation", "runoff"):
        at.number_input(key=key).set_value(None)
    elif missing == "source":
        at.text_input(key=key).set_value("")
    else:
        at.selectbox(key=key).set_value(None)
    submit(at)
    assert at.error
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 1


def test_forecast_requires_issue_timestamp_with_timezone(tmp_path):
    at, brain, _, _ = form(tmp_path)
    fill(at)
    at.selectbox(key="field_intake_weather_0_kind").set_value("forecast")
    at.text_input(key="field_intake_weather_0_issued_at").set_value("2026-07-05T12:00:00")
    submit(at)
    assert any("timezone" in err.value for err in at.error)
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 1


def test_changing_window_clears_dated_inputs_but_keeps_field_parameters(tmp_path):
    at, _, _, _ = form(tmp_path, budget=True)
    fill(at, budget=True)
    # Submit once to commit values to widget state before changing the date.
    submit(at)
    at.date_input(key="field_intake_start").set_value(date(2026, 7, 8)).run(timeout=15)
    assert not at.exception
    assert at.number_input(key="field_intake_area").value == 1
    assert at.number_input(key="field_intake_weather_0_radiation").value is None
    assert at.number_input(key="field_intake_depletion").value is None
    assert at.text_input(key="field_intake_initial_source").value == ""


def test_unit_switch_clears_guided_intake(tmp_path):
    at, brain, _, _ = form(tmp_path)
    fill(at)
    submit(at)
    brain.create_unit("web", "operator", {"farm": {"name": "Other", "timezone": "UTC"},
        "unit": {"name": "Second", "production_system": "soil", "medium": "soil",
                 "environment": "open_field", "water_strategy": "rainfed"}})
    at.run(timeout=15)
    assert not at.exception
    assert at.number_input(key="field_intake_area").value is None
    assert at.text_input(key="field_intake_crop").value == ""
    assert at.date_input(key="field_intake_start").value is None


def test_cycle_selection_pins_crop_and_clears_previous_intake(tmp_path):
    at, brain, unit, before = form(tmp_path)
    fill(at)
    submit(at)
    brain._farms.create_cycle(before["owner_id"], ProductionCycle("onions", unit["id"], "onion", "2026-08-01"))
    at.run(timeout=15)
    at.selectbox(key="field_cycle").set_value("onions").run(timeout=15)
    assert not at.exception
    assert at.text_input(key="field_intake_crop").value == "onion"
    assert at.text_input(key="field_intake_crop").disabled
    assert at.number_input(key="field_intake_area").value is None
    assert at.number_input(key="field_intake_weather_0_radiation").value is None
    assert at.date_input(key="field_intake_start").value is None


def test_missing_date_and_orphan_initial_source_are_reported_without_saving(tmp_path):
    at, brain, _, _ = form(tmp_path, budget=True)
    fill(at, budget=True, initialized=False)
    at.text_input(key="field_intake_initial_source").set_value("No numeric measurement yet")
    submit(at)
    assert any("leave both blank" in err.value for err in at.error)
    at.date_input(key="field_intake_start").set_value(None).run(timeout=15)
    submit(at)
    assert any("first weather day" in err.value for err in at.error)
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 1
