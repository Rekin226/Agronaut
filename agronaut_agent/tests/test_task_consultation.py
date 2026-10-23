"""Owned task planning and resolutions preserve the actual-record boundary."""
import sqlite3

import pytest

from agronaut_agent.crop_records import CropRecords
from agronaut_agent.farm_overview import FarmOverview
from agronaut_agent.store import SCHEMA_VERSION, _Db
from agronaut_agent.task_consultation import TaskConsultation, render_task_intake
from agronaut_agent.tests.test_crop_calendar import task
from agronaut_agent.tests.test_crop_records import harvest
from agronaut_agent.tests.test_field_workflow import setup as setup

ANSWERS = dict(action='plan', cycle_id='1', entry='inspect-1', title='Inspect crop', activity='scouting',
    basis='date', start='2026-07-06', end='2026-07-08', start_offset_days='5', end_offset_days='7',
    location='North rows', priority='normal', notes='Synthetic work plan', trigger='none', source='Operator diary · carnet · 筆記')


def finish(wizard, answers=None, target=None, language='en', owner='owner', unit='field'):
    supplied = dict(ANSWERS, **(answers or {}))
    view = wizard.start(owner, unit, language)
    while view['next_field'] != target:
        view = wizard.answer(owner, unit, supplied[view['next_field']], view['revision'])
    return view


@pytest.mark.parametrize('language', ['en', 'fr', 'zh-Hant'])
@pytest.mark.parametrize('basis', ['date', 'planting'])
def test_plan_preview_and_atomic_retry_preserve_source_and_calendar(setup, language, basis):
    workflow, farms = setup
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard, dict(basis=basis), language=language)
    assert view['window'] == dict(start='2026-07-06', end='2026-07-08')
    assert ANSWERS['source'] in render_task_intake(view)
    assert farms.export('owner')['crop_records'] == []
    saved = wizard.save('owner', 'field', view['draft_id'], view['revision'])
    assert saved['record']['data']['source'] == ANSWERS['source']
    assert saved['record']['data']['status'] == 'planned'
    assert saved['record']['operation']['intake']['language'] == language
    assert wizard.save('owner', 'field', view['draft_id'], view['revision']) == saved
    assert wizard.get('owner', 'field') is None
    overview = FarmOverview(workflow.db).get('owner', 'farm')
    assert overview['farms'][0]['units'][0]['cycles'][0]['tasks'][0]['record_id'] == saved['record']['id']
    assert farms.export('owner')['water_days'] == []
    newer = wizard.start('owner', 'field')
    assert wizard.save('owner', 'field', view['draft_id'], view['revision']) == saved
    assert wizard.get('owner', 'field') == newer


@pytest.mark.parametrize('language', ['en', 'fr', 'zh-Hant'])
@pytest.mark.parametrize('action', ['completed', 'cancelled', 'reopen'])
def test_resolutions_use_exact_current_task_and_do_not_create_actuals(setup, language, action):
    workflow, farms = setup
    records = CropRecords(workflow.db)
    planned = records.plan_task('owner', 'field', 'cycle', task(), request_id='task')
    if action == 'reopen':
        planned = records.resolve_task('owner', 'field', planned['id'], 'cancelled', 'Cancel', 'Operator', request_id='cancel')
    wizard = TaskConsultation(workflow.db)
    before = farms.export('owner')
    view = finish(wizard, dict(action=action, record_id=planned['id'], completed_on='2026-07-06', evidence='none', reason='Operator report'), language=language)
    assert farms.export('owner')['crop_records'] == before['crop_records']
    saved = wizard.save('owner', 'field', view['draft_id'], view['revision'])
    assert saved['record']['supersedes_id'] == planned['id']
    assert saved['record']['data']['status'] == ('planned' if action == 'reopen' else action)
    assert saved['record']['data']['resolution']['completion_record'] is None
    assert farms.export('owner')['water_days'] == []
    assert {r['kind'] for r in farms.export('owner')['crop_records']} == {'calendar'}


