"""Native coupled inputs, exact continuation and scoped drafts without a model call."""

import csv
import json
from copy import deepcopy
from io import BytesIO, StringIO

import pytest
from streamlit.testing.v1 import AppTest

from agent.coupled_water_ui import (
    build_daily_rows,
    build_initial_rows,
    build_weather,
    read_weather_snapshot,
)
from agent.tests import session_keys
from agent.tests.test_field_ui import _SCRIPT, setup
from agronaut_agent.coupled_water_io import evaluate_coupled_water_request
from agronaut_agent.tests.test_coupled_water_workflow import example, next_day

NEW = "field_coupled_new_"
NEXT = "field_coupled_next_"


def csv_rows(rows):
    stream = StringIO()
    csv.writer(stream).writerows(rows)
    return stream.getvalue()


def tables(data):
    patches, layers, evaporation, water, roots, activities = [], [], [], [], [], []
    for patch in data["initial_state"]["patches"]:
        s, e, m = patch["surface_water"], patch["evaporation_parameters"], patch["evaporation_memory"]
        patches.append([patch["id"], patch["area_fraction"], s["stored_mm"], s["capacity_mm"], s["source"]])
        evaporation.append([patch["id"], e["minimum_depth_m"], e["maximum_depth_m"], e["readily_evaporable_mm"], e["decline_factor"],
            m["stage_one_remaining_mm"], m["active_depth_m"], "-" if m["stage_two_start_fraction"] is None else m["stage_two_start_fraction"], e["source"], m["source"]])
        for cell in patch["layers"]:
            layers.append([patch["id"], cell["id"], *[cell[k] for k in ("top_m", "bottom_m", "field_capacity", "wilting_point",
                "saturation", "evaporation_floor", "water_content")], cell["drainage"]["tau"], cell["drainage"]["saturated_conductivity_mm_day"],
                cell["soil_source"], cell["drainage"]["source"]])
    for day in data["days"]:
        for patch in day["patches"]:
            w, e, r, t = patch["water"], patch["evaporation_coefficients"], patch["root_parameters"], patch["transpiration_coefficients"]
            water.append([day["day"], patch["id"], w["rainfall_mm"], w["rainfall_runoff_mm"], w["irrigation_at_surface_mm"], w["source"],
                          e["wet_soil_coefficient"], e["ponded_water_coefficient"], e["source"]])
            roots.append([day["day"], patch["id"], *[r[k] for k in ("root_depth_m", "topsoil_depth_m", "stomatal_threshold", "local_shape_factor",
                "root_zone_aeration_factor", "source")], t["transpiration_coefficient"], t["cold_stress_factor"], t["source"]])
            for activity in patch["root_activities"]:
                activities.append([day["day"], patch["id"], activity["id"], activity["maximum_rate_per_day"], activity["aeration_factor"], activity["source"]])
    weather = [[w["day"], w["et0_mm_day"], "?" if w["rainfall_mm"] is None else w["rainfall_mm"]] for w in data["weather"]]
    return {name: csv_rows(rows) for name, rows in zip(("patch_rows", "layer_rows", "evaporation_rows", "water_rows", "root_rows", "activity_rows", "weather_rows"),
                                                     (patches, layers, evaporation, water, roots, activities, weather))}


def fill_daily(at, data, prefix):
    w = data["weather"][0]
    for key, value in dict(weather_method=w["method"], weather_source=w["source"], issued=w.get("issued_at", ""), daily_source=data["days"][0]["source"]).items():
        at.text_input(key=prefix+key).set_value(value)
    at.selectbox(key=prefix+"weather_kind").set_value(w["kind"])
    for key, value in tables(data).items():
        if key in ("weather_rows", "water_rows", "root_rows", "activity_rows"):
            at.text_area(key=prefix+key).set_value(value)


def filled(tmp_path):
    brain, unit, original = setup(tmp_path)
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    data = example()
    fill_initial(at, data)
    return at, brain, unit, original


