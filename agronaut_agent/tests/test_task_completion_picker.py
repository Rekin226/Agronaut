"""Discover only matching actual records; selecting them never creates quantities."""
import json

import pytest

from agronaut_agent.crop_records import CropRecords
from agronaut_agent.store import _Db
from agronaut_agent.task_consultation import TaskConsultation, render_task_intake
from agronaut_agent.tests.test_crop_calendar import task
from agronaut_agent.tests.test_crop_records import harvest
from agronaut_agent.tests.test_fertilizer_records import application
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_field_workflow import water
from agronaut_agent.tests.test_measurement_entry import reading
from agronaut_agent.tests.test_task_consultation import finish
from agronaut_agent.tests.test_task_source_browser import answer
from agronaut_agent.tests.test_task_triggers import scouting


def start(workflow, activity='harvest', language='en', **changes):
    row = CropRecords(workflow.db).plan_task('owner', 'field', 'cycle', task(activity=activity, **changes), request_id='task')
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard, dict(action='completed', record_id=row['id'], completed_on='2026-07-06'), target='evidence', language=language)
    return wizard, view


@pytest.mark.parametrize('language', ['en', 'fr', 'zh-Hant'])
@pytest.mark.parametrize('activity', ['harvest', 'scouting', 'stage_check', 'transplant', 'fertilizer', 'irrigation'])
def test_matching_actual_is_selectable_previewed_and_preserved_without_new_quantities(setup, language, activity):
    workflow, farms = setup
    records = CropRecords(workflow.db)
    kind = 'crop_record'
    if activity == 'harvest':
        actual = records.record_harvest('owner', 'field', 'cycle', harvest(), request_id='actual')
    elif activity == 'scouting':
        actual = records.record_scouting('owner', 'field', 'cycle', scouting(), request_id='actual')
    elif activity == 'stage_check':
        actual = records.record_stage('owner', 'field', 'cycle', dict(entry='stage', day='2026-07-06', location='North',
            stage='Flowering', source='Operator', provenance='observed'), request_id='actual')
    elif activity == 'transplant':
        actual = records.record_transplant('owner', 'field', 'cycle', dict(entry='transplant', day='2026-07-06', location='North',
            origin='Nursery', plant_count=20, source='Operator', provenance='reported'), request_id='actual')
    elif activity == 'fertilizer':
        actual = records.record_fertilizer('owner', 'field', 'cycle', application(), request_id='actual')
    else:
        kind = 'water_day'
        actual = workflow.record_day('owner', 'field', water(), cycle_id='cycle', request_id='actual')
    wizard, view = start(workflow, activity, language)
    options = view['evidence_options']
    assert options['total'] == 1 and options['items'][0]['id'] == actual['id']
    assert options['items'][0]['kind'] == kind
    assert 'refresh' in render_task_intake(view)
    before = farms.export('owner')
    view = answer(wizard, view, '1')
    assert view['answers']['evidence_snapshot'] == actual and view['evidence_options'] is None
    assert actual['data']['provenance'] in render_task_intake(view)
    view = finish(wizard, dict(reason='Operator confirms work'), language=language)
    result = wizard.save('owner', 'field', view['draft_id'], view['revision'])
    assert result['record']['data']['resolution']['completion_record'] == dict(kind=kind, id=actual['id'], snapshot=actual)
    assert wizard.save('owner', 'field', view['draft_id'], view['revision']) == result
    after = farms.export('owner')
    assert after['water_days'] == before['water_days']
    assert [r for r in after['crop_records'] if r['kind'] != 'calendar'] == [r for r in before['crop_records'] if r['kind'] != 'calendar']


def test_filters_wrong_activity_date_scope_voids_and_measurements(setup):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    records.record_harvest('owner', 'field2', 'cycle2', harvest(), request_id='other-unit')
    records.record_harvest('other', 'field3', 'cycle3', harvest(), request_id='foreign')
    other_cycle = records.create('owner', 'field', 'pepper', '2026-07-01', 'Operator', request_id='other-cycle')['cycle']['id']
    records.record_harvest('owner', 'field', other_cycle, harvest(), request_id='other-cycle-harvest')
    records.record_harvest('owner', 'field', 'cycle', harvest(day='2026-07-07'), request_id='wrong-day')
    records.record_scouting('owner', 'field', 'cycle', scouting(), request_id='wrong-activity')
    records.record_measurement('owner', 'field', reading(provenance='forecast'), cycle_id='cycle', request_id='forecast')
    records.record_measurement('owner', 'field', reading(), cycle_id='cycle', request_id='reading')
    voided = records.record_harvest('owner', 'field', 'cycle', harvest(batch='voided'), request_id='voided')
    records.void_harvest('owner', 'field', voided['id'], 'Wrong batch', request_id='void')
    wizard, view = start(workflow)
    assert view['evidence_options'] == dict(page=1, pages=1, total=0, items=[])
    assert 'No matching actual' in view['question']
    with pytest.raises(ValueError, match='displayed'):
        answer(wizard, view, '1')
    view = answer(wizard, view, 'none')
    assert 'evidence_snapshot' not in view['answers']
    view = finish(wizard, dict(reason='Operator report only'))
    result = wizard.save('owner', 'field', view['draft_id'], view['revision'])
    assert result['record']['data']['resolution']['completion_record'] is None