def test_completion_links_existing_evidence_and_later_void_is_flagged(setup):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    planned = records.plan_task('owner', 'field', 'cycle', task(), request_id='task')
    actual = records.record_harvest('owner', 'field', 'cycle', harvest(), request_id='actual')
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard, dict(action='completed', record_id=planned['id'], completed_on=actual['data']['day'],
        evidence='crop_record:'+actual['id'], reason='Harvest recorded'))
    saved = wizard.save('owner', 'field', view['draft_id'], view['revision'])
    assert saved['record']['data']['resolution']['completion_record']['snapshot'] == actual
    records.void_harvest('owner', 'field', actual['id'], 'Wrong batch', request_id='void')
    assert wizard.save('owner', 'field', view['draft_id'], view['revision']) == saved
    tasks = records.get('owner', 'field', 'cycle')['calendar']['tasks']
    assert tasks[0]['needs_review'] and tasks[0]['completion_evidence_state'] == 'superseded'


@pytest.mark.parametrize('field,value', [('action','automatic'), ('cycle_id','cycle3'), ('priority','critical'),
    ('activity','spray'), ('basis','guess'), ('start','not-a-date'), ('end','2026-07-05'),
    ('title','/delete_me'), ('source','x'*1001)])
def test_invalid_plan_answers_leave_draft_unchanged(setup, field, value):
    workflow, _ = setup
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard, target=field)
    with pytest.raises(ValueError):
        wizard.answer('owner', 'field', value, view['revision'])
    assert wizard.get('owner', 'field') == view


@pytest.mark.parametrize('field,value', [('start_offset_days','1.5'), ('start_offset_days','-366'), ('end_offset_days','4')])
def test_invalid_planting_windows_leave_question_unchanged(setup, field, value):
    workflow, _ = setup
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard, dict(basis='planting'), field)
    with pytest.raises(ValueError):
        wizard.answer('owner', 'field', value, view['revision'])
    assert wizard.get('owner', 'field') == view


def test_back_change_language_restart_and_stale_revision(setup):
    workflow, _ = setup
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard)
    old = view
    while view['next_field'] != 'basis':
        view = wizard.answer('owner', 'field', '', view['revision'], back=True)
    assert 'start' not in view['answers']
    view = wizard.start('owner', 'field', 'fr')
    with pytest.raises(ValueError, match='changed'):
        wizard.answer('owner', 'field', 'date', old['revision'])
    db = _Db(workflow.db.path)
    try:
        assert TaskConsultation(db).get('owner', 'field') == view
        result = finish(TaskConsultation(db), dict(basis='planting'), language='fr')
        assert result['window'] == old['window']
    finally:
        db._conn.close()


def test_changed_cycle_and_duplicate_entry_require_review_without_write(setup):
    workflow, farms = setup
    records = CropRecords(workflow.db)
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard)
    records.correct_planting('owner','field','cycle','pepper','2026-07-02','Operator','Corrected',request_id='edit',expected_revision=None)
    before = farms.export('owner')
    with pytest.raises(ValueError, match='cycle changed'):
        wizard.save('owner','field',view['draft_id'],view['revision'])
    assert farms.export('owner') == before
    wizard.cancel('owner','field',view['revision'])
    view = finish(wizard)
    records.plan_task('owner', 'field', 'cycle', task(entry=ANSWERS['entry']), request_id='concurrent')
    with pytest.raises(ValueError, match='already exists'):
        wizard.save('owner','field',view['draft_id'],view['revision'])
    assert wizard.get('owner','field') == view


def test_stale_task_foreign_evidence_and_future_completion_are_rejected(setup):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    planned = records.plan_task('owner','field','cycle',task(),request_id='task')
    actual = records.record_harvest('other','field3','cycle3',harvest(),request_id='foreign')
    wizard = TaskConsultation(workflow.db)
    supplied = dict(action='completed',record_id=planned['id'],completed_on='2026-07-06',evidence='none',reason='Done')
    view = finish(wizard,supplied,'completed_on')
    with pytest.raises(ValueError, match='today'):
        wizard.answer('owner','field','2099-01-01',view['revision'])
    view = finish(wizard,supplied,'evidence')
    with pytest.raises(ValueError, match='owned'):
        wizard.answer('owner','field','crop_record:'+actual['id'],view['revision'])
    assert wizard.get('owner','field') == view
    view = finish(wizard,supplied)
    records.resolve_task('owner','field',planned['id'],'cancelled','Cancelled elsewhere','Operator',request_id='other')
    with pytest.raises(ValueError, match='superseded'):
        wizard.save('owner','field',view['draft_id'],view['revision'])
    assert wizard.get('owner','field') == view


