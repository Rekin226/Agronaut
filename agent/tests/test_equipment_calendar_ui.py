"""Equipment booking, conflict correction and scope changes in the native forms."""

from agent.tests.test_resource_timing_ui import start
from agent.tests.test_resource_ui import click
from agronaut_agent.tests.test_equipment_calendar import clock as clock


def calendar(brain, field):
    return brain.farm_resources("web", "operator", field["farm_id"], "equipment_calendar", {})


def prepared(tmp_path):
    at, brain, field, parent, timing = start(tmp_path)
    timed = brain.farm_resources("web", "operator", field["farm_id"], "save_timed", dict(plan_id=parent["id"], data=timing, request_id="timed"))
    at.run(timeout=15)
    prefix = "resource_equipment_asset_new_"
    for key, value in dict(equipment_key="pump-a", name="Field pump", source="Operator inventory").items():
        at.text_input(key=prefix+key).set_value(value)
    at.selectbox(key=prefix+"kind").set_value("pump")
    click(at, "Save physical equipment")
    assert not at.error
    pump = calendar(brain, field)["equipment"][0]
    return at, brain, field, timed, pump


def fill_plan(at, timed, pump):
    at.selectbox(key="resource_equipment_plan_new").set_value(timed["id"]).run(timeout=15)
    prefix = f"resource_equipment_timed_new_{timed['id']}_"
    for w in timed["timing_inputs"]["work"]:
        at.multiselect(key=prefix+"work_"+w["id"]).set_value([pump["id"]])
    at.text_input(key=prefix+"source").set_value("Operator reviewed suitable pump and full intervals")


def fill_block(at, pump, start="2026-10-20T08:30:00Z", end="2026-10-20T09:30:00Z"):
    at.radio(key="resource_equipment_mode_new").set_value("Calendar block").run(timeout=15)
    prefix = "resource_equipment_block_new_"
    at.multiselect(key=prefix+"equipment_ids").set_value([pump["id"]])
    for key, value in dict(start=start, end=end, title="Maintenance", source="Synthetic maintenance diary").items():
        at.text_input(key=prefix+key).set_value(value)


def test_register_book_retry_conflict_adjacent_block_and_release(tmp_path):
    at, brain, field, timed, pump = prepared(tmp_path)
    click(at, "Save physical equipment")
    assert not at.error and len(calendar(brain, field)["equipment_history"]) == 1
    fill_plan(at, timed, pump)
    click(at, "Book equipment for scenario")
    assert not at.error
    first = calendar(brain, field)["active"][0]
    assert len(first["intervals"]) == 2
    click(at, "Book equipment for scenario")
    assert not at.error and len(calendar(brain, field)["bookings"]) == 1
    at.selectbox(key="resource_equipment_booking_choice").set_value(None).run(timeout=15)
    fill_block(at, pump)
    click(at, "Save equipment calendar block")
    assert any("equipment conflict" in e.value for e in at.error)
    assert len(calendar(brain, field)["active"]) == 1
    at.text_input(key="resource_equipment_block_new_start").set_value("2026-10-20T10:00:00Z")
    at.text_input(key="resource_equipment_block_new_end").set_value("2026-10-20T11:00:00Z")
    click(at, "Save equipment calendar block")
    assert not at.error and len(calendar(brain, field)["active"]) == 2
    at.selectbox(key="resource_equipment_booking_choice").set_value(first["id"]).run(timeout=15)
    prefix = f"resource_equipment_release_{first['id']}_"
    at.text_input(key=prefix+"source").set_value("Operator")
    at.text_input(key=prefix+"reason").set_value("Work cancelled")
    click(at, "Release equipment booking")
    assert not at.error and len(calendar(brain, field)["active"]) == 1
    ledger = brain.farm_resources("web", "operator", field["farm_id"], "ledger", {})
    assert ledger["commitments"] == []
    exported = brain._farms.export(timed["owner_id"])
    assert exported["water_days"] == exported["crop_records"] == []
    assert any(b.label == "Download equipment calendar history" for b in at.get("download_button"))


def test_stale_equipment_revision_requires_explicit_reselection_and_old_booking_stays(tmp_path):
    at, brain, field, timed, pump = prepared(tmp_path)
    fill_plan(at, timed, pump)
    click(at, "Book equipment for scenario")
    prior = calendar(brain, field)["active"][0]
    revised = brain.farm_resources("web", "operator", field["farm_id"], "save_equipment",
        dict(data=dict(pump["data"], status="unavailable"), supersedes_id=pump["id"], reason="Breakdown", request_id="broken"))
    at.run(timeout=15)
    prefix = f"resource_equipment_timed_{prior['id']}_{timed['id']}_"
    assert at.multiselect(key=prefix+"work_field").value == []
    assert any("needs review" in w.value for w in at.warning)
    at.text_input(key=prefix+"reason").set_value("Try booking again")
    click(at, "Book equipment for scenario")
    assert at.error and calendar(brain, field)["active"][0]["id"] == prior["id"]
    assert calendar(brain, field)["equipment"][0]["id"] == revised["id"]
    # Release remains possible even when the original equipment is unavailable.
    prefix = f"resource_equipment_release_{prior['id']}_"
    at.text_input(key=prefix+"source").set_value("Operator")
    at.text_input(key=prefix+"reason").set_value("Cancel during repair")
    click(at, "Release equipment booking")
    assert not at.error and calendar(brain, field)["active"] == []


def test_farm_switch_clears_equipment_editor_and_keeps_history_scoped(tmp_path):
    at, brain, field, timed, pump = prepared(tmp_path)
    fill_block(at, pump)
    other = brain.create_unit("web", "operator", dict(farm=dict(name="Other farm", timezone="UTC"), unit=dict(name="Other field",
        production_system="soil", medium="soil", environment="open_field", water_strategy="rainfed")))
    at.run(timeout=15)
    at.selectbox(key="resource_farm").set_value(other["farm_id"]).run(timeout=15)
    assert not at.exception
    assert at.selectbox(key="resource_equipment_choice").value is None
    assert at.text_input(key="resource_equipment_asset_new_equipment_key").value == ""
    assert calendar(brain, other)["equipment"] == []
    assert len(calendar(brain, field)["equipment"]) == 1
    assert calendar(brain, field)["active"] == []
