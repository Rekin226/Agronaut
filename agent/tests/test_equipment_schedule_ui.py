"""Calendar-aware proposals through real forms and explicit booking handoff."""

from agent.tests.test_equipment_calendar_ui import calendar, prepared
from agent.tests.test_resource_ui import click
from agronaut_agent.tests.test_equipment_schedule import clock as clock
from agronaut_agent.tests.test_equipment_schedule import inputs


def ready(tmp_path):
    at, brain, field, timed, pump = prepared(tmp_path)
    data = inputs(timed, pump)["schedule"]
    saved = brain.farm_resources("web", "operator", field["farm_id"], "save_schedule", dict(plan_id=timed["id"], data=data, request_id="ordinary"))
    brain.farm_resources("web", "operator", field["farm_id"], "reserve_equipment",
        dict(data=dict(equipment_ids=[pump["id"]], start="2026-10-20T08:00:00Z", end="2026-10-20T09:00:00Z",
                       title="Maintenance", source="Operator maintenance log"), request_id="maintenance"))
    at.run(timeout=15)
    at.selectbox(key="resource_saved").set_value(saved["id"]).run(timeout=15)
    at.checkbox(key="timing_schedule_equipment_enabled").set_value(True).run(timeout=15)
    for key in ("field", "greenhouse"):
        at.multiselect(key="timing_schedule_equipment_run_"+key).set_value([pump["id"]])
    at.text_input(key="timing_schedule_equipment_source").set_value("Operator reviewed pump suitability")
    at.run(timeout=15)
    assert not at.exception
    return at, brain, field, saved, pump


def test_propose_save_reload_and_explicit_booking_handoff(tmp_path):
    at, brain, field, parent, pump = ready(tmp_path)
    click(at, "Propose start times")
    proposal = at.session_state["timing_schedule_result"]
    assert proposal["schedule_result"]["equipment_checks_pass"]
    assert "09:00:00" in proposal["timing_inputs"]["work"][0]["start"]
    assert len(calendar(brain, field)["active"]) == 1
    click(at, "Save scheduling scenario")
    assert not at.error
    saved = at.session_state["timing_schedule_result"]
    click(at, "Save scheduling scenario")
    assert at.session_state["timing_schedule_result"]["id"] == saved["id"]
    at.selectbox(key="resource_saved").set_value(saved["id"]).run(timeout=15)
    assert at.checkbox(key="timing_schedule_equipment_enabled").value
    assert at.multiselect(key="timing_schedule_equipment_run_field").value == [pump["id"]]
    click(at, "Review equipment booking for saved proposal")
    assert at.selectbox(key="resource_equipment_plan_new").value == saved["id"]
    prefix = f"resource_equipment_timed_new_{saved['id']}_"
    assert at.multiselect(key=prefix+"work_field").value == [pump["id"]]
    assert len(calendar(brain, field)["active"]) == 1
    at.text_input(key=prefix+"source").set_value("Operator adopts reviewed schedule")
    click(at, "Book equipment for scenario")
    assert not at.error and len(calendar(brain, field)["active"]) == 2
    assert brain._farms.export(parent["owner_id"])["resource_commitments"] == []


def test_existing_booking_must_be_selected_and_replacement_stays_pending(tmp_path):
    at, brain, field, parent, pump = ready(tmp_path)
    click(at, "Save scheduling scenario")
    saved = at.session_state["timing_schedule_result"]
    prior = brain.farm_resources("web", "operator", field["farm_id"], "book_equipment",
        dict(plan_id=saved["id"], assignments={w["id"]: [pump["id"]] for w in saved["timing_inputs"]["work"]},
             source="Operator books", accept_partial=False, request_id="book"))
    at.run(timeout=15)
    click(at, "Propose start times")
    assert any("explicitly select" in e.value for e in at.error)
    at.selectbox(key="timing_schedule_equipment_replaces").set_value(prior["id"]).run(timeout=15)
    click(at, "Save scheduling scenario")
    assert not at.error
    replacement = at.session_state["timing_schedule_result"]
    assert replacement["equipment_schedule"]["inputs"]["replaces_booking_id"] == prior["id"]
    assert any(r["id"] == prior["id"] for r in calendar(brain, field)["active"])
    click(at, "Review equipment booking for proposal")
    assert at.selectbox(key="resource_equipment_booking_choice").value == prior["id"]
    assert at.selectbox(key="resource_equipment_plan_"+prior["id"]).value == replacement["id"]
    prefix = f"resource_equipment_timed_{prior['id']}_{replacement['id']}_"
    at.text_input(key=prefix+"reason").set_value("Adopt revised start-time proposal")
    click(at, "Book equipment for scenario")
    assert not at.error
    assert not any(r["id"] == prior["id"] for r in calendar(brain, field)["active"])


def test_stale_item_clears_selection_and_farm_switch_clears_scheduling_context(tmp_path):
    at, brain, field, _, pump = ready(tmp_path)
    brain.farm_resources("web", "operator", field["farm_id"], "save_equipment",
        dict(data=dict(pump["data"], status="unavailable"), request_id="broken", supersedes_id=pump["id"], reason="Breakdown"))
    at.run(timeout=15)
    assert at.multiselect(key="timing_schedule_equipment_run_field").value == []
    click(at, "Propose start times")
    assert at.error
    other = brain.create_unit("web", "operator", dict(farm=dict(name="Other", timezone="UTC"), unit=dict(name="Field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    at.selectbox(key="resource_farm").set_value(other["farm_id"]).run(timeout=15)
    assert not at.exception
    assert "timing_schedule_equipment_run_field" not in at.session_state
    assert "timing_schedule_equipment_enabled" not in at.session_state