def test_atomic_failure_export_owner_isolation_and_purge(setup):
    workflow, farms = setup
    wizard = TaskConsultation(workflow.db)
    view = finish(wizard)
    foreign = wizard.start('other','field3')
    with pytest.raises(ValueError):
        wizard.get('other','field')
    before = farms.export('owner')
    assert before['task_intakes'][0]['id'] == view['draft_id']
    workflow.db.execute("CREATE TRIGGER fail_task_delete BEFORE DELETE ON task_intake_drafts BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='test failure'):
        wizard.save('owner','field',view['draft_id'],view['revision'])
    assert farms.export('owner') == before
    workflow.db.execute('DROP TRIGGER fail_task_delete')
    farms.purge('owner')
    assert not farms.export('owner')['task_intakes']
    assert wizard.get('other','field3') == foreign


@pytest.mark.parametrize('name', ['FieldConsultation','ScoutingConsultation','PlantingConsultation','WaterLogConsultation','MeasurementConsultation','HarvestConsultation'])
def test_other_unit_drafts_are_mutually_exclusive(setup, name):
    import importlib
    modules = dict(FieldConsultation='field_consultation', ScoutingConsultation='scouting_consultation',
        PlantingConsultation='planting_consultation', WaterLogConsultation='water_log_consultation',
        MeasurementConsultation='measurement_consultation', HarvestConsultation='harvest_consultation')
    workflow, _ = setup
    wizard = TaskConsultation(workflow.db)
    other = getattr(importlib.import_module('agronaut_agent.'+modules[name]), name)(workflow.db)
    view = wizard.start('owner','field')
    with pytest.raises(ValueError, match='task entry'):
        other.start('owner','field')
    wizard.cancel('owner','field',view['revision'])
    other.start('owner','field')
    with pytest.raises(ValueError, match='Finish or cancel'):
        wizard.start('owner','field')


def test_v22_upgrade_keeps_records_and_private_backup(setup):
    workflow, farms = setup
    before = farms.export('owner')
    workflow.db.execute('DROP TABLE task_intake_drafts')
    workflow.db.execute('PRAGMA user_version=22')
    upgraded = _Db(workflow.db.path)
    try:
        assert upgraded.query('PRAGMA user_version')[0][0] == SCHEMA_VERSION
        assert TaskConsultation(upgraded).start('owner','field')['next_field'] == 'action'
        backup = next(workflow.db.path.parent.glob(f'{workflow.db.path.name}.pre-v{SCHEMA_VERSION}-*.bak'))
        assert backup.stat().st_mode & 0o777 == 0o600
        with sqlite3.connect(backup) as conn:
            assert conn.execute('PRAGMA user_version').fetchone()[0] == 22
            assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='task_intake_drafts'").fetchone() is None
            assert conn.execute('SELECT COUNT(*) FROM production_cycles').fetchone()[0] == 3
        assert len(before['cycles']) == 2
    finally:
        upgraded._conn.close()


def test_failed_upgrade_rolls_back_version_and_preserves_old_data(setup, monkeypatch):
    from agronaut_agent import task_consultation
    workflow, _ = setup
    workflow.db.execute('DROP TABLE task_intake_drafts')
    workflow.db.execute('PRAGMA user_version=22')
    monkeypatch.setattr(task_consultation, 'SCHEMA', 'CREATE TABLE invalid SQL')
    with pytest.raises(sqlite3.Error):
        _Db(workflow.db.path)
    assert workflow.db.query('PRAGMA user_version')[0][0] == 22
    assert workflow.db.query('SELECT COUNT(*) FROM production_cycles')[0][0] == 3
    assert workflow.db.query("SELECT 1 FROM sqlite_master WHERE name='task_intake_drafts'") == []


@pytest.mark.parametrize('zone', [None, 'Not/A_Timezone'])
def test_unknown_timezone_does_not_create_task_draft(setup, zone):
    workflow, farms = setup
    workflow.db.execute("UPDATE farms SET timezone=? WHERE id='farm'", (zone,))
    before = farms.export('owner')
    with pytest.raises(ValueError, match='timezone'):
        TaskConsultation(workflow.db).start('owner','field')
    assert farms.export('owner') == before
