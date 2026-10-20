"""A displayed number keeps its source identity across resume, paging and corrections."""
import json

import pytest

from agronaut_agent.crop_records import CropRecords
from agronaut_agent.observation_quality import ObservationReviews
from agronaut_agent.store import _Db
from agronaut_agent.task_consultation import TaskConsultation, render_task_intake
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_field_workflow import water
from agronaut_agent.tests.test_measurement_entry import reading
from agronaut_agent.tests.test_task_consultation import finish
from agronaut_agent.tests.test_task_triggers import scouting


def answer(wizard, view, value):
    return wizard.answer('owner', 'field', value, view['revision'])


def sources(records, count=7):
    return [records.record_scouting('owner', 'field', 'cycle',
        scouting(entry=f'scout-{i}', day=f'2026-07-{i+1:02}', location=f'Row {i}'),
        request_id=f'scout-{i}') for i in range(count)]


@pytest.mark.parametrize('language', ['en', 'fr', 'zh-Hant'])
def test_paging_resume_language_and_new_records_do_not_remap_displayed_numbers(setup, language):
    workflow, farms = setup
    records = CropRecords(workflow.db)
    original = sources(records)
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard, target='trigger', language=language)
    before = farms.export('owner')['crop_records']
    assert view['source_options']['total'] == 7
    assert [r['id'] for r in view['source_options']['items']] == [r['id'] for r in reversed(original[2:])]
    assert 'Row 6' in view['question'] and 'next' in view['question'] and 'refresh' in view['question']
    previous = view
    view = answer(wizard, view, 'next')
    assert view['answers'] == previous['answers'] and view['source_options']['page'] == 2
    assert [r['number'] for r in view['source_options']['items']] == [6, 7]
    with pytest.raises(ValueError, match='draft changed'):
        answer(wizard, previous, '1')
    with pytest.raises(ValueError, match='displayed'):
        answer(wizard, view, '1')
    with pytest.raises(ValueError, match='No page'):
        answer(wizard, view, 'next')
    assert wizard.get('owner', 'field') == view
    reopened = _Db(workflow.db.path)
    try:
        assert TaskConsultation(reopened).get('owner', 'field') == view
    finally:
        reopened._conn.close()
    records.record_scouting('owner', 'field', 'cycle', scouting(entry='later', day='2026-07-09'), request_id='later')
    changed_language = wizard.start('owner', 'field', 'fr' if language != 'fr' else 'en')
    assert changed_language['source_options'] == view['source_options']
    view = answer(wizard, changed_language, 'previous')
    assert view['source_options'] == previous['source_options']
    view = answer(wizard, view, 'next')
    view = answer(wizard, view, '6')
    assert view['answers']['trigger'] == 'crop_record:'+original[1]['id']
    assert view['answers']['trigger_snapshot'] == original[1]
    assert view['source_options'] is None
    view = finish(wizard, dict(trigger_reason='Inspect this row'), language=view['language'])
    saved = wizard.save('owner', 'field', view['draft_id'], view['revision'])
    assert saved['record']['data']['trigger']['id'] == original[1]['id']
    after = {row['id']: row for row in farms.export('owner')['crop_records']}
    assert all(after[row['id']] == row for row in before)


@pytest.mark.parametrize('kind', ['crop_record', 'measurement', 'water_day'])
def test_numbered_source_rejects_changed_display_and_refresh_requires_new_choice(setup, kind):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    if kind == 'crop_record':
        source = records.record_scouting('owner', 'field', 'cycle', scouting(), request_id='source')
    elif kind == 'measurement':
        source = records.record_measurement('owner', 'field', reading(), cycle_id='cycle', request_id='source')
    else:
        source = workflow.record_day('owner', 'field', water(), cycle_id='cycle', request_id='source')
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard, target='trigger')
    assert view['source_options']['items'][0]['kind'] == kind
    if kind == 'crop_record':
        changed = records.record_scouting('owner', 'field', 'cycle', scouting(affected_count=1), request_id='change', supersedes_id=source['id'])
    elif kind == 'measurement':
        changed = ObservationReviews(workflow.db).review('owner', 'field', source['id'], 'checked',
            'Checked diary', 'Operator', request_id='check', expected_revision=None)
    else:
        changed = workflow.record_day('owner', 'field', water(net_irrigation_mm=0), cycle_id='cycle', request_id='change', supersedes_id=source['id'])
    with pytest.raises(ValueError, match='Source list changed; reply refresh'):
        answer(wizard, view, '1')
    assert wizard.get('owner', 'field') == view
    assert records.get('owner', 'field', 'cycle')['calendar']['tasks'] == []
    refreshed = answer(wizard, view, 'refresh')
    assert refreshed['source_options']['page'] == 1
    assert refreshed['answers'] == view['answers']
    selected = answer(wizard, refreshed, '1')
    if kind == 'measurement':
        assert selected['answers']['trigger_snapshot']['quality_review'] == changed
        assert selected['answers']['trigger_reference']['review_revision_id'] == changed['id']
    else:
        assert selected['answers']['trigger_snapshot'] == changed


