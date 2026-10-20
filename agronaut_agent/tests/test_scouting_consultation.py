"""Scouting drafts preserve owner scope and append only reviewed observations."""

import sqlite3

import pytest

from agronaut_agent.crop_records import execute_crop_records
from agronaut_agent.field_consultation import FieldConsultation
from agronaut_agent.scouting_consultation import ScoutingConsultation, render_scouting_intake
from agronaut_agent.store import SCHEMA_VERSION, _Db
from agronaut_agent.tests.test_crop_records import create, setup  # noqa: F401

ANSWERS = dict(cycle_id='1',day='2026-07-06',location='Row 1; first ten plants',
               symptoms='Yellow leaf margins; cause unknown',counts='10,2',source='Synthetic dated notebook',provenance='observed')


def complete(wizard, unit='field', **changes):
    supplied = dict(ANSWERS,**changes)
    view = wizard.start('owner',unit)
    while view['status']!='ready':
        view = wizard.answer('owner',unit,supplied[view['next_field']],view['revision'])
    return view


@pytest.mark.parametrize('unit',['field','hydro','aqua'])
def test_review_save_retry_and_restart_preserve_exact_observation(setup,unit):  # noqa: F811
    records,farms=setup
    cycle=create(records,unit)['cycle']['id']
    wizard=ScoutingConsultation(records.db)
    view=complete(wizard,unit,counts='unknown')
    assert len(records.get('owner',unit,cycle)['records'])==1
    assert 'Inspected plants: unknown' in render_scouting_intake(view)
    assert farms.export('owner')['scouting_intakes'][0]['data']['counts']==[None,None]
    reopened=_Db(records.db.path)
    try:
        other=ScoutingConsultation(reopened)
        assert other.get('owner',unit)==view
        saved=other.save('owner',unit,view['draft_id'],view['revision'])
        assert saved['record']['data']==view['preview']['data']
        assert saved['record']['cycle_id']==cycle
        assert other.save('owner',unit,view['draft_id'],view['revision'])==saved
        new=other.start('owner',unit)
        assert other.save('owner',unit,view['draft_id'],view['revision'])==saved
        assert other.get('owner',unit)==new  # An old save retry must not remove a newer draft.
        with pytest.raises(ValueError,match='receipt'):
            other.save('owner',unit,view['draft_id'],'wrong')
    finally:
        reopened._conn.close()
    assert len(records.get('owner',unit,cycle)['records'])==2
    assert farms.export('owner')['scouting_intakes']


def test_back_and_stale_revision_do_not_change_saved_or_draft_data(setup):  # noqa: F811
    records,_=setup
    create(records)
    wizard=ScoutingConsultation(records.db)
    first=wizard.start('owner','field')
    next_view=wizard.answer('owner','field','1',first['revision'])
    with pytest.raises(ValueError,match='changed'):
        wizard.answer('owner','field','2026-07-06',first['revision'])
    assert wizard.get('owner','field')==next_view
    back=wizard.answer('owner','field','',next_view['revision'],back=True)
    assert back['next_field']=='cycle_id' and back['answers']=={}
    with pytest.raises(ValueError,match='Complete'):
        wizard.save('owner','field',back['draft_id'],back['revision'])


@pytest.mark.parametrize('value',['10','10,11','0,0','-1,0','10.0,2','10,unknown','1000000001,0','true',''])
def test_invalid_counts_do_not_advance_or_turn_into_zero(setup,value):  # noqa: F811
    records,_=setup
    create(records)
    wizard=ScoutingConsultation(records.db)
    view=wizard.start('owner','field')
    while view['next_field']!='counts':
        view=wizard.answer('owner','field',ANSWERS[view['next_field']],view['revision'])
    with pytest.raises(ValueError):
        wizard.answer('owner','field',value,view['revision'])
    assert wizard.get('owner','field')==view
    assert wizard.answer('owner','field','10,0',view['revision'])['answers']['counts']==[10,0]


def test_current_cycle_timezone_and_closure_are_revalidated_before_save(setup):  # noqa: F811
    records,_=setup
    cycle=create(records)['cycle']['id']
    wizard=ScoutingConsultation(records.db)
    view=complete(wizard)
    records.db.execute("UPDATE farms SET timezone='Asia/Taipei' WHERE id='farm'")
    with pytest.raises(ValueError,match='timezone changed'):
        wizard.save('owner','field',view['draft_id'],view['revision'])
    records.db.execute("UPDATE farms SET timezone='UTC' WHERE id='farm'")
    records.close_cycle('owner','field',cycle,'2026-07-05','Synthetic closure','Synthetic notebook',request_id='close',expected_revision=None)
    with pytest.raises(ValueError):
        wizard.save('owner','field',view['draft_id'],view['revision'])
    assert wizard.get('owner','field')==view
    assert not any(r['kind']=='scouting' for r in records.get('owner','field',cycle)['records'])
    wizard.cancel('owner','field',view['revision'])
    assert wizard.get('owner','field') is None


def test_changed_planting_details_require_new_review(setup):  # noqa: F811
    records,_=setup
    cycle=create(records)['cycle']['id']
    wizard=ScoutingConsultation(records.db)
    view=complete(wizard)
    records.correct_planting('owner','field',cycle,'onion','2026-07-01','Fixture','Correct crop',request_id='amend',expected_revision=records.get('owner','field',cycle)['planting']['revision_id'])
    with pytest.raises(ValueError,match='planting details changed'):
        wizard.save('owner','field',view['draft_id'],view['revision'])
    assert wizard.get('owner','field')==view


