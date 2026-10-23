"""Crop work plans, revisions and explicit completion through the no-model page."""

from datetime import date

from agent.tests.test_crop_ui import planting
from agronaut_agent.tests.test_crop_calendar import task
from agronaut_agent.tests.test_crop_records import harvest


def click(at, label):
    next(b for b in at.button if b.label == label).click().run(timeout=15)
    assert not at.exception


def fill_plan(at, cycle, basis="planting"):
    prefix = f"crop_task_{cycle}_new_"
    at.selectbox(key=prefix+"basis").set_value(basis).run(timeout=15)
    for key in ("entry", "title", "location", "source"):
        at.text_input(key=prefix+key).set_value(task()[key])
    at.text_area(key=prefix+"notes").set_value(task()["notes"])
    at.selectbox(key=prefix+"activity").set_value("harvest")
    at.selectbox(key=prefix+"priority").set_value("normal")
    if basis == "planting":
        at.number_input(key=prefix+"start_offset_days").set_value(5)
        at.number_input(key=prefix+"end_offset_days").set_value(7)
    else:
        at.date_input(key=prefix+"start").set_value(date(2026, 7, 6))
        at.date_input(key=prefix+"end").set_value(date(2026, 7, 8))
    return prefix


def choose_resolution(at, cycle, row, status):
    at.selectbox(key=f"crop_task_resolution_{cycle}").set_value(row["id"]).run(timeout=15)
    prefix = f"crop_task_resolution_{row['id']}_"
    at.selectbox(key=prefix+"status").set_value(status).run(timeout=15)
    at.text_input(key=prefix+"reason").set_value("Synthetic explicit operator update")
    at.text_input(key=prefix+"source").set_value("Synthetic notebook")
    if status == "completed":
        at.date_input(key=prefix+"completed_on").set_value(date(2026, 7, 6))
    return prefix


def test_relative_plan_reassessed_after_planting_change_and_status_history(tmp_path):
    at, brain, _, cycle = planting(tmp_path)
    def call(operation, **data):
        return brain.crop_records("web", "operator", operation, data)
    fill_plan(at, cycle)
    click(at, "Save work plan")
    assert not at.error
    before = call("get", cycle_id=cycle)
    planned = before["records"][-1]
    assert before["harvest_totals"] == []
    call("correct_planting", cycle_id=cycle, crop="pepper", planted_on="2026-07-02", reason="Date typo",
         source="Synthetic correction", request_id="amend", expected_revision=before["planting"]["revision_id"])
    at.run(timeout=15)
    table = next(f.value for f in at.dataframe if "originally_planned_start" in f.value.columns)
    assert table.iloc[0]["due_start"] == "2026-07-07"
    assert table.iloc[0]["originally_planned_start"] == "2026-07-06"
    assert table.iloc[0]["needs_review"]
    at.selectbox(key=f"crop_task_plan_{cycle}").set_value(planned["id"]).run(timeout=15)
    prefix = f"crop_task_{cycle}_{planned['id']}_"
    assert at.text_input(key=prefix+"entry").disabled
    at.text_input(key=prefix+"source").set_value("Synthetic reviewed schedule")
    click(at, "Save work plan")
    assert not at.error
    journal = call("get", cycle_id=cycle)
    assert not journal["calendar"]["tasks"][0]["needs_review"]
    for status in ("completed", "planned", "cancelled", "planned"):
        choose_resolution(at, cycle, journal["records"][-1], status)
        click(at, "Save task status")
        assert not at.error
        journal = call("get", cycle_id=cycle)
        assert journal["calendar"]["tasks"][0]["status"] == status
        assert journal["harvest_totals"] == []
    assert len([r for r in journal["records"] if r["kind"] == "calendar"]) == 6


def test_completion_link_keeps_actual_revision_and_flags_corrected_evidence(tmp_path):
    at, brain, _, cycle = planting(tmp_path)
    def call(operation, **data):
        return brain.crop_records("web", "operator", operation, data)
    fill_plan(at, cycle, "date")
    click(at, "Save work plan")
    planned = call("get", cycle_id=cycle)["records"][-1]
    actual = call("record_harvest", cycle_id=cycle, data=harvest(), request_id="actual")
    at.run(timeout=15)
    prefix = choose_resolution(at, cycle, planned, "completed")
    at.selectbox(key=prefix+"evidence").set_value(actual["id"])
    click(at, "Save task status")
    assert not at.error
    completed = call("get", cycle_id=cycle)["records"][-1]
    assert completed["data"]["resolution"]["completion_record"]["snapshot"] == actual
    call("record_harvest", cycle_id=cycle, data=harvest(quantity=12), request_id="correct", supersedes_id=actual["id"])
    at.run(timeout=15)
    assert not at.exception
    table = next(f.value for f in at.dataframe if "originally_planned_start" in f.value.columns)
    assert table.iloc[0]["evidence"] == "superseded" and table.iloc[0]["needs_review"]
    assert call("get", cycle_id=cycle)["harvest_totals"][0]["quantity"] == "12"


def test_stale_actual_and_task_forms_fail_without_creating_completion(tmp_path):
    at, brain, _, cycle = planting(tmp_path)
    def call(operation, **data):
        return brain.crop_records("web", "operator", operation, data)
    planned = call("plan_task", cycle_id=cycle, data=task(), request_id="plan")
    actual = call("record_harvest", cycle_id=cycle, data=harvest(), request_id="actual")
    at.run(timeout=15)
    prefix = choose_resolution(at, cycle, planned, "completed")
    at.selectbox(key=prefix+"evidence").set_value(actual["id"])
    call("void_harvest", record_id=actual["id"], reason="Wrong batch", request_id="void")
    click(at, "Save task status")
    assert at.error and not at.exception
    assert call("get", cycle_id=cycle)["calendar"]["tasks"][0]["status"] == "planned"
    assert at.selectbox(key=prefix+"evidence").value is None
    assert any("record changed" in w.value for w in at.warning)
    call("resolve_task", record_id=planned["id"], status="cancelled", reason="Cancelled elsewhere",
         source="Synthetic", request_id="cancel")
    click(at, "Save task status")
    assert at.error
    assert call("get", cycle_id=cycle)["calendar"]["tasks"][0]["status"] == "cancelled"