def test_scope_rejection_and_voids_are_filtered_and_empty_list_allows_none(setup):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    records.record_scouting('other', 'field3', 'cycle3', scouting(), request_id='foreign')
    records.record_scouting('owner', 'field2', 'cycle2', scouting(), request_id='other-unit')
    cycle = records.create('owner', 'field', 'pepper', '2026-07-01', 'Operator', request_id='new-cycle')['cycle']['id']
    records.record_scouting('owner', 'field', cycle, scouting(), request_id='other-cycle')
    source = records.record_scouting('owner', 'field', 'cycle', scouting(), request_id='void-source')
    records.void_observation('owner', 'field', source['id'], 'Wrong row', request_id='void')
    reading_row = records.record_measurement('owner', 'field', reading(), cycle_id='cycle', request_id='rejected')
    ObservationReviews(workflow.db).review('owner', 'field', reading_row['id'], 'rejected', 'Wrong sensor',
        'Operator', request_id='reject', expected_revision=None)
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard, dict(cycle_id='cycle'), target='trigger')
    assert view['source_options'] == dict(page=1, pages=1, total=0, items=[])
    assert 'No eligible source' in view['question']
    with pytest.raises(ValueError, match='displayed'):
        answer(wizard, view, '1')
    with pytest.raises(ValueError):
        wizard.get('other', 'field')
    view = answer(wizard, view, 'none')
    assert view['next_field'] == 'source' and view['source_options'] is None
    assert 'trigger_reference' not in view['answers']


def test_back_refreshes_sources_without_treating_navigation_as_answers(setup):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    sources(records)
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard, target='trigger')
    view = answer(wizard, view, 'next')
    view = answer(wizard, view, 'refresh')
    back = wizard.answer('owner', 'field', '', view['revision'], back=True)
    assert back['next_field'] == 'notes' and back['source_options'] is None
    view = answer(wizard, back, 'Revised notes')
    assert view['source_options']['page'] == 1
    view = answer(wizard, view, '1')
    records.record_scouting('owner', 'field', 'cycle', scouting(entry='later', day='2026-07-09'), request_id='later')
    back = wizard.answer('owner', 'field', '', view['revision'], back=True)
    assert back['next_field'] == 'trigger' and 'trigger_snapshot' not in back['answers']
    assert back['source_options']['total'] == 8


@pytest.mark.parametrize('language', ['en', 'fr', 'zh-Hant'])
def test_source_page_is_bounded_and_cannot_render_user_text_as_navigation(setup, language):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    for index in range(6):
        records.record_scouting('owner', 'field', 'cycle', scouting(entry=f'scout-{index}',
            location='🌱'*200, symptoms='\nnext\nprevious\n'+'🌱'*500), request_id=f'source-{index}')
    view = finish(TaskConsultation(workflow.db), target='trigger', language=language)
    rendered = render_task_intake(view)
    assert len(rendered.encode('utf-16-le'))//2 <= 3500
    assert len(view['source_options']['items']) == 5
    assert '\nnext\n' not in rendered and '\nprevious\n' not in rendered


def test_v2_draft_keeps_reference_only_source_question(setup):
    workflow, _ = setup
    sources(CropRecords(workflow.db), 1)
    wizard = TaskConsultation(workflow.db)
    wizard.start('owner', 'field')
    payload = json.loads(workflow.db.query('SELECT payload FROM task_intake_drafts')[0][0])
    payload['intake_version'] = 2
    workflow.db.execute('UPDATE task_intake_drafts SET payload=?', (json.dumps(payload),))
    view = finish(wizard, target='trigger')
    assert view['source_options'] is None and 'next / previous' not in view['question']
    with pytest.raises(ValueError, match='Use none'):
        answer(wizard, view, '1')
    assert answer(wizard, view, 'none')['next_field'] == 'source'


def test_rejected_displayed_source_requires_refresh_and_cached_sources_are_erased(setup):
    workflow, farms = setup
    records = CropRecords(workflow.db)
    row = records.record_measurement('owner', 'field', reading(), cycle_id='cycle', request_id='source')
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard, target='trigger')
    foreign = wizard.start('other', 'field3')
    exported = farms.export('owner')['task_intakes'][0]
    assert exported['source_browser']['items'][0]['id'] == row['id']
    assert 'source_browser' not in farms.export('other')['task_intakes'][0]
    ObservationReviews(workflow.db).review('owner', 'field', row['id'], 'rejected', 'Wrong sensor',
        'Operator', request_id='reject', expected_revision=None)
    with pytest.raises(ValueError, match='Source list changed'):
        answer(wizard, view, '1')
    view = answer(wizard, view, 'refresh')
    assert view['source_options']['items'] == []
    farms.purge('owner')
    assert not farms.export('owner')['task_intakes']
    assert wizard.get('other', 'field3') == foreign
