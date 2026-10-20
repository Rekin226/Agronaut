"""Review, retain, detach and scope linked equipment work through real forms."""

from datetime import date

from agent.tests.test_resource_ui import click, fill_period, start
from agronaut_agent.tests.test_equipment_consultation import example


def prepared(tmp_path):
    at, brain, field, _ = start(tmp_path)
    # Raise the synthetic water budget, then declare the required pump pool.
    at.selectbox(key="resource_pool_choice").set_value("shared-water").run(timeout=15)
    at.text_input(key="resource_pool_shared-water_available").set_value("100000")
    click(at, "Keep resource in draft")
    at.selectbox(key="resource_pool_choice").set_value(None).run(timeout=15)
    at.selectbox(key="resource_pool_new_kind").set_value("pump_time").run(timeout=15)
    for key, value in dict(id="pump", available="24", source="Synthetic pump capacity").items():
        at.text_input(key="resource_pool_new_"+key).set_value(value)
    click(at, "Keep resource in draft")
    brain.select_unit("web", "operator", field["id"])
    source = brain.field_workflow("web", "operator", "save", dict(task="drip_design", inputs=example("drip_design"), request_id="equipment"))
    at.run(timeout=15)
    at.selectbox(key="resource_design_calculation_id").set_value(source["id"]).run(timeout=15)
    at.selectbox(key="resource_design_water").set_value("shared-water").run(timeout=15)
    at.selectbox(key="resource_design_pump").set_value("pump").run(timeout=15)
    for key, value in dict(job_id="irrigate", title="Complete event", source="Synthetic review of date, full field and pump mapping").items():
        at.text_input(key="resource_design_"+key).set_value(value)
    at.date_input(key="resource_design_day").set_value(date(2026, 10, 20))
    at.number_input(key="resource_design_priority").set_value(1)
    click(at, "Preview irrigation job")
    assert not at.error and not at.exception
    return at, brain, field, source


def test_reviewed_import_saves_full_evidence_and_keeps_link_during_job_edits(tmp_path):
    at, brain, field, source = prepared(tmp_path)
    assert at.session_state["resource_jobs"] == {}
    assert brain.farm_resources("web", "operator", field["farm_id"], "list", {})["plans"] == []
    click(at, "Keep reviewed irrigation job in draft")
    job = at.session_state["resource_jobs"]["irrigate"]
    assert job["irrigation_design"]["calculation_id"] == source["id"]
    at.selectbox(key="resource_job_choice").set_value("irrigate").run(timeout=15)
    assert at.text_input(key="resource_job_irrigate_amount_shared-water").disabled
    assert at.number_input(key="resource_job_irrigate_requested_blocks").disabled
    at.text_input(key="resource_job_irrigate_title").set_value("Reviewed full event")
    click(at, "Keep work in draft")
    assert at.session_state["resource_jobs"]["irrigate"]["irrigation_design"] == job["irrigation_design"]
    fill_period(at)
    click(at, "Save resource scenario")
    assert not at.error
    saved = at.session_state["resource_result"]
    assert saved["snapshot"]["irrigation_designs"][0]["calculation"] == source
    at.selectbox(key="resource_saved").set_value(saved["id"]).run(timeout=15)
    assert not at.exception
    assert any("Irrigation source calculations" == exp.label for exp in at.expander)
    assert brain._farms.export(saved["owner_id"])["water_days"] == []


def test_detach_is_explicit_and_requires_an_independent_source(tmp_path):
    at, _, _, _ = prepared(tmp_path)
    click(at, "Keep reviewed irrigation job in draft")
    at.selectbox(key="resource_job_choice").set_value("irrigate").run(timeout=15)
    click(at, "Convert to manual work entry")
    assert "irrigation_design" not in at.session_state["resource_jobs"]["irrigate"]
    assert at.session_state["resource_jobs"]["irrigate"]["source"] == ""
    fill_period(at)
    click(at, "Save resource scenario")
    assert at.error
    at.text_input(key="resource_job_irrigate_source").set_value("Independent supplied manual quantities")
    click(at, "Keep work in draft")
    click(at, "Save resource scenario")
    assert not at.error
    assert "irrigation_designs" not in at.session_state["resource_result"]["snapshot"]


def test_changed_pools_require_another_preview_and_farm_switch_clears_evidence(tmp_path):
    at, brain, _, _ = prepared(tmp_path)
    at.selectbox(key="resource_pool_choice").set_value("pump").run(timeout=15)
    at.text_input(key="resource_pool_pump_available").set_value("12")
    click(at, "Keep resource in draft")
    click(at, "Keep reviewed irrigation job in draft")
    assert any("pools changed" in item.value for item in at.error)
    assert at.session_state["resource_jobs"] == {}
    other = brain.create_unit("web", "operator", dict(farm=dict(name="Other", timezone="UTC"), unit=dict(name="Other field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    at.selectbox(key="resource_farm").set_value(other["farm_id"]).run(timeout=15)
    assert not at.exception
    assert "resource_design_result" not in at.session_state and "resource_design_request" not in at.session_state
