"""Raw reading review, time interpretation, atomic receipts and owner privacy."""

import sqlite3

import pytest

from agro_model.farm import OBSERVATION_UNITS
from agronaut_agent.field_consultation import FieldConsultation
from agronaut_agent.measurement_consultation import (
    MeasurementConsultation,
    reading_time,
    render_measurement_intake,
)
from agronaut_agent.planting_consultation import PlantingConsultation
from agronaut_agent.scouting_consultation import ScoutingConsultation
from agronaut_agent.store import SCHEMA_VERSION, _Db
from agronaut_agent.tests.test_crop_records import create, setup  # noqa: F401
from agronaut_agent.water_log_consultation import WaterLogConsultation

ANSWERS = dict(cycle_id='none', quantity='flow', value='25.5', observed_at='2026-07-06 15:30',
               source='Synthetic meter reading at inlet; steady flow at the stated time', provenance='measured')


def complete(wizard, unit='field', language='en', **changes):
    view = wizard.start('owner', unit, language)
    answers = dict(ANSWERS, **changes)
    while view['status'] != 'ready':
        view = wizard.answer('owner', unit, answers[view['next_field']], view['revision'])
    return view


@pytest.mark.parametrize('unit', ['field', 'hydro', 'aqua'])
@pytest.mark.parametrize('language', ['en', 'fr', 'zh-Hant'])
def test_review_restart_atomic_save_and_receipt_preserve_reading(setup, unit, language):  # noqa: F811
    records, farms = setup
    wizard = MeasurementConsultation(records.db)
    ready = complete(wizard, unit, language)
    assert farms.observations('owner', unit) == []
    assert '25.5 L/h' in render_measurement_intake(ready)
    assert farms.export('owner')['measurement_intakes'][0]['data']['entered_time'] == ANSWERS['observed_at']
    reopened = _Db(records.db.path)
    try:
        other = MeasurementConsultation(reopened)
        assert other.start('owner', unit) == ready
        saved = other.save('owner', unit, ready['draft_id'], ready['revision'])
        assert saved['record']['observed_at'] == '2026-07-06T15:30:00+00:00'
        assert saved['record']['quality'] == 'unverified' and saved['record']['unit'] == 'L/h'
        new = other.start('owner', unit)
        assert other.save('owner', unit, ready['draft_id'], ready['revision']) == saved
        assert other.get('owner', unit) == new
    finally:
        reopened._conn.close()
    records.review_measurement('owner', unit, saved['record']['id'], 'rejected', 'Synthetic review', 'Synthetic notebook',
                               request_id='reject', expected_revision=None)
    assert wizard.save('owner', unit, ready['draft_id'], ready['revision']) == saved
    assert farms.observations('owner', unit)[0]['quality_review']['quality'] == 'rejected'
    exported = farms.export('owner')
    receipt = exported['measurement_intake_receipts'][0]
    assert receipt['answers']['entered_time'] == ANSWERS['observed_at'] and receipt['result'] == saved
    assert not exported['water_days'] and not exported['calculations'] and not exported['crop_records']


@pytest.mark.parametrize('quantity', list(OBSERVATION_UNITS))
def test_each_supported_quantity_keeps_exact_unit_without_creating_derived_records(setup, quantity):  # noqa: F811
    records, farms = setup
    wizard = MeasurementConsultation(records.db)
    ready = complete(wizard, quantity=quantity, value='-3.5' if quantity=='air_temperature' else '.25')
    saved = wizard.save('owner', 'field', ready['draft_id'], ready['revision'])['record']
    assert saved['quantity'] == quantity and saved['unit'] == OBSERVATION_UNITS[quantity]
    assert saved['value'] == (-3.5 if quantity=='air_temperature' else .25)
    assert farms.export('owner')['crop_records'] == []
    assert farms.export('owner')['water_days'] == []


