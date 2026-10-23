"""Daily water intake preserves actual evidence, revisions, and replay boundaries."""

import sqlite3
from datetime import datetime, timezone

import pytest

from agronaut_agent.crop_records import CropRecords
from agronaut_agent.field_workflow import execute_field_workflow
from agronaut_agent.planting_consultation import PlantingConsultation
from agronaut_agent.scouting_consultation import ScoutingConsultation
from agronaut_agent.store import SCHEMA_VERSION, _Db
from agronaut_agent.tests.test_field_workflow import inputs, setup, water  # noqa: F401
from agronaut_agent.water_log_consultation import WaterLogConsultation, render_water_log

ANSWERS = dict(cycle_id='1', day='2026-07-06', rainfall_mm='5', net_irrigation_mm='3', runoff_mm='1',
               source='Synthetic whole-day notebook; net irrigation estimated separately', provenance='estimated',
               correction='replace', reason='Synthetic transcription correction')


def complete(wizard, language='en', **changes):
    answers = dict(ANSWERS, **changes)
    view = wizard.start('owner', 'field', language)
    while view['status'] != 'ready':
        view = wizard.answer('owner', 'field', answers[view['next_field']], view['revision'])
    return view


@pytest.mark.parametrize('language', ['en', 'fr', 'zh-Hant'])
@pytest.mark.parametrize('cycle', ['1', 'none'])
def test_atomic_save_resume_export_and_retry_do_not_change_calculations(setup, language, cycle):  # noqa: F811
    workflow, farms = setup
    base = workflow.save('owner', 'field', 'root_zone_budget', inputs(), request_id='base', cycle_id='cycle' if cycle=='1' else None)
    wizard = WaterLogConsultation(workflow.db)
    view = complete(wizard, language, cycle_id=cycle)
    assert workflow.list('owner', 'field')['water_days'] == []
    assert farms.export('owner')['water_log_intakes'][0]['data']['rainfall_mm'] == 5
    assert '5.0' in render_water_log(view)
    reopened = _Db(workflow.db.path)
    try:
        resumed = WaterLogConsultation(reopened)
        assert resumed.start('owner', 'field') == view
        saved = resumed.save('owner', 'field', view['draft_id'], view['revision'])
        assert saved['record']['data'] == water(source=ANSWERS['source'], provenance='estimated')
        assert saved['record']['cycle_id'] == ('cycle' if cycle=='1' else None)
        new = resumed.start('owner', 'field')
        assert resumed.save('owner', 'field', view['draft_id'], view['revision']) == saved
        assert resumed.get('owner', 'field') == new
    finally:
        reopened._conn.close()
    assert workflow.get('owner', 'field', base['id']) == base
    assert len(workflow.list('owner', 'field')['calculations']) == 1
    replay = workflow.replay('owner', 'field', base['id'], [saved['record']['id']], request_id='replay')
    assert replay['inputs']['events'][0]['net_irrigation_mm'] == 3
    assert replay['inputs']['rainfall_overrides'][0]['rainfall_mm'] == 5
    assert replay['inputs']['weather'] == base['inputs']['weather']


@pytest.mark.parametrize('language,replace', [('en', 'replace'), ('fr', 'remplacer'), ('zh-Hant', '取代')])
def test_correction_retains_prior_and_requires_explicit_replacement(setup, language, replace):  # noqa: F811
    workflow, _ = setup
    original = workflow.record_day('owner', 'field', water(quality='checked'), request_id='original', cycle_id='cycle')
    wizard = WaterLogConsultation(workflow.db)
    view = wizard.start('owner', 'field', language)
    while view['next_field'] != 'correction':
        view = wizard.answer('owner', 'field', ANSWERS[view['next_field']], view['revision'])
    assert view['previous_record'] == original
    with pytest.raises(ValueError):
        wizard.answer('owner', 'field', 'save', view['revision'])
    with pytest.raises(ValueError, match='Finish'):
        wizard.save('owner', 'field', view['draft_id'], view['revision'])
    view = wizard.answer('owner', 'field', replace, view['revision'])
    view = wizard.answer('owner', 'field', 'Correcting copied source and provenance', view['revision'])
    result = wizard.save('owner', 'field', view['draft_id'], view['revision'])['record']
    assert result['supersedes_id'] == original['id']
    assert result['data']['quality'] == 'unverified'
    assert result['operation']['intake']['reason'] == 'Correcting copied source and provenance'
    rows = workflow.list('owner', 'field')['water_days']
    assert len(rows) == 2 and rows[0]['data'] == original['data'] and rows[0]['superseded']


def test_concurrent_record_creation_and_correction_require_new_review(setup):  # noqa: F811
    workflow, _ = setup
    wizard = WaterLogConsultation(workflow.db)
    ready = complete(wizard)
    first = workflow.record_day('owner', 'field', water(), request_id='other', cycle_id='cycle')
    with pytest.raises(ValueError, match='Daily totals changed'):
        wizard.save('owner', 'field', ready['draft_id'], ready['revision'])
    refreshed = wizard.start('owner', 'field')
    assert refreshed['next_field'] == 'correction'
    ready = complete(wizard)
    workflow.record_day('owner', 'field', water(rainfall_mm=9), request_id='other2', cycle_id='cycle', supersedes_id=first['id'])
    with pytest.raises(ValueError, match='Daily totals changed'):
        wizard.save('owner', 'field', ready['draft_id'], ready['revision'])
    refreshed = wizard.start('owner', 'field')
    assert refreshed['next_field'] == 'correction' and 'reason' not in refreshed['answers']
    back = wizard.answer('owner', 'field', '', refreshed['revision'], back=True)
    assert 'correction' not in back['answers'] and 'reason' not in back['answers']