@pytest.mark.parametrize('quality,amount', [('unverified', 0), ('rejected', 3)])
def test_zero_or_rejected_irrigation_is_not_completion_evidence(setup, quality, amount):
    workflow, _ = setup
    workflow.record_day('owner', 'field', water(quality=quality, net_irrigation_mm=amount), cycle_id='cycle', request_id='water')
    _, view = start(workflow, 'irrigation')
    assert view['evidence_options']['total'] == 0


@pytest.mark.parametrize('activity', ['maintenance', 'other'])
def test_unmapped_activities_offer_operator_report_without_fabricated_evidence(setup, activity):
    workflow, _ = setup
    CropRecords(workflow.db).record_harvest('owner', 'field', 'cycle', harvest(), request_id='harvest')
    _, view = start(workflow, activity)
    assert view['evidence_options']['total'] == 0


@pytest.mark.parametrize('kind', ['crop_record', 'water_day'])
def test_corrected_display_is_rejected_refresh_selects_new_revision_and_later_change_blocks_save(setup, kind):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    def record(request, prior=None):
        if kind == 'crop_record':
            return records.record_harvest('owner', 'field', 'cycle', harvest(), request_id=request, supersedes_id=prior)
        return workflow.record_day('owner', 'field', water(), cycle_id='cycle', request_id=request, supersedes_id=prior)
    original = record('original')
    wizard, view = start(workflow, 'harvest' if kind == 'crop_record' else 'irrigation')
    corrected = record('correction', original['id'])
    with pytest.raises(ValueError, match='Completion records changed; reply refresh'):
        answer(wizard, view, '1')
    assert wizard.get('owner', 'field') == view
    view = answer(wizard, view, 'refresh')
    assert view['evidence_options']['items'][0]['id'] == corrected['id']
    view = answer(wizard, view, '1')
    view = finish(wizard, dict(reason='Completed'))
    latest = record('later', corrected['id'])
    with pytest.raises(ValueError, match='completion record must be current'):
        wizard.save('owner', 'field', view['draft_id'], view['revision'])
    while view['next_field'] != 'evidence':
        view = wizard.answer('owner', 'field', '', view['revision'], back=True)
    assert 'evidence_snapshot' not in view['answers']
    assert view['evidence_options']['items'][0]['id'] == latest['id']
    view = wizard.answer('owner', 'field', '', view['revision'], back=True)
    assert view['next_field'] == 'completed_on' and view['evidence_options'] is None
    view = answer(wizard, view, '2026-07-07')
    assert view['evidence_options']['total'] == 0


def test_paging_restart_and_refresh_preserve_identity_and_owned_export_erasure(setup):
    workflow, farms = setup
    records = CropRecords(workflow.db)
    for i in range(7):
        records.record_harvest('owner', 'field', 'cycle', harvest(batch=f'batch-{i}'), request_id=f'h-{i}')
    wizard, view = start(workflow)
    before = view
    view = answer(wizard, view, 'next')
    assert view['evidence_options']['page'] == 2 and view['answers'] == before['answers']
    with pytest.raises(ValueError, match='draft changed'):
        answer(wizard, before, '1')
    with pytest.raises(ValueError, match='displayed'):
        answer(wizard, view, '1')
    selected = view['evidence_options']['items'][0]
    reopened = _Db(workflow.db.path)
    try:
        assert TaskConsultation(reopened).get('owner', 'field') == view
    finally:
        reopened._conn.close()
    records.record_harvest('owner', 'field', 'cycle', harvest(batch='added'), request_id='added')
    view = wizard.start('owner', 'field', 'fr')
    assert view['evidence_options']['items'][0] == selected
    assert len(farms.export('owner')['task_intakes'][0]['evidence_browser']['items']) == 7
    view = answer(wizard, view, '6')
    assert view['answers']['evidence_snapshot']['id'] == selected['id']
    foreign = wizard.start('other', 'field3')
    farms.purge('owner')
    assert not farms.export('owner')['task_intakes']
    assert wizard.get('other', 'field3') == foreign


@pytest.mark.parametrize('language', ['en', 'fr', 'zh-Hant'])
def test_evidence_page_bounds_long_task_and_record_text(setup, language):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    for index in range(6):
        records.record_scouting('owner', 'field', 'cycle', scouting(entry=f'scout-{index}',
            location='🌱'*300, symptoms='\nnext\n'+'🌱'*500), request_id=f'scout-{index}')
    _, view = start(workflow, 'scouting', language, title='🌱'*1000)
    rendered = render_task_intake(view)
    assert len(view['evidence_options']['items']) == 5
    assert len(rendered.encode('utf-16-le'))//2 <= 3500
    assert '\nnext\n' not in rendered


def test_v4_draft_keeps_full_reference_completion_selection(setup):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    actual = records.record_harvest('owner', 'field', 'cycle', harvest(), request_id='actual')
    row = records.plan_task('owner', 'field', 'cycle', task(), request_id='task')
    wizard = TaskConsultation(workflow.db)
    wizard.start('owner', 'field')
    payload = json.loads(workflow.db.query('SELECT payload FROM task_intake_drafts')[0][0])
    payload['intake_version'] = 4
    workflow.db.execute('UPDATE task_intake_drafts SET payload=?', (json.dumps(payload),))
    view = finish(wizard, dict(action='completed', record_id=row['id'], completed_on='2026-07-06'), target='evidence')
    assert view['evidence_options'] is None
    view = answer(wizard, view, 'crop_record:'+actual['id'])
    assert view['next_field'] == 'reason' and 'evidence_snapshot' not in view['answers']
