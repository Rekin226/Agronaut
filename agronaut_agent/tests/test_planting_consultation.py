"""Reviewed actual planting creates a cycle once and keeps draft state private."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from agronaut_agent.field_consultation import FieldConsultation
from agronaut_agent.planting_consultation import PlantingConsultation, render_planting_intake
from agronaut_agent.scouting_consultation import ScoutingConsultation
from agronaut_agent.store import SCHEMA_VERSION, _Db
from agronaut_agent.tests.test_crop_records import create, setup  # noqa: F401

ANSWERS=dict(crop='Tomato',planted_on='2026-07-01',source='Synthetic dated planting notebook')


def complete(wizard,unit='field',language='en'):
    view=wizard.start('owner',unit,language)
    for key in ('crop','planted_on','source'):
        assert view['next_field']==key
        view=wizard.answer('owner',unit,ANSWERS[key],view['revision'])
    return view


@pytest.mark.parametrize('unit',['field','hydro','aqua'])
@pytest.mark.parametrize('language',['en','fr','zh-Hant'])
def test_review_atomic_save_restart_and_receipt_are_shared_by_all_crop_domains(setup,unit,language):  # noqa: F811
    records,farms=setup
    wizard=PlantingConsultation(records.db)
    view=complete(wizard,unit,language)
    assert view['status']=='ready' and farms.list_cycles('owner',unit)==[]
    assert ANSWERS['crop'] in render_planting_intake(view)
    assert farms.export('owner')['planting_intakes'][0]['language']==language
    reopened=_Db(records.db.path)
    try:
        resumed=PlantingConsultation(reopened)
        assert resumed.start('owner',unit)==view
        saved=resumed.save('owner',unit,view['draft_id'],view['revision'])
        assert saved['record']['data']==ANSWERS
        assert saved['cycle']['crop']=='Tomato'
        assert resumed.get('owner',unit) is None
        new=resumed.start('owner',unit)
        assert resumed.save('owner',unit,view['draft_id'],view['revision'])==saved
        assert resumed.get('owner',unit)==new
    finally:
        reopened._conn.close()
    assert len(farms.list_cycles('owner',unit))==1


def test_back_cancel_stale_answers_and_language_changes_preserve_drafts(setup):  # noqa: F811
    records,farms=setup
    wizard=PlantingConsultation(records.db)
    initial=wizard.start('owner','field')
    with pytest.raises(ValueError,match='Complete'):
        wizard.save('owner','field',initial['draft_id'],initial['revision'])
    second=wizard.answer('owner','field','Tomato',initial['revision'])
    with pytest.raises(ValueError,match='changed'):
        wizard.answer('owner','field','Other',initial['revision'])
    back=wizard.answer('owner','field','',second['revision'],back=True)
    assert back['answers']=={} and back['next_field']=='crop'
    view=complete(wizard)
    changed=wizard.start('owner','field','fr')
    assert changed['answers']==view['answers'] and changed['revision']!=view['revision']
    with pytest.raises(ValueError,match='changed'):
        wizard.save('owner','field',view['draft_id'],view['revision'])
    wizard.cancel('owner','field',changed['revision'])
    assert farms.list_cycles('owner','field')==[]


@pytest.mark.parametrize('language,token',[('en','new'),('fr','nouveau'),('zh-Hant','新增')])
def test_same_crop_and_day_need_explicit_new_cycle_confirmation(setup,language,token):  # noqa: F811
    records,farms=setup
    existing=create(records)
    wizard=PlantingConsultation(records.db)
    view=complete(wizard,language=language)
    assert view['next_field']=='duplicate' and view['matching_cycles'][0]['id']==existing['cycle']['id']
    with pytest.raises(ValueError,match='Complete'):
        wizard.save('owner','field',view['draft_id'],view['revision'])
    with pytest.raises(ValueError,match='Reply new'):
        wizard.answer('owner','field','save',view['revision'])
    view=wizard.answer('owner','field',token,view['revision'])
    saved=wizard.save('owner','field',view['draft_id'],view['revision'])
    assert len(farms.list_cycles('owner','field'))==2
    assert saved['record']['operation']['intake']['matching_cycle_ids']==[existing['cycle']['id']]


def test_concurrent_matching_cycle_invalidates_review_and_requires_resume(setup):  # noqa: F811
    records,farms=setup
    wizard=PlantingConsultation(records.db)
    view=complete(wizard)
    existing=create(records)
    with pytest.raises(ValueError,match='Matching crop cycles changed'):
        wizard.save('owner','field',view['draft_id'],view['revision'])
    refreshed=wizard.start('owner','field')
    assert refreshed['next_field']=='duplicate' and refreshed['revision']!=view['revision']
    ready=wizard.answer('owner','field','new',refreshed['revision'])
    create(records,request='another')
    with pytest.raises(ValueError,match='Matching crop cycles changed'):
        wizard.save('owner','field',ready['draft_id'],ready['revision'])
    revised=wizard.start('owner','field')
    assert revised['next_field']=='duplicate' and len(revised['matching_cycles'])==2
    back=wizard.answer('owner','field','',revised['revision'],back=True)
    assert not back['answers'].get('duplicate_confirmed')
    assert existing['cycle']['id'] in {cycle['id'] for cycle in farms.list_cycles('owner','field')}


def test_bad_dates_and_changed_timezone_do_not_create_cycles(setup):  # noqa: F811
    records,farms=setup
    wizard=PlantingConsultation(records.db)
    view=wizard.start('owner','field')
    view=wizard.answer('owner','field','Tomato',view['revision'])
    for value in ('tomorrow','2026-02-30',(datetime.now(timezone.utc).date()+timedelta(days=2)).isoformat()):
        with pytest.raises(ValueError,match='actual planting date'):
            wizard.answer('owner','field',value,view['revision'])
        assert wizard.get('owner','field')==view
    records.db.execute("UPDATE farms SET timezone='Asia/Taipei' WHERE id='farm'")
    with pytest.raises(ValueError,match='timezone changed'):
        wizard.answer('owner','field','2026-07-01',view['revision'])
    wizard.cancel('owner','field',view['revision'])
    assert farms.list_cycles('owner','field')==[]


def test_owner_export_erasure_and_draft_conflicts(setup):  # noqa: F811
    records,farms=setup
    wizard=PlantingConsultation(records.db)
    view=wizard.start('owner','field')
    wizard.start('other','other')
    with pytest.raises(ValueError):
        wizard.get('other','field')
    with pytest.raises(ValueError,match='planting'):
        ScoutingConsultation(records.db).start('owner','field')
    with pytest.raises(ValueError,match='planting'):
        FieldConsultation(records.db).start('owner','field')
    wizard.cancel('owner','field',view['revision'])
    field=FieldConsultation(records.db).start('owner','field')
    with pytest.raises(ValueError,match='Finish or cancel'):
        wizard.start('owner','field')
    FieldConsultation(records.db).cancel('owner','field',field['revision'])
    wizard.start('owner','field')
    farms.purge('owner')
    assert farms.export('owner')['planting_intakes']==[]
    assert len(farms.export('other')['planting_intakes'])==1


def test_failed_save_rolls_back_both_cycle_and_source_record(setup):  # noqa: F811
    records,farms=setup
    wizard=PlantingConsultation(records.db)
    view=complete(wizard)
    records.db.execute("CREATE TRIGGER refuse_delete BEFORE DELETE ON planting_intake_drafts BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    with pytest.raises(sqlite3.IntegrityError,match='synthetic failure'):
        wizard.save('owner','field',view['draft_id'],view['revision'])
    assert wizard.get('owner','field')==view
    assert farms.list_cycles('owner','field')==[]
    assert not records.db.query('SELECT * FROM crop_records')


def test_v18_upgrade_keeps_records_and_private_backup(setup):  # noqa: F811
    records,_=setup
    existing=create(records)
    records.db.execute('DROP TABLE measurement_intake_receipts')
    records.db.execute('DROP TABLE measurement_intake_drafts')
    records.db.execute('DROP TABLE water_log_drafts')
    records.db.execute('DROP TABLE planting_intake_drafts')
    records.db.execute('PRAGMA user_version=18')
    path=records.db.path
    upgraded=_Db(path)
    try:
        assert upgraded.query('PRAGMA user_version')[0][0]==SCHEMA_VERSION
        assert PlantingConsultation(upgraded).start('owner','field')['next_field']=='crop'
        backup=next(path.parent.glob(f'{path.name}.pre-v{SCHEMA_VERSION}-*.bak'))
        assert backup.stat().st_mode & 0o777==0o600
        with sqlite3.connect(backup) as conn:
            assert conn.execute('PRAGMA user_version').fetchone()[0]==18
            assert conn.execute('SELECT id FROM production_cycles').fetchone()[0]==existing['cycle']['id']
            assert conn.execute("SELECT name FROM sqlite_master WHERE name='planting_intake_drafts'").fetchone() is None
    finally:
        upgraded._conn.close()


def test_failed_v19_upgrade_leaves_v18_intact(setup,monkeypatch):  # noqa: F811
    from agronaut_agent import planting_consultation

    records,_=setup
    records.db.execute('DROP TABLE measurement_intake_receipts')
    records.db.execute('DROP TABLE measurement_intake_drafts')
    records.db.execute('DROP TABLE water_log_drafts')
    records.db.execute('DROP TABLE planting_intake_drafts')
    records.db.execute('PRAGMA user_version=18')
    monkeypatch.setattr(planting_consultation,'SCHEMA','CREATE TABLE broken (')
    with pytest.raises(sqlite3.OperationalError):
        _Db(records.db.path)
    assert records.db.query('PRAGMA user_version')[0][0]==18
    assert not records.db.query("SELECT name FROM sqlite_master WHERE name='planting_intake_drafts'")