def test_incomplete_window_is_rejected_and_unit_switch_clears_plan(tmp_path):
    at, brain, _, cycle = planting(tmp_path)
    prefix = fill_plan(at, cycle)
    at.number_input(key=prefix+"start_offset_days").set_value(None)
    click(at, "Save work plan")
    assert at.error
    assert brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["calendar"]["tasks"] == []
    brain.create_unit("web", "operator", {"farm": {"name": "Other", "timezone": "UTC"},
        "unit": {"name": "Other soil", "production_system": "soil", "medium": "soil",
                 "environment": "open_field", "water_strategy": "rainfed"}})
    at.run(timeout=15)
    assert not at.exception
    assert prefix+"title" not in at.session_state
    assert not any(w.label == "Save work plan" for w in at.button)


def test_imported_early_date_window_remains_visible_and_can_be_revised(tmp_path):
    at, brain, _, cycle = planting(tmp_path)
    planned = brain.crop_records("web", "operator", "plan_task", dict(cycle_id=cycle,
        data=task(due=dict(basis="date", start="0001-01-01", end="0001-01-02")), request_id="early"))
    at.run(timeout=15)
    at.selectbox(key=f"crop_task_plan_{cycle}").set_value(planned["id"]).run(timeout=15)
    assert not at.exception
    prefix = f"crop_task_{cycle}_{planned['id']}_"
    assert at.date_input(key=prefix+"start").value is None
    table = next(f.value for f in at.dataframe if "originally_planned_start" in f.value.columns)
    assert table.iloc[0]["originally_planned_start"] == "0001-01-01"
    at.date_input(key=prefix+"start").set_value(date(2026, 7, 6))
    at.date_input(key=prefix+"end").set_value(date(2026, 7, 8))
    at.text_input(key=prefix+"source").set_value("Corrected imported dates")
    click(at, "Save work plan")
    assert not at.error
    calendar = brain.crop_records("web", "operator", "get", dict(cycle_id=cycle))["calendar"]
    assert calendar["tasks"][0]["effective_window"]["start"] == "2026-07-06"


def test_linked_task_source_is_saved_and_unavailable_selection_is_not_silently_removed(tmp_path):
    from agronaut_agent.tests.test_task_triggers import scouting

    at, brain, _, cycle = planting(tmp_path)
    def call(operation, **data):
        return brain.crop_records('web','operator',operation,data)
    source = call('record_scouting',cycle_id=cycle,data=scouting(),request_id='scout')
    at.run(timeout=15)
    prefix = fill_plan(at,cycle)
    selected = 'crop_record:'+source['id']
    at.selectbox(key=prefix+'trigger').set_value(selected)
    at.text_area(key=prefix+'trigger_reason').set_value('Inspect because leaves changed')
    click(at,'Save work plan')
    assert not at.error
    row = call('get',cycle_id=cycle)['records'][-1]
    assert row['data']['trigger_snapshot'] == source
    call('void_observation',record_id=source['id'],reason='Wrong row',request_id='void')
    at.run(timeout=15)
    table = next(f.value for f in at.dataframe if 'trigger_state' in f.value.columns)
    assert table.iloc[0]['needs_review'] and table.iloc[0]['trigger_state'] == 'superseded'
    at.selectbox(key=f'crop_task_plan_{cycle}').set_value(row['id']).run(timeout=15)
    prefix = f"crop_task_{cycle}_{row['id']}_"
    assert at.selectbox(key=prefix+'trigger').value == selected
    at.text_input(key=prefix+'source').set_value('Reviewed source issue')
    click(at,'Save work plan')
    assert at.error and 'no longer available' in at.error[0].value
    assert call('get',cycle_id=cycle)['calendar']['tasks'][0]['record_id'] == row['id']
    at.selectbox(key=prefix+'trigger').set_value('none')
    click(at,'Save work plan')
    assert not at.error, [e.value for e in at.error]
    assert call('get',cycle_id=cycle)['calendar']['tasks'][0]['trigger'] is None


def test_measurement_source_review_changed_after_form_render_is_rejected(tmp_path):
    from agronaut_agent.tests.test_measurement_entry import reading

    at, brain, _, cycle = planting(tmp_path)
    def call(operation, **data):
        return brain.crop_records('web','operator',operation,data)
    source = call('record_measurement',cycle_id=cycle,data=reading(),request_id='reading')
    at.run(timeout=15)
    prefix = fill_plan(at,cycle)
    at.selectbox(key=prefix+'trigger').set_value('measurement:'+source['id'])
    at.text_area(key=prefix+'trigger_reason').set_value('Check the gauge')
    changed = call('review_measurement',observation_id=source['id'],quality='checked',reason='Verified notebook',source='Operator',request_id='review',expected_revision=None)
    click(at,'Save work plan')
    assert at.error and 'review changed' in at.error[0].value
    assert call('get',cycle_id=cycle)['calendar']['tasks'] == []
    click(at,'Save work plan')
    assert not at.error
    row = call('get',cycle_id=cycle)['records'][-1]
    assert row['data']['trigger']['review_revision_id'] == changed['id']
