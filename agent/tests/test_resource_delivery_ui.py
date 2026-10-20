"""Outlet limits through native timing and scheduling forms without a model."""

from agent.tests.test_resource_schedule_ui import fill_schedule
from agent.tests.test_resource_timing_ui import fill, start
from agent.tests.test_resource_ui import click
from agro_model.tests.test_resource_delivery import delivery, free_counts


def fill_delivery(at, rate="500"):
    at.checkbox(key="timing_delivery_enabled").check().run(timeout=15)
    for key, value in delivery(rate)[0]["windows"][0].items():
        at.text_input(key="timing_delivery:tank_new_"+key).set_value(value)
    click(at, "Keep delivery window for tank in draft")


def test_timing_failure_edit_save_reload_and_explicit_disable(tmp_path):
    at, brain, field, parent, timing = start(tmp_path)
    fill(at, timing)
    fill_delivery(at, "499")
    click(at, "Save timed scenario")
    assert not at.error and not at.exception
    failed = at.session_state["timing_result"]
    assert not failed["timing_result"]["declared_checks_pass"]
    at.selectbox(key="resource_saved").set_value(failed["id"]).run(timeout=15)
    assert at.checkbox(key="timing_delivery_enabled").value
    at.selectbox(key="timing_choice_delivery:tank").set_value("outlet").run(timeout=15)
    assert at.text_input(key="timing_delivery:tank_outlet_max_flow_lph").value == "499"
    at.text_input(key="timing_delivery:tank_outlet_max_flow_lph").set_value("500")
    click(at, "Keep delivery window for tank in draft")
    click(at, "Save timed scenario")
    assert not at.error and not at.exception
    saved = at.session_state["timing_result"]
    assert saved["timing_result"]["declared_checks_pass"]
    at.selectbox(key="resource_saved").set_value(saved["id"]).run(timeout=15)
    at.checkbox(key="timing_delivery_enabled").uncheck().run(timeout=15)
    click(at, "Save timed scenario")
    unchecked = at.session_state["timing_result"]
    assert unchecked["timing_inputs"]["water_delivery"] == []
    assert not unchecked["timing_result"]["water_delivery_checked"]
    at.multiselect(key="resource_compare").set_value([saved["id"], unchecked["id"]]).run(timeout=15)
    table = next(f.value for f in at.dataframe if "water_delivery_checked" in f.value.columns)
    assert list(table["water_delivery_checked"]) == [True, False]
    assert brain._farms.export(parent["owner_id"])["water_days"] == []


def test_schedule_uses_limits_reloads_and_retry_does_not_duplicate(tmp_path):
    at, brain, field, parent, timing = start(tmp_path)
    free_counts(timing)
    fill_schedule(at, timing)
    fill_delivery(at)
    click(at, "Save scheduling scenario")
    assert not at.error and not at.exception
    saved = at.session_state["timing_schedule_result"]
    assert saved["schedule_result"]["delivery_pruned"] > 0
    assert saved["timing_result"]["declared_checks_pass"]
    click(at, "Save scheduling scenario")
    assert at.session_state["timing_schedule_result"]["id"] == saved["id"]
    at.selectbox(key="resource_saved").set_value(saved["id"]).run(timeout=15)
    assert at.session_state["timing_rows"]["delivery:tank"]["outlet"]["max_flow_lph"] == "500"
    click(at, "Propose start times")
    assert at.session_state["timing_schedule_result"]["timing_inputs"] == saved["timing_inputs"]
    other = brain.create_unit("web", "operator", dict(farm=dict(name="Other", timezone="UTC"), unit=dict(name="Field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    at.selectbox(key="resource_farm").set_value(other["farm_id"]).run(timeout=15)
    assert "timing_rows" not in at.session_state and "timing_delivery_enabled" not in at.session_state


def test_empty_windows_mean_closed_and_invalid_source_does_not_save(tmp_path):
    at, brain, field, _, timing = start(tmp_path)
    fill_schedule(at, timing)
    at.checkbox(key="timing_delivery_enabled").check().run(timeout=15)
    click(at, "Propose start times")
    assert at.session_state["timing_schedule_result"]["schedule_result"]["status"] == "no_feasible_schedule_on_grid"
    fill_delivery(at)
    at.selectbox(key="timing_choice_delivery:tank").set_value("outlet").run(timeout=15)
    at.text_input(key="timing_delivery:tank_outlet_source").set_value("")
    click(at, "Keep delivery window for tank in draft")
    assert "timing_schedule_result" not in at.session_state
    click(at, "Save scheduling scenario")
    assert at.error and not at.exception
    assert len(brain.farm_resources("web", "operator", field["farm_id"], "list", {})["plans"]) == 1