@pytest.mark.parametrize('value', ['unknown', 'NaN', 'Infinity', '-1', '3 L', '2,5', '1_000', '1e-10000'])
def test_unavailable_or_invalid_amounts_cannot_become_zero(setup, value):  # noqa: F811
    workflow, _ = setup
    wizard = WaterLogConsultation(workflow.db)
    view = wizard.start('owner', 'field')
    for key in ('cycle_id', 'day'):
        view = wizard.answer('owner', 'field', ANSWERS[key], view['revision'])
    with pytest.raises(ValueError):
        wizard.answer('owner', 'field', value, view['revision'])
    assert wizard.get('owner', 'field') == view
    for answer in ('0', '0'):
        view = wizard.answer('owner', 'field', answer, view['revision'])
    with pytest.raises(ValueError):
        wizard.answer('owner', 'field', '1', view['revision'])
    view = wizard.answer('owner', 'field', '0', view['revision'])
    assert all(view['answers'][key] == 0 for key in ('rainfall_mm', 'net_irrigation_mm', 'runoff_mm'))


def test_date_cycle_closure_and_timezone_changes_are_checked(setup):  # noqa: F811
    workflow, _ = setup
    wizard = WaterLogConsultation(workflow.db)
    view = wizard.start('owner', 'field')
    view = wizard.answer('owner', 'field', '1', view['revision'])
    for day in ('2026-06-30', '2026-02-30', datetime.now(timezone.utc).date().isoformat(), '9999-01-01'):
        with pytest.raises(ValueError):
            wizard.answer('owner', 'field', day, view['revision'])
        assert wizard.get('owner', 'field') == view
    ready = complete(wizard)
    CropRecords(workflow.db).close_cycle('owner', 'field', 'cycle', '2026-07-05', 'Synthetic closure', 'Synthetic notebook', request_id='close', expected_revision=None)
    with pytest.raises(ValueError, match='completed local day'):
        wizard.save('owner', 'field', ready['draft_id'], ready['revision'])
    # A cycle closure can invalidate a pending date; back still lets the grower fix it.
    back = wizard.answer('owner', 'field', '', ready['revision'], back=True)
    assert back['next_field'] == 'provenance'
    workflow.db.execute("UPDATE farms SET timezone='Asia/Taipei' WHERE id='farm'")
    with pytest.raises(ValueError, match='timezone or crop'):
        wizard.save('owner', 'field', back['draft_id'], back['revision'])


def test_stale_language_owner_conflicts_and_erasure(setup):  # noqa: F811
    workflow, farms = setup
    wizard = WaterLogConsultation(workflow.db)
    view = complete(wizard)
    changed = wizard.start('owner', 'field', 'fr')
    assert changed['answers'] == view['answers'] and changed['revision'] != view['revision']
    with pytest.raises(ValueError):
        wizard.save('owner', 'field', view['draft_id'], view['revision'])
    with pytest.raises(ValueError):
        wizard.start('other', 'field')
    for other in (PlantingConsultation(workflow.db), ScoutingConsultation(workflow.db)):
        with pytest.raises(ValueError, match='water logging'):
            other.start('owner', 'field')
    wizard.start('other', 'field3')
    farms.purge('owner')
    assert farms.export('owner')['water_log_intakes'] == []
    assert len(farms.export('other')['water_log_intakes']) == 1


def test_failed_draft_delete_rolls_back_daily_record(setup):  # noqa: F811
    workflow, _ = setup
    wizard = WaterLogConsultation(workflow.db)
    ready = complete(wizard)
    workflow.db.execute("CREATE TRIGGER refuse_water_delete BEFORE DELETE ON water_log_drafts BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        wizard.save('owner', 'field', ready['draft_id'], ready['revision'])
    assert workflow.list('owner', 'field')['water_days'] == []
    assert wizard.get('owner', 'field') == ready


def test_v19_upgrade_and_failure_preserve_records_and_private_backup(setup, monkeypatch):  # noqa: F811
    from agronaut_agent import water_log_consultation

    workflow, _ = setup
    original = workflow.record_day('owner', 'field', water(), request_id='original')
    workflow.db.execute('DROP TABLE measurement_intake_receipts')
    workflow.db.execute('DROP TABLE measurement_intake_drafts')
    workflow.db.execute('DROP TABLE water_log_drafts')
    workflow.db.execute('PRAGMA user_version=19')
    schema = water_log_consultation.SCHEMA
    monkeypatch.setattr(water_log_consultation, 'SCHEMA', 'CREATE TABLE broken (')
    with pytest.raises(sqlite3.OperationalError):
        _Db(workflow.db.path)
    assert workflow.db.query('PRAGMA user_version')[0][0] == 19
    monkeypatch.setattr(water_log_consultation, 'SCHEMA', schema)
    upgraded = _Db(workflow.db.path)
    try:
        assert upgraded.query('PRAGMA user_version')[0][0] == SCHEMA_VERSION
        assert execute_field_workflow(upgraded, 'owner', 'field', 'start_water_log', {})['next_field'] == 'cycle_id'
        backups = list(workflow.db.path.parent.glob(f'*.pre-v{SCHEMA_VERSION}-*.bak'))
        assert backups
        for backup in backups:
            assert backup.stat().st_mode & 0o777 == 0o600
            with sqlite3.connect(backup) as conn:
                assert conn.execute('PRAGMA user_version').fetchone()[0] == 19
                assert conn.execute('SELECT id FROM field_water_days').fetchone()[0] == original['id']
                assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='water_log_drafts'").fetchone() is None
    finally:
        upgraded._conn.close()