@pytest.mark.parametrize('zone,entered,expected', [
    ('Africa/Ouagadougou', '2025-07-06 15:30', '2025-07-06T15:30:00+00:00'),
    ('Asia/Taipei', '2025-07-06 15:30', '2025-07-06T15:30:00+08:00'),
    ('America/New_York', '2025-11-02T01:30:00-04:00', '2025-11-02T01:30:00-04:00'),
    ('America/New_York', '2025-11-02T01:30:00-05:00', '2025-11-02T01:30:00-05:00'),
    ('Asia/Taipei', '2025-07-06T23:30:00Z', '2025-07-06T23:30:00+00:00'),
])
def test_time_interpretation_preserves_explicit_offsets(zone, entered, expected):
    assert reading_time(entered, zone) == expected


@pytest.mark.parametrize('entered,zone,message', [
    ('2025-11-02 01:30', 'America/New_York', 'occurs twice'),
    ('2025-03-09 02:30', 'America/New_York', 'does not exist'),
    ('2025-01-01', 'UTC', 'valid date'),
    ('2025-02-30 12:00', 'UTC', 'valid date'),
    ('2025-01-01T12:00:00+00:60', 'UTC', 'valid date'),
    ('9999-01-01 12:00', 'UTC', 'future'),
])
def test_ambiguous_missing_or_invalid_times_require_clarification(entered, zone, message):
    with pytest.raises(ValueError, match=message):
        reading_time(entered, zone)


def test_advanced_api_preserves_forecast_support_while_actual_intake_refuses_future(setup):  # noqa: F811
    records, _ = setup
    data = dict(quantity='air_temperature', value=25, unit='degC', observed_at='2099-07-06T12:00:00+00:00',
                provenance='forecast', source='Synthetic future scenario')
    saved = records.record_measurement('owner', 'field', data, request_id='forecast')
    assert saved['provenance'] == 'forecast'
    with pytest.raises(ValueError, match='future'):
        reading_time(data['observed_at'], 'UTC')


@pytest.mark.parametrize('quantity,value', [('soil_water_content','25'), ('flow','-1'), ('runtime','unknown'),
    ('pressure','NaN'), ('flow','1_000'), ('flow','25 L/h'), ('flow','2,5'), ('flow','0.'+'0'*350+'1')])
def test_invalid_values_cannot_advance_or_become_zero(setup, quantity, value):  # noqa: F811
    records, _ = setup
    wizard = MeasurementConsultation(records.db)
    view = wizard.start('owner', 'field')
    for answer in ('none', quantity):
        view = wizard.answer('owner', 'field', answer, view['revision'])
    with pytest.raises(ValueError):
        wizard.answer('owner', 'field', value, view['revision'])
    assert wizard.get('owner', 'field') == view


def test_preplant_readings_allowed_but_closed_cycle_uses_farm_local_day(setup):  # noqa: F811
    records, _ = setup
    cycle = create(records)['cycle']['id']
    records.db.execute("UPDATE farms SET timezone='Asia/Taipei' WHERE id='farm'")
    records.close_cycle('owner', 'field', cycle, '2026-07-06', 'Synthetic close', 'Notebook', request_id='close', expected_revision=None)
    wizard = MeasurementConsultation(records.db)
    ready = complete(wizard, cycle_id=cycle, observed_at='2026-06-29 12:00')
    wizard.save('owner', 'field', ready['draft_id'], ready['revision'])
    view = wizard.start('owner', 'field')
    for value in (cycle, 'flow', '10'):
        view = wizard.answer('owner', 'field', value, view['revision'])
    with pytest.raises(ValueError, match='after the crop cycle end'):
        wizard.answer('owner', 'field', '2026-07-06T23:00:00Z', view['revision'])
    assert wizard.get('owner', 'field') == view
    view = wizard.answer('owner', 'field', '2026-07-06T15:59:00Z', view['revision'])
    assert view['next_field'] == 'source'