def fill_initial(at, data):
    for key, value in dict(crop=data["crop"]["name"], crop_source=data["crop"]["source"], area=data["area_m2"],
        as_of=data["initial_state"]["as_of"], initial_source=data["initial_state"]["source"], conditions_source=data["conditions"]["source"]).items():
        at.text_input(key=NEW+key).set_value(value)
    at.selectbox(key=NEW+"initial_kind").set_value(data["initial_state"]["kind"])
    at.selectbox(key=NEW+"initial_quality").set_value(data["initial_state"]["quality"])
    for key in ("bulk_soil_properties", "no_capillary_rise", "no_frozen_soil", "no_salinity_effects"):
        at.checkbox(key=NEW+key).check()
    for key, value in tables(data).items():
        if key in ("patch_rows", "layer_rows", "evaporation_rows"):
            at.text_area(key=NEW+key).set_value(value)
    fill_daily(at, data, NEW)


def submit_new(at):
    next(b for b in at.button if b.label == "Calculate and save coupled soil water").click().run(timeout=15)
    assert not at.exception


def test_saved_coupled_calculation_renders_separate_water_terms_and_no_legacy_replay(tmp_path):
    brain, _, _ = setup(tmp_path)
    saved = brain.field_workflow("web", "operator", "save", dict(
        task="coupled_soil_water", inputs=example(), request_id="coupled"))
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key="field_plan").set_value(saved["id"]).run(timeout=15)
    assert not at.exception and not at.error
    assert any("Coupled soil-water replay" in option for option in at.selectbox(key="field_plan").options)
    assert any("evaporation 200 L" in t.value and "transpiration 200 L" in t.value for t in at.text)
    assert not any(b.key == "field_replay" for b in at.button)
    assert brain.field_workflow("web", "operator", "get", dict(calculation_id=saved["id"])) == saved


def test_native_save_retry_and_sources_preserve_original_and_do_not_record_irrigation(tmp_path):
    at, brain, _, original = filled(tmp_path)
    submit_new(at)
    assert not at.error
    saved = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
    assert saved["task"] == "coupled_soil_water"
    for key in ("initial_state", "days", "weather", "conditions"):
        assert saved["inputs"][key] == example()[key]
    assert saved["result"]["result"]["totals"]["actual_evapotranspiration_l"]["exact"] == "400/1"
    submit_new(at)
    assert not at.error
    records = brain.field_workflow("web", "operator", "list", {})
    assert len(records["calculations"]) == 2 and records["water_days"] == []
    assert brain.field_workflow("web", "operator", "get", dict(calculation_id=original["id"])) == original


def test_native_continuation_selects_child_clears_draft_and_retries_idempotently(tmp_path):
    at, brain, _, _ = filled(tmp_path)
    submit_new(at)
    parent_id = at.selectbox(key="field_plan").value
    parent = brain.field_workflow("web", "operator", "get", dict(calculation_id=parent_id))
    future = example()
    future["weather"], future["days"] = next_day(future)
    for _ in range(2):
        at.selectbox(key="field_plan").set_value(parent_id).run(timeout=15)
        fill_daily(at, future, NEXT)
        at.text_input(key=NEXT+"reason").set_value("Synthetic next day")
        next(b for b in at.button if b.label == "Continue and save coupled soil water").click().run(timeout=15)
        assert not at.exception and not at.error
        child = brain.field_workflow("web", "operator", "get", dict(calculation_id=at.selectbox(key="field_plan").value))
        assert child["parent_id"] == parent_id
        assert child["inputs"]["initial_state"]["checkpoint"] == parent["result"]["result"]["ending_checkpoint"]
        assert at.text_area(key=NEXT+"water_rows").value == ""
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 3
    assert brain.field_workflow("web", "operator", "get", dict(calculation_id=parent_id)) == parent


