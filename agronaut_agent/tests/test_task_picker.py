"""Saved work is discoverable without relying on the attention-only overview."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agronaut_agent.crop_records import CropRecords
from agronaut_agent.farm_overview import FarmOverview
from agronaut_agent.store import _Db
from agronaut_agent.task_consultation import TaskConsultation, render_task_intake
from agronaut_agent.tests.test_crop_calendar import task
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_task_consultation import finish
from agronaut_agent.tests.test_task_source_browser import answer


def planned(records, index=0, **changes):
    return records.plan_task('owner', 'field', 'cycle', task(entry=f'work-{index}', **changes), request_id=f'plan-{index}')


@pytest.mark.parametrize('language', ['en', 'fr', 'zh-Hant'])
@pytest.mark.parametrize('action', ['completed', 'cancelled', 'reopen'])
def test_numbered_task_selection_reaches_work_missing_from_attention_overview(setup, language, action):
    workflow, farms = setup
    records = CropRecords(workflow.db)
    row = planned(records, due=dict(basis='date', start='2099-01-01', end='2099-01-02'))
    if action == 'reopen':
        row = records.resolve_task('owner', 'field', row['id'], 'cancelled', 'Postponed', 'Operator', request_id='cancel')
    overview = FarmOverview(workflow.db).get('owner', 'farm', now=datetime(2026,7,7,tzinfo=timezone.utc))
    assert overview['farms'][0]['units'][0]['cycles'][0]['attention'] == []
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard, dict(action=action), target='record_id', language=language)
    assert view['task_options']['items'][0]['id'] == row['id']
    assert view['task_options']['items'][0]['status'] == row['data']['status']
    assert row['data']['title'] in view['question']
    assert 'refresh' in render_task_intake(view)
    before = farms.export('owner')['crop_records']
    view = finish(wizard, dict(action=action, record_id='1', completed_on='2026-07-06', evidence='none',
        reason='Operator report'), language=language)
    assert view['answers']['task_snapshot'] == row and view['task_options'] is None
    assert farms.export('owner')['crop_records'] == before
    result = wizard.save('owner', 'field', view['draft_id'], view['revision'])
    assert result['record']['supersedes_id'] == row['id']
    assert result['record']['data']['status'] == ('planned' if action == 'reopen' else action)
    assert wizard.save('owner', 'field', view['draft_id'], view['revision']) == result
    assert farms.export('owner')['water_days'] == []


def test_paging_and_resume_keep_numbers_and_exclude_other_units_and_owners(setup):
    workflow, farms = setup
    records = CropRecords(workflow.db)
    rows = [planned(records, i) for i in range(7)]
    records.plan_task('owner', 'field2', 'cycle2', task(), request_id='other-unit')
    records.plan_task('other', 'field3', 'cycle3', task(), request_id='foreign')
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard, dict(action='cancelled'), target='record_id')
    assert view['task_options']['total'] == 7
    assert [item['id'] for item in view['task_options']['items']] == [row['id'] for row in rows[:5]]
    cached = farms.export('owner')['task_intakes'][0]['task_browser']['items']
    assert {item['id'] for item in cached} == {row['id'] for row in rows}
    assert not farms.export('other')['task_intakes']
    old = view
    view = answer(wizard, view, 'next')
    assert view['answers'] == old['answers'] and view['task_options']['page'] == 2
    with pytest.raises(ValueError, match='draft changed'):
        answer(wizard, old, '1')
    with pytest.raises(ValueError, match='displayed'):
        answer(wizard, view, '1')
    with pytest.raises(ValueError, match='No page'):
        answer(wizard, view, 'next')
    reopened = _Db(workflow.db.path)
    try:
        assert TaskConsultation(reopened).get('owner', 'field') == view
    finally:
        reopened._conn.close()
    planned(records, 8)
    translated = wizard.start('owner', 'field', 'fr')
    assert translated['task_options'] == view['task_options']
    view = answer(wizard, translated, 'previous')
    assert view['task_options'] == old['task_options']
    view = answer(wizard, view, 'next')
    view = answer(wizard, view, '6')
    assert view['answers']['task_snapshot']['id'] == rows[5]['id']
    assert view['answers']['record_id'] == rows[5]['id']


@pytest.mark.parametrize('change', ['revision', 'completion'])
def test_changed_displayed_task_requires_refresh_and_a_new_selection(setup, change):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    row = planned(records)
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard, dict(action='cancelled'), target='record_id')
    if change == 'revision':
        current = records.plan_task('owner', 'field', 'cycle', task(entry='work-0', title='Corrected work'),
            request_id='revision', supersedes_id=row['id'])
    else:
        current = records.resolve_task('owner', 'field', row['id'], 'completed', 'Done', 'Operator',
            completed_on='2026-07-06', request_id='completed')
    with pytest.raises(ValueError, match='Task list changed; reply refresh'):
        answer(wizard, view, '1')
    assert wizard.get('owner', 'field') == view
    view = answer(wizard, view, 'refresh')
    assert view['task_options']['items'][0]['id'] == current['id']
    assert 'record_id' not in view['answers']
    view = answer(wizard, view, '1')
    assert view['answers']['task_snapshot'] == current


def test_selected_task_change_rejects_save_and_backtracking_rebuilds_list(setup):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    row = planned(records)
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard, dict(action='cancelled', record_id='1', reason='Postponed'))
    changed = records.resolve_task('owner', 'field', row['id'], 'completed', 'Done elsewhere', 'Operator',
        completed_on='2026-07-06', request_id='completion')
    with pytest.raises(ValueError, match='latest revision'):
        wizard.save('owner', 'field', view['draft_id'], view['revision'])
    while view['next_field'] != 'record_id':
        view = wizard.answer('owner', 'field', '', view['revision'], back=True)
    assert 'task_snapshot' not in view['answers'] and 'cycle_id' not in view['answers']
    assert view['task_options']['items'][0]['id'] == changed['id']
    view = answer(wizard, view, 'refresh')
    view = wizard.answer('owner', 'field', '', view['revision'], back=True)
    assert view['next_field'] == 'action' and view['task_options'] is None


def test_reopen_filters_planned_work_and_empty_list_is_recoverable(setup):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    row = planned(records)
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard, dict(action='reopen'), target='record_id')
    assert view['task_options'] == dict(page=1, pages=1, total=0, items=[])
    assert 'No eligible tasks' in view['question'] and '/back' in view['question']
    with pytest.raises(ValueError, match='displayed'):
        answer(wizard, view, '1')
    completed = records.resolve_task('owner', 'field', row['id'], 'completed', 'Done', 'Operator',
        completed_on='2026-07-06', request_id='completed')
    view = answer(wizard, view, 'refresh')
    assert view['task_options']['items'][0]['id'] == completed['id']
    view = answer(wizard, view, '1')
    assert view['next_field'] == 'reason'


def test_only_cycles_known_to_the_draft_are_listed_and_owned_cache_is_erased(setup):
    workflow, farms = setup
    records = CropRecords(workflow.db)
    planned(records)
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard, dict(action='cancelled'), target='record_id')
    cycle = records.create('owner', 'field', 'pepper', '2026-07-01', 'Operator', request_id='second-cycle')['cycle']['id']
    other = records.plan_task('owner', 'field', cycle, task(), request_id='other-cycle')
    view = answer(wizard, view, 'refresh')
    assert view['task_options']['total'] == 1
    wizard.cancel('owner', 'field', view['revision'])
    view = finish(wizard, dict(action='cancelled'), target='record_id')
    assert view['task_options']['total'] == 2
    assert other['id'] in {item['id'] for item in view['task_options']['items']}
    foreign = wizard.start('other', 'field3')
    farms.purge('owner')
    assert not farms.export('owner')['task_intakes']
    assert wizard.get('other', 'field3') == foreign


@pytest.mark.parametrize('language', ['en', 'fr', 'zh-Hant'])
def test_long_task_text_cannot_expand_page_or_create_navigation_lines(setup, language):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    for index in range(6):
        planned(records, index, title='\nnext\n'+'🌱'*1000, location='🌱'*1000)
    view = finish(TaskConsultation(workflow.db), dict(action='cancelled'), target='record_id', language=language)
    rendered = render_task_intake(view)
    assert len(view['task_options']['items']) == 5
    assert len(rendered.encode('utf-16-le'))//2 <= 3500
    assert '\nnext\n' not in rendered


def test_v3_draft_keeps_full_id_task_selection(setup):
    workflow, _ = setup
    row = planned(CropRecords(workflow.db))
    wizard = TaskConsultation(workflow.db)
    wizard.start('owner', 'field')
    payload = json.loads(workflow.db.query('SELECT payload FROM task_intake_drafts')[0][0])
    payload['intake_version'] = 3
    workflow.db.execute('UPDATE task_intake_drafts SET payload=?', (json.dumps(payload),))
    view = finish(wizard, dict(action='cancelled'), target='record_id')
    assert view['task_options'] is None and 'Copy its full ID' in view['question']
    assert answer(wizard, view, row['id'])['answers']['task_snapshot'] == row


def test_full_numeric_record_id_is_not_mistaken_for_a_menu_number(setup, monkeypatch):
    workflow, _ = setup
    with monkeypatch.context() as patch:
        patch.setattr('agronaut_agent.crop_records.uuid.uuid4', lambda: SimpleNamespace(hex='12345678123442348234123456789012'))
        row = planned(CropRecords(workflow.db))
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard, dict(action='cancelled'), target='record_id')
    assert answer(wizard, view, row['id'])['answers']['task_snapshot'] == row