def test_stale_back_language_context_owner_conflicts_and_erase(setup):  # noqa: F811
    records, farms = setup
    wizard = MeasurementConsultation(records.db)
    ready = complete(wizard)
    changed = wizard.start('owner', 'field', 'fr')
    assert changed['answers'] == ready['answers'] and changed['revision'] != ready['revision']
    with pytest.raises(ValueError):
        wizard.save('owner', 'field', ready['draft_id'], ready['revision'])
    for other in (PlantingConsultation(records.db), ScoutingConsultation(records.db), FieldConsultation(records.db), WaterLogConsultation(records.db)):
        with pytest.raises(ValueError, match='measurement entry'):
            other.start('owner', 'field')
    with pytest.raises(ValueError):
        wizard.start('other', 'field')
    saved = wizard.save('owner', 'field', changed['draft_id'], changed['revision'])
    assert saved['language'] == 'fr'
    view = wizard.start('owner', 'field')
    view = wizard.answer('owner', 'field', 'none', view['revision'])
    view = wizard.answer('owner', 'field', '', view['revision'], back=True)
    assert view['answers'] == {}
    records.db.execute("UPDATE farms SET timezone='Asia/Taipei' WHERE id='farm'")
    with pytest.raises(ValueError, match='timezone or crop'):
        wizard.answer('owner', 'field', 'none', view['revision'])
    wizard.start('other', 'other')
    farms.purge('owner')
    assert not farms.export('owner')['measurement_intakes'] and not farms.export('owner')['measurement_intake_receipts']
    assert len(farms.export('other')['measurement_intakes']) == 1


@pytest.mark.parametrize('table', ['measurement_intake_receipts', 'measurement_intake_drafts'])
def test_receipt_or_draft_failure_rolls_back_observation(setup, table):  # noqa: F811
    records, farms = setup
    wizard = MeasurementConsultation(records.db)
    ready = complete(wizard)
    action = 'INSERT' if table.endswith('receipts') else 'DELETE'
    records.db.execute(f"CREATE TRIGGER fail_measurement BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        wizard.save('owner', 'field', ready['draft_id'], ready['revision'])
    assert farms.observations('owner', 'field') == []
    assert farms.export('owner')['measurement_intake_receipts'] == []
    assert wizard.get('owner', 'field') == ready


def test_v20_migration_backup_and_rollback_preserve_observations(setup, monkeypatch):  # noqa: F811
    from agronaut_agent import measurement_consultation

    records, _ = setup
    original = records.record_measurement('owner', 'field', dict(quantity='flow', value=10, unit='L/h',
        observed_at='2026-07-06T15:30:00+00:00', source='Synthetic meter', provenance='measured'), request_id='prior')
    records.db.execute('DROP TABLE measurement_intake_receipts')
    records.db.execute('DROP TABLE measurement_intake_drafts')
    records.db.execute('PRAGMA user_version=20')
    schema = measurement_consultation.SCHEMA
    monkeypatch.setattr(measurement_consultation, 'SCHEMA', (schema[0], 'CREATE TABLE broken ('))
    with pytest.raises(sqlite3.OperationalError):
        _Db(records.db.path)
    assert records.db.query('PRAGMA user_version')[0][0] == 20
    assert not records.db.query("SELECT 1 FROM sqlite_master WHERE name='measurement_intake_drafts'")
    monkeypatch.setattr(measurement_consultation, 'SCHEMA', schema)
    upgraded = _Db(records.db.path)
    try:
        assert upgraded.query('PRAGMA user_version')[0][0] == SCHEMA_VERSION
        assert MeasurementConsultation(upgraded).start('owner', 'field')['next_field'] == 'cycle_id'
        backups = list(records.db.path.parent.glob(f'*.pre-v{SCHEMA_VERSION}-*.bak'))
        assert backups
        for backup in backups:
            assert backup.stat().st_mode & 0o777 == 0o600
            with sqlite3.connect(backup) as conn:
                assert conn.execute('PRAGMA user_version').fetchone()[0] == 20
                assert conn.execute('SELECT id FROM field_observations').fetchone()[0] == original['id']
                assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='measurement_intake_receipts'").fetchone() is None
    finally:
        upgraded._conn.close()
