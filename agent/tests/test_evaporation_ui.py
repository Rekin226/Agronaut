"""Native dual-coefficient intake, saved evidence and scoped draft resets."""

import json

from streamlit.testing.v1 import AppTest

from agent.tests.test_field_ui import _SCRIPT, setup
from agro_model.tests.test_evaporation import example
from agronaut_agent.tests.test_evaporation_weather import weather_bundle
from agronaut_agent.tests.test_field_workflow import water


def filled(tmp_path, *, imported=False):
    brain, _, _ = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    if imported:
        at.radio(key="field_dual_weather_mode").set_value("Import weather snapshot").run(timeout=15)
    data = example()
    texts = dict(crop="tomato", soil_source=data["surface_soil"]["source"], as_of=data["initial_state"]["as_of"],
        initial_source=data["initial_state"]["source"], conditions_source=data["conditions"]["source"],
        source="Synthetic daily inputs")
    if not imported:
        texts.update(method="Synthetic grass-reference total", weather_source="Synthetic ET and rainfall")
    for key, value in texts.items():
        at.text_input(key="field_dual_"+key).set_value(value)
    for key, value in dict(area=1000.0, depletion=20.0, field_capacity=0.3, wilting_point=0.2, depth_m=0.1, readily_evaporable_mm=9.0).items():
        at.number_input(key="field_dual_"+key).set_value(value)
    for key in ("basal_unstressed", "surface_transpiration_negligible", "no_mulch_or_frozen_soil"):
        at.checkbox(key="field_dual_"+key).check()
    if not imported:
        at.selectbox(key="field_dual_kind").set_value("scenario")
    rows = "\n".join(",".join(map(str, (day["day"], weather["et0_mm_day"], weather["rainfall_mm"], day["net_irrigation_mm"], day["runoff_mm"],
        day["kcb"], day["kc_max"], day["cover_fraction"], day["wetted_fraction"], day["exposed_wetted_fraction"])))
        for weather, day in zip(data["weather"], data["days"]))
    if imported:
        rows = "\n".join(",".join([row.split(",")[0], *row.split(",")[3:]]) for row in rows.splitlines())
        at.text_area(key="field_dual_snapshot").set_value(json.dumps(weather_bundle()))
    at.text_area(key="field_dual_rows").set_value(rows)
    return at, brain, rows


def submit(at):
    next(button for button in at.button if button.label == "Calculate and save dual crop demand").click().run(timeout=15)
    assert not at.exception


def test_native_save_retry_report_and_download_leave_root_budget_unchanged(tmp_path):
    at, brain, _ = filled(tmp_path)
    before = brain.field_workflow("web", "operator", "list", {})["calculations"][0]
    before = brain.field_workflow("web", "operator", "get", dict(calculation_id=before["id"]))
    submit(at)
    assert not at.error
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert saved["task"] == "dual_crop_water"
    assert saved["result"]["result"]["days"][-1]["values"]["evaporation_water_limited"]
    assert any("basal ET" in item.value for item in at.text)
    assert any(item.label == "Download calculation and evidence" for item in at.get("download_button"))
    submit(at)
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 2
    assert brain.field_workflow("web", "operator", "get", dict(calculation_id=before["id"])) == before
    assert brain._farms.export(saved["owner_id"])["water_days"] == []