def test_missing_amount_applicability_and_forecast_evidence_prevent_save(tmp_path):
    at, brain, _, _ = filled(tmp_path)
    rows = tables(example())["water_rows"]
    at.text_area(key=NEW+"water_rows").set_value(rows.replace(",0,0,0,", ",0,0,,"))
    submit_new(at)
    assert any("Every column" in e.value for e in at.error)
    at.text_area(key=NEW+"water_rows").set_value(rows)
    at.checkbox(key=NEW+"no_capillary_rise").uncheck()
    submit_new(at)
    assert any("no_capillary_rise" in e.value for e in at.error)
    at.checkbox(key=NEW+"no_capillary_rise").check()
    at.selectbox(key=NEW+"weather_kind").set_value("forecast")
    submit_new(at)
    assert any("forecast requires" in e.value for e in at.error)
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 1


def test_continuation_rejects_overlap_and_scope_switch_clears_initial_and_next_drafts(tmp_path):
    at, brain, _, _ = filled(tmp_path)
    submit_new(at)
    fill_daily(at, example(), NEXT)
    at.text_input(key=NEXT+"reason").set_value("Overlapping dates")
    next(b for b in at.button if b.label == "Continue and save coupled soil water").click().run(timeout=15)
    assert not at.exception and any("consecutive" in e.value for e in at.error)
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 2
    brain.create_unit("web", "operator", dict(farm=dict(name="Other", timezone="UTC"), unit=dict(name="Other field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    assert not at.exception
    assert at.text_area(key=NEW+"layer_rows").value == ""
    assert at.selectbox(key=NEW+"initial_kind").value is None
    assert not at.checkbox(key=NEW+"no_capillary_rise").value
    assert not any(key.startswith(NEXT) for key in session_keys(at))


def test_empty_cycle_selection_does_not_resurrect_a_previous_continuation_draft(tmp_path):
    at, brain, _, _ = filled(tmp_path)
    submit_new(at)
    fill_daily(at, example(), NEXT)
    at.text_input(key=NEXT+"reason").set_value("Draft to clear")
    cycle = brain.crop_records("web", "operator", "create", dict(crop="tomato", planted_on="2026-07-01",
        source="Synthetic cycle", request_id="new-cycle"))["cycle"]["id"]
    at.run(timeout=15)
    at.selectbox(key="field_cycle").set_value(cycle).run(timeout=15)
    assert not at.exception
    assert at.text_area(key=NEW+"water_rows").value == ""
    assert at.text_input(key=NEW+"crop").value == "tomato"
    at.selectbox(key="field_cycle").set_value(None).run(timeout=15)
    assert not at.exception
    assert at.text_area(key=NEXT+"water_rows").value == ""
    assert at.text_input(key=NEXT+"reason").value == ""


def test_same_cycle_planting_correction_clears_draft_and_updates_crop_name(tmp_path):
    brain, _, _ = setup(tmp_path)
    cycle = brain.crop_records("web", "operator", "create", dict(crop="tomato", planted_on="2026-07-01",
        source="Synthetic cycle", request_id="cycle"))["cycle"]["id"]
    at = AppTest.from_string(_SCRIPT).run(timeout=15)
    at.selectbox(key="field_cycle").set_value(cycle).run(timeout=15)
    fill_initial(at, example())
    at.run(timeout=15)
    revision = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["planting"]["revision_id"]
    brain.crop_records("web", "operator", "correct_planting", dict(cycle_id=cycle, crop="pepper", planted_on="2026-07-02",
        source="Synthetic correction", reason="Corrected crop", request_id="correct", expected_revision=revision))
    at.run(timeout=15)
    assert not at.exception
    assert at.text_input(key=NEW+"crop").value == "pepper"
    assert at.text_area(key=NEW+"root_rows").value == ""
    assert at.selectbox(key=NEW+"initial_quality").value is None
    assert not at.checkbox(key=NEW+"bulk_soil_properties").value


def test_native_weather_import_requires_a_file_and_keeps_daily_inputs_explicit(tmp_path):
    at, brain, _, _ = filled(tmp_path)
    at.radio(key=NEW+"weather_mode").set_value("Import downloaded weather").run(timeout=15)
    assert not at.exception
    assert at.text_area(key=NEW+"water_rows").value == tables(example())["water_rows"]
    submit_new(at)
    assert any("Choose a downloaded weather snapshot" in e.value for e in at.error)
    assert len(brain.field_workflow("web", "operator", "list", {})["calculations"]) == 1


def test_row_builders_preserve_two_patches_sources_and_unknown_weather_rain():
    data = example()
    second = deepcopy(data["initial_state"]["patches"][0])
    second["id"], second["area_fraction"] = "second", "0.75"
    data["initial_state"]["patches"][0]["area_fraction"] = "0.25"
    data["initial_state"]["patches"].append(second)
    second_day = deepcopy(data["days"][0]["patches"][0])
    second_day["id"] = "second"
    second_day["water"].update(rainfall_mm="3", source="Synthetic rain, separately supplied")
    data["days"][0]["patches"].append(second_day)
    data["weather"][0]["rainfall_mm"] = None
    raw = tables(data)
    initial = data["initial_state"]
    assert build_initial_rows(raw["patch_rows"], raw["layer_rows"], raw["evaporation_rows"],
        **{k: initial[k] for k in ("as_of", "kind", "quality", "source")}) == initial
    assert build_daily_rows(raw["water_rows"], raw["root_rows"], raw["activity_rows"], source=data["days"][0]["source"]) == data["days"]
    w = data["weather"][0]
    assert build_weather(raw["weather_rows"], zone=w["timezone"], kind=w["kind"], method=w["method"], source=w["source"], issued="") == data["weather"]
    assert evaluate_coupled_water_request(data)["totals"]["rainfall_l"]["exact"] == "225/1"


@pytest.mark.parametrize("case", ["duplicate", "missing_activity", "extra_patch", "missing_column", "header"])
def test_incomplete_or_ambiguous_tables_are_rejected(case):
    data = example()
    raw = tables(data)
    if case == "duplicate":
        raw["water_rows"] *= 2
    elif case == "missing_activity":
        raw["activity_rows"] = ""
    elif case == "extra_patch":
        raw["root_rows"] = raw["root_rows"].replace(",plot,", ",other,")
    elif case == "missing_column":
        raw["water_rows"] = "2026-07-06,plot,0"
    else:
        raw["water_rows"] = "day,patch,rainfall_mm,rainfall_runoff_mm,irrigation_at_surface_mm,water_source,wet_soil_coefficient,ponded_water_coefficient,evaporation_source"
    with pytest.raises(ValueError):
        build_daily_rows(raw["water_rows"], raw["root_rows"], raw["activity_rows"], source="Synthetic")


def test_downloaded_weather_import_retains_evidence_and_rejects_tampering_before_calculation():
    from agronaut_agent.tests.test_weather_provider import bundle
    retained = bundle()
    extracted = read_weather_snapshot(BytesIO(json.dumps(retained).encode()))
    data = example()
    data.update(extracted)
    result = evaluate_coupled_water_request(data)
    assert result["inputs"]["weather_sources"] == retained["weather_sources"]
    assert result["totals"]["rainfall_l"]["exact"] == "0/1"
    data["weather"][0]["et0_mm_day"] = 1
    with pytest.raises(ValueError, match="retained provider"):
        evaluate_coupled_water_request(data)
    for upload in (None, BytesIO(b'{"weather": [], "weather": [], "weather_sources": []}'), BytesIO(b'{}')):
        with pytest.raises(ValueError):
            read_weather_snapshot(upload)


@pytest.mark.parametrize("day,hours", [("2026-03-08", 23), ("2026-11-01", 25)])
def test_manual_weather_uses_actual_local_calendar_day_lengths(day, hours):
    result = build_weather(f"{day},5,?", zone="America/New_York", kind="scenario", method="Synthetic", source="Synthetic", issued="")
    assert result[0]["period_hours"] == hours
    assert result[0]["rainfall_mm"] is None
