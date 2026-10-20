"""Actual harvest drafts preserve crop scope, units, provenance and atomic receipts."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from agronaut_agent.harvest_consultation import HarvestConsultation, render_harvest_intake
from agronaut_agent.store import SCHEMA_VERSION, _Db
from agronaut_agent.tests.test_crop_records import create, harvest, setup  # noqa: F401

ANSWERS = dict(cycle_id='1', batch='batch-1', day='2026-07-06', product='tomato fruit', unit='kg',
               quantity='12.5', basis='1', grade='ungraded', source='Synthetic dated weighing notebook', provenance='measured')


def complete(wizard, unit='field', language='en', stop=None, **changes):
    view = wizard.start('owner', unit, language)
    while view['status'] != 'ready' and view['next_field'] != stop:
        view = wizard.answer('owner', unit, dict(ANSWERS, **changes)[view['next_field']], view['revision'])
    return view


@pytest.mark.parametrize('unit', ['field', 'hydro', 'aqua'])
@pytest.mark.parametrize('language', ['en', 'fr', 'zh-Hant'])
def test_review_save_restart_retry_and_season_totals(setup, unit, language):  # noqa: F811
    records, farms = setup
    cycle = create(records, unit)['cycle']['id']
    wizard = HarvestConsultation(records.db)
    view = complete(wizard, unit, language)
    assert not records.get('owner', unit, cycle)['harvest_totals']
    preview = render_harvest_intake(view)
    assert '12.5' in preview and 'kg' in preview and 'batch-1' in preview and cycle in preview
    assert farms.export('owner')['harvest_intakes'][0]['language'] == language
    reopened = _Db(records.db.path)
    try:
        resumed = HarvestConsultation(reopened)
        assert resumed.start('owner', unit) == view
        saved = resumed.save('owner', unit, view['draft_id'], view['revision'])
        assert saved['record']['data']['quantity'] == 12.5
        assert saved['record']['data']['basis'] == 'fresh'
        assert saved['record']['operation']['intake']['language'] == language
        assert resumed.get('owner', unit) is None
        new = resumed.start('owner', unit)
        assert resumed.save('owner', unit, view['draft_id'], view['revision']) == saved
        assert resumed.get('owner', unit) == new
        with pytest.raises(ValueError, match='changed'):
            resumed.save('owner', unit, view['draft_id'], 'different-revision')
    finally:
        reopened._conn.close()
    journal = records.get('owner', unit, cycle)
    assert len(journal['harvest_totals']) == 1 and journal['harvest_totals'][0]['quantity'] == '12.5'
    assert journal['lifecycle']['status'] == 'open'
    review = records.season_review('owner', unit, cycle)
    assert review['journal']['harvest_totals'] == journal['harvest_totals']
    assert saved['record']['id'] in {r['id'] for r in review['journal']['records']}


@pytest.mark.parametrize('value', ['0', '-1', 'unknown', 'NaN', 'Infinity', '1,5', '1e3', '1000000000001', '1.000000000000000001'])
def test_invalid_or_fractional_counts_do_not_advance(setup, value):  # noqa: F811
    records, _ = setup
    create(records)
    wizard = HarvestConsultation(records.db)
    view = complete(wizard, stop='quantity', unit='field')
    view = wizard.answer('owner', 'field', '', view['revision'], back=True)
    view = wizard.answer('owner', 'field', 'count', view['revision'])
    with pytest.raises(ValueError, match='positive decimal'):
        wizard.answer('owner', 'field', value, view['revision'])
    assert wizard.get('owner', 'field') == view


def test_preview_changes_back_cancel_and_language_invalidate_stale_save(setup):  # noqa: F811
    records, _ = setup
    create(records)
    wizard = HarvestConsultation(records.db)
    first = wizard.start('owner', 'field')
    with pytest.raises(ValueError, match='Complete'):
        wizard.save('owner', 'field', first['draft_id'], first['revision'])
    view = complete(wizard)
    translated = wizard.start('owner', 'field', 'fr')
    assert translated['answers'] == view['answers'] and translated['revision'] != view['revision']
    with pytest.raises(ValueError, match='changed'):
        wizard.save('owner', 'field', view['draft_id'], view['revision'])
    back = wizard.answer('owner', 'field', '', translated['revision'], back=True)
    assert back['next_field'] == 'provenance' and 'provenance' not in back['answers']
    with pytest.raises(ValueError, match='changed'):
        wizard.answer('owner', 'field', 'estimated', translated['revision'])
    ready = wizard.answer('owner', 'field', 'estimé', back['revision'])
    assert ready['answers']['provenance'] == 'estimated'
    wizard.cancel('owner', 'field', ready['revision'])
    assert wizard.get('owner', 'field') is None


def test_existing_and_concurrently_recorded_batches_never_double_count(setup):  # noqa: F811
    records, _ = setup
    cycle = create(records)['cycle']['id']
    wizard = HarvestConsultation(records.db)
    view = complete(wizard)
    records.record_harvest('owner', 'field', cycle, harvest(), request_id='parallel')
    with pytest.raises(ValueError, match='already exists'):
        wizard.save('owner', 'field', view['draft_id'], view['revision'])
    assert wizard.get('owner', 'field') == view
    wizard.cancel('owner', 'field', view['revision'])
    batch = complete(wizard, stop='batch')
    with pytest.raises(ValueError, match='already exists'):
        wizard.answer('owner', 'field', 'batch-1', batch['revision'])
    assert records.get('owner', 'field', cycle)['harvest_totals'][0]['batches'] == 1


@pytest.mark.parametrize('change', ['timezone', 'crop', 'closure'])
def test_context_changes_block_reviewed_save_and_still_allow_cancel(setup, change):  # noqa: F811
    records, _ = setup
    cycle = create(records)['cycle']['id']
    wizard = HarvestConsultation(records.db)
    view = complete(wizard)
    if change == 'timezone':
        records.db.execute("UPDATE farms SET timezone='Asia/Taipei' WHERE id='farm'")
    elif change == 'crop':
        records.db.execute("UPDATE production_cycles SET crop='onion' WHERE id=?", (cycle,))
    else:
        records.close_cycle('owner', 'field', cycle, '2026-07-04', 'Synthetic closure', 'Synthetic notebook',
                            request_id='close', expected_revision=None)
    with pytest.raises(ValueError):
        wizard.save('owner', 'field', view['draft_id'], view['revision'])
    assert not records.get('owner', 'field', cycle)['harvest_totals']
    wizard.cancel('owner', 'field', view['revision'])


def test_dates_respect_planting_today_and_closed_cycle(setup):  # noqa: F811
    records, _ = setup
    cycle = create(records)['cycle']['id']
    records.close_cycle('owner', 'field', cycle, '2026-07-07', 'Synthetic closure', 'Synthetic notebook',
                        request_id='close', expected_revision=None)
    wizard = HarvestConsultation(records.db)
    view = complete(wizard, stop='day')
    for value in ('2026-02-30', '2026-06-30', '2026-07-08', (datetime.now(timezone.utc).date()+timedelta(days=2)).isoformat()):
        with pytest.raises(ValueError, match='actual harvest date'):
            wizard.answer('owner', 'field', value, view['revision'])
    ready = complete(wizard)
    saved = wizard.save('owner', 'field', ready['draft_id'], ready['revision'])
    assert saved['record']['data']['day'] == '2026-07-06'
    assert records.get('owner', 'field', cycle)['lifecycle']['status'] == 'closed'


def test_atomic_save_failure_leaves_draft_and_no_harvest(setup):  # noqa: F811
    records, _ = setup
    cycle = create(records)['cycle']['id']
    wizard = HarvestConsultation(records.db)
    view = complete(wizard)
    records.db.execute("CREATE TRIGGER refuse_harvest_delete BEFORE DELETE ON harvest_intake_drafts BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='synthetic failure'):
        wizard.save('owner', 'field', view['draft_id'], view['revision'])
    assert wizard.get('owner', 'field') == view
    assert not records.get('owner', 'field', cycle)['harvest_totals']


def test_owner_export_and_erasure_preserve_other_owner(setup):  # noqa: F811
    records, farms = setup
    create(records)
    create(records, 'other', owner='other')
    wizard = HarvestConsultation(records.db)
    view = complete(wizard)
    wizard.start('other', 'other')
    for operation in (lambda: wizard.get('other', 'field'), lambda: wizard.start('other', 'field'),
                      lambda: wizard.save('other', 'field', view['draft_id'], view['revision'])):
        with pytest.raises(ValueError):
            operation()
    assert not wizard.active('other', 'field')
    assert len(farms.export('owner')['harvest_intakes']) == 1
    farms.purge('owner')
    assert not farms.export('owner')['harvest_intakes']
    assert len(farms.export('other')['harvest_intakes']) == 1


def test_v21_upgrade_preserves_records_and_private_backup(setup):  # noqa: F811
    records, _ = setup
    cycle = create(records)['cycle']['id']
    original = records.record_harvest('owner', 'field', cycle, harvest(), request_id='before-upgrade')
    records.db.execute('DROP TABLE harvest_intake_drafts')
    records.db.execute('PRAGMA user_version=21')
    upgraded = _Db(records.db.path)
    try:
        assert upgraded.query('PRAGMA user_version')[0][0] == SCHEMA_VERSION
        assert HarvestConsultation(upgraded).start('owner', 'field')['next_field'] == 'cycle_id'
        assert any(original['id'] in row[0] for row in upgraded.query('SELECT payload FROM crop_records'))
        backup = next(records.db.path.parent.glob(f'{records.db.path.name}.pre-v{SCHEMA_VERSION}-*.bak'))
        assert backup.stat().st_mode & 0o777 == 0o600
        with sqlite3.connect(backup) as conn:
            assert conn.execute('PRAGMA user_version').fetchone()[0] == 21
            assert not conn.execute("SELECT name FROM sqlite_master WHERE name='harvest_intake_drafts'").fetchall()
    finally:
        upgraded._conn.close()


def test_failed_upgrade_retains_v21(setup, monkeypatch):  # noqa: F811
    from agronaut_agent import harvest_consultation

    records, _ = setup
    records.db.execute('DROP TABLE harvest_intake_drafts')
    records.db.execute('PRAGMA user_version=21')
    monkeypatch.setattr(harvest_consultation, 'SCHEMA', 'CREATE TABLE broken (')
    with pytest.raises(sqlite3.OperationalError):
        _Db(records.db.path)
    assert records.db.query('PRAGMA user_version')[0][0] == 21
    assert not records.db.query("SELECT name FROM sqlite_master WHERE name='harvest_intake_drafts'")


@pytest.mark.parametrize('unit,quantity,condition', [('kg', '12.5', 'dry'), ('g', '125', 'as_harvested'),
    ('t', '0.125', 'fresh'), ('count', '12', 'as_harvested')])
def test_explicit_units_basis_and_estimates_remain_on_the_saved_batch(setup, unit, quantity, condition):  # noqa: F811
    records, _ = setup
    create(records)
    wizard = HarvestConsultation(records.db)
    view = complete(wizard, stop='unit')
    with pytest.raises(ValueError, match='Choose kg'):
        wizard.answer('owner', 'field', 'baskets', view['revision'])
    wizard.answer('owner', 'field', unit, view['revision'])
    view = complete(wizard, quantity=quantity, basis=condition, provenance='estimated')
    result = wizard.save('owner', 'field', view['draft_id'], view['revision'])['record']['data']
    assert result['quantity'] == float(quantity) and result['unit'] == unit
    assert result['basis'] == condition and result['provenance'] == 'estimated'