def test_missing_applicability_or_forecast_issue_fails_before_save_and_unit_switch_clears(tmp_path):
    at, brain, _ = filled(tmp_path)
    at.checkbox(key="field_dual_surface_transpiration_negligible").uncheck()
    submit(at)
    assert any("surface_transpiration_negligible" in error.value for error in at.error)
    at.checkbox(key="field_dual_surface_transpiration_negligible").check()
    at.selectbox(key="field_dual_kind").set_value("forecast")
    submit(at)
    assert any("forecast requires" in error.value for error in at.error)
    at.text_input(key="field_dual_issued").set_value("2026-07-05T12:00:00Z")
    submit(at)
    assert not at.error
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert all(row["weather_kind"] == "forecast" for row in saved["result"]["result"]["days"])
    brain.create_unit("web", "operator", dict(farm=dict(name="Other", timezone="UTC"), unit=dict(name="Other field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    assert at.text_area(key="field_dual_rows").value == ""
    assert at.number_input(key="field_dual_depletion").value is None
    assert not at.checkbox(key="field_dual_basal_unstressed").value


def test_missing_day_column_or_invalid_fraction_does_not_create_a_calculation(tmp_path):
    at, brain, rows = filled(tmp_path)
    at.text_area(key="field_dual_rows").set_value("2026-07-06,7,0")
    submit(at)
    assert at.error
    at.text_area(key="field_dual_rows").set_value(rows.replace(",0.3,0.19", ",0.3,0.8"))
    submit(at)
    assert any("exposed/wetted fraction" in error.value for error in at.error)
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 1


def test_imported_snapshot_can_be_revised_without_changing_daily_inputs(tmp_path):
    at, brain, _ = filled(tmp_path, imported=True)
    submit(at)
    assert not at.error
    parent_id = at.selectbox(key="field_plan").value
    parent = brain.field_workflow("web", "operator", "get", dict(calculation_id=parent_id))
    assert parent["inputs"]["weather"] == weather_bundle()["weather"]
    assert parent["inputs"]["weather_sources"] == weather_bundle()["weather_sources"]
    assert all(day["weather_kind"] == "forecast" for day in parent["result"]["result"]["days"])
    assert any("download time, not the forecast issue time" in item.value for item in at.text)
    newer = weather_bundle("2026-07-05T15:00:00+00:00")
    at.text_area(key=f"field_weather_patch_{parent_id}").set_value(json.dumps({key: newer[key] for key in ("weather", "weather_sources")}))
    at.button(key="field_revise_weather").click().run(timeout=15)
    assert not at.exception and not at.error
    child = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert child["parent_id"] == parent_id
    assert child["inputs"]["days"] == parent["inputs"]["days"]
    assert child["inputs"]["weather_sources"] == newer["weather_sources"]
    assert brain.field_workflow("web", "operator", "get", dict(calculation_id=parent_id)) == parent
    assert brain._farms.export(parent["owner_id"])["water_days"] == []


def test_snapshot_import_rejects_tampering_dates_and_duplicate_json_keys(tmp_path):
    at, brain, rows = filled(tmp_path, imported=True)
    bundle = weather_bundle()
    bundle["weather"][0]["et0_mm_day"] = 99
    at.text_area(key="field_dual_snapshot").set_value(json.dumps(bundle))
    submit(at)
    assert any("retained provider snapshot" in error.value for error in at.error)
    at.text_area(key="field_dual_snapshot").set_value(json.dumps(weather_bundle()))
    at.text_area(key="field_dual_rows").set_value(rows.replace("2026-07-08", "2026-07-09"))
    submit(at)
    assert any("must match" in error.value for error in at.error)
    at.text_area(key="field_dual_rows").set_value(rows)
    at.text_area(key="field_dual_snapshot").set_value('{"weather": [], "weather": [], "weather_sources": []}')
    submit(at)
    assert any("duplicate" in error.value.lower() for error in at.error)
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 1


def test_snapshot_import_clears_on_unit_change(tmp_path):
    at, brain, _ = filled(tmp_path, imported=True)
    submit(at)
    assert not at.error
    brain.create_unit("web", "operator", dict(farm=dict(name="Other", timezone="UTC"), unit=dict(name="Other field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    assert at.radio(key="field_dual_weather_mode").value == "Enter daily weather"
    at.radio(key="field_dual_weather_mode").set_value("Import weather snapshot").run(timeout=15)
    assert at.text_area(key="field_dual_snapshot").value == ""
    assert at.text_area(key="field_dual_rows").value == ""


def test_web_actual_water_replay_keeps_historical_weather_and_crop_sources(tmp_path):
    at, brain, _ = filled(tmp_path, imported=True)
    at.text_area(key="field_dual_snapshot").set_value(json.dumps(weather_bundle("2026-07-09T12:00:00+00:00")))
    submit(at)
    parent_id = at.selectbox(key="field_plan").value
    parent = brain.field_workflow("web", "operator", "get", dict(calculation_id=parent_id))
    actual = brain.field_workflow("web", "operator", "record_day", dict(request_id="actual", data=water(provenance="measured", quality="checked")))
    at.run(timeout=15)
    at.multiselect(key="field_replay_days").set_value([actual["id"]])
    assert not at.checkbox(key=f"field_replay_historical_{parent_id}").value
    at.button(key="field_replay").click().run(timeout=15)
    assert not at.exception and any("allow_historical" in error.value for error in at.error)
    at.checkbox(key=f"field_replay_historical_{parent_id}").check()
    at.button(key="field_replay").click().run(timeout=15)
    assert not at.exception and not at.error
    child_id = at.selectbox(key="field_plan").value
    child = brain.field_workflow("web", "operator", "get", dict(calculation_id=child_id))
    assert child["parent_id"] == parent_id
    assert child["inputs"]["weather"] == parent["inputs"]["weather"]
    assert child["inputs"]["weather_sources"] == parent["inputs"]["weather_sources"]
    assert child["inputs"]["days"][0]["source"] == parent["inputs"]["days"][0]["source"]
    assert actual["id"] in child["inputs"]["days"][0]["water_source"]
    report = "\n".join(element.value for element in at.text)
    assert "Rainfall used: 5 mm (measured, checked" in report and "rain 12 mm" in report
    assert "Net irrigation 3 mm; runoff 1 mm" in report
    assert not at.checkbox(key=f"field_replay_historical_{child_id}").value
    assert brain.field_workflow("web", "operator", "get", dict(calculation_id=parent_id)) == parent
    assert len(brain.field_workflow("web", "operator", "list", {})["water_days"]) == 1


def test_web_replay_does_not_relabel_scenario_weather_as_observed(tmp_path):
    at, brain, _ = filled(tmp_path)
    submit(at)
    parent_id = at.selectbox(key="field_plan").value
    actual = brain.field_workflow("web", "operator", "record_day", dict(request_id="actual", data=water()))
    at.run(timeout=15)
    at.multiselect(key="field_replay_days").set_value([actual["id"]])
    at.checkbox(key=f"field_replay_historical_{parent_id}").check()
    at.button(key="field_replay").click().run(timeout=15)
    assert not at.exception and any("forecast and scenario" in error.value for error in at.error)
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 2