def test_owner_isolation_export_erasure_and_mutually_exclusive_intakes(setup):  # noqa: F811
    records,farms=setup
    create(records)
    create(records,'other',owner='other')
    wizard=ScoutingConsultation(records.db)
    a=wizard.start('owner','field')
    b=wizard.start('other','other')
    with pytest.raises(ValueError):
        wizard.get('other','field')
    with pytest.raises(ValueError,match='scouting'):
        FieldConsultation(records.db).start('owner','field')
    wizard.cancel('owner','field',a['revision'])
    FieldConsultation(records.db).start('owner','field')
    with pytest.raises(ValueError,match='field planning'):
        wizard.start('owner','field')
    assert farms.export('owner')['scouting_intakes']==[]
    assert len(farms.export('other')['scouting_intakes'])==1
    farms.purge('owner')
    assert wizard.get('other','other')==b
    farms.purge('other')
    assert not records.db.query('SELECT * FROM scouting_intake_drafts')


def test_save_and_draft_removal_rollback_together(setup):  # noqa: F811
    records,_=setup
    cycle=create(records)['cycle']['id']
    wizard=ScoutingConsultation(records.db)
    view=complete(wizard)
    records.db.execute("CREATE TRIGGER reject_delete BEFORE DELETE ON scouting_intake_drafts BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    with pytest.raises(sqlite3.IntegrityError,match='synthetic failure'):
        wizard.save('owner','field',view['draft_id'],view['revision'])
    assert wizard.get('owner','field')==view
    assert len(records.get('owner','field',cycle)['records'])==1


def test_crop_operations_dispatch_and_missing_cycles(setup):  # noqa: F811
    records,_=setup
    with pytest.raises(ValueError,match='Create a crop cycle'):
        execute_crop_records(records.db,'owner','field','start_scouting',{})
    create(records)
    view=execute_crop_records(records.db,'owner','field','start_scouting',{})
    while view['status']!='ready':
        view=execute_crop_records(records.db,'owner','field','answer_scouting',dict(answer=ANSWERS[view['next_field']],expected_revision=view['revision']))
    result=execute_crop_records(records.db,'owner','field','save_scouting',dict(draft_id=view['draft_id'],expected_revision=view['revision']))
    assert result['record']['kind']=='scouting'


def test_v17_upgrade_preserves_records_and_private_backup(setup):  # noqa: F811
    records,_=setup
    cycle=create(records)['cycle']['id']
    # Remove later additions before constructing the v17 migration fixture.
    records.db.execute('DROP TABLE measurement_intake_receipts')
    records.db.execute('DROP TABLE measurement_intake_drafts')
    records.db.execute('DROP TABLE water_log_drafts')
    records.db.execute('DROP TABLE planting_intake_drafts')
    records.db.execute('DROP TABLE scouting_intake_drafts')
    records.db.execute('PRAGMA user_version=17')
    path=records.db.path
    upgraded=_Db(path)
    try:
        assert upgraded.query('PRAGMA user_version')[0][0]==SCHEMA_VERSION
        assert ScoutingConsultation(upgraded).start('owner','field')['next_field']=='cycle_id'
        backup=next(path.parent.glob(f'{path.name}.pre-v{SCHEMA_VERSION}-*.bak'))
        assert backup.stat().st_mode & 0o777==0o600
        with sqlite3.connect(backup) as conn:
            assert conn.execute('PRAGMA user_version').fetchone()[0]==17
            assert conn.execute('SELECT id FROM production_cycles').fetchone()[0]==cycle
            assert conn.execute("SELECT name FROM sqlite_master WHERE name='scouting_intake_drafts'").fetchone() is None
    finally:
        upgraded._conn.close()


def test_failed_v18_migration_preserves_old_version(setup,monkeypatch):  # noqa: F811
    from agronaut_agent import scouting_consultation
    records,_=setup
    create(records)
    records.db.execute('DROP TABLE measurement_intake_receipts')
    records.db.execute('DROP TABLE measurement_intake_drafts')
    records.db.execute('DROP TABLE water_log_drafts')
    records.db.execute('DROP TABLE planting_intake_drafts')
    records.db.execute('DROP TABLE scouting_intake_drafts')
    records.db.execute('PRAGMA user_version=17')
    monkeypatch.setattr(scouting_consultation,'SCHEMA','CREATE TABLE broken (')
    with pytest.raises(sqlite3.OperationalError):
        _Db(records.db.path)
    assert records.db.query('PRAGMA user_version')[0][0]==17
    assert not records.db.query("SELECT name FROM sqlite_master WHERE name='scouting_intake_drafts'")


def test_observation_date_rejects_future_and_preplant_values_without_advancing(setup):  # noqa: F811
    from datetime import datetime, timedelta, timezone

    records,_=setup
    create(records)
    wizard=ScoutingConsultation(records.db)
    first=wizard.start('owner','field')
    view=wizard.answer('owner','field','1',first['revision'])
    for value in ['2026-06-30',(datetime.now(timezone.utc).date()+timedelta(days=2)).isoformat()]:
        with pytest.raises(ValueError,match='between planting and today'):
            wizard.answer('owner','field',value,view['revision'])
        assert wizard.get('owner','field')==view
