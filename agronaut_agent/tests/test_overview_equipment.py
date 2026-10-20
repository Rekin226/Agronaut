"""One owned farm snapshot connects tasks with current equipment reservations."""
from copy import deepcopy
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from agronaut_agent.crop_records import CropRecords
from agronaut_agent.equipment_calendar import EquipmentCalendar
from agronaut_agent.farm_overview import FarmOverview
from agronaut_agent.farm_overview_text import render_overview
from agronaut_agent.tests import test_farm_resources
from agronaut_agent.tests.test_equipment_calendar import asset, book, prepared
from agronaut_agent.tests.test_field_workflow import setup as setup

resources_setup = test_farm_resources.setup


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr('agronaut_agent.equipment_calendar.datetime', Clock)


def reserve(calendar, farm='farm', owner='owner', key='booking',
            start='2026-10-20T16:00:00Z', end='2026-10-20T17:00:00Z', pump=None, **kwargs):
    pump = pump or calendar.save_equipment(owner, farm, asset(), request_id='pump')
    zone = ZoneInfo(calendar.db.query('SELECT timezone FROM farms WHERE id=?', (farm,))[0][0])
    start, end = (datetime.fromisoformat(value).astimezone(zone).isoformat() for value in (start, end))
    data = dict(equipment_ids=[pump['id']], start=start, end=end,
                title='Synthetic shared pump booking', source='Synthetic equipment diary')
    return calendar.reserve_equipment(owner, farm, data, request_id=key, **kwargs)


def equipment(workflow, when='2026-10-20T16:00:00+00:00', farm='farm'):
    return FarmOverview(workflow.db).get('owner', farm, now=datetime.fromisoformat(when))['farms'][0]['equipment']


def test_farm_timezones_owners_and_read_only_snapshot(setup):
    workflow, farms = setup
    calendar = EquipmentCalendar(workflow.db)
    workflow.db.execute("UPDATE farms SET timezone='Africa/Ouagadougou' WHERE id='farm'")
    workflow.db.execute("UPDATE farms SET timezone='Asia/Taipei' WHERE id='farm2'")
    for farm, owner in [('farm', 'owner'), ('farm2', 'owner'), ('farm3', 'other')]:
        reserve(calendar, farm, owner)
    before = farms.export('owner')
    report = FarmOverview(workflow.db).get('owner', now=datetime(2026, 10, 20, 16, tzinfo=timezone.utc))
    assert [f['farm']['id'] for f in report['farms']] == ['farm', 'farm2']
    assert [f['local_date'] for f in report['farms']] == ['2026-10-20', '2026-10-21']
    for farm in report['farms']:
        data = farm['equipment']
        assert data['counts'] == dict(active=1, today=1, ongoing=1, upcoming=0, elapsed=0, needs_review=0)
        assert len(data['attention']) == 1
        assert 'plan_snapshot' not in data['bookings'][0]
    assert report['farms'][1]['equipment']['bookings'][0]['intervals'][0]['local_start'] == '2026-10-21T00:00:00+08:00'
    assert farms.export('owner') == before
    with pytest.raises(ValueError, match='unknown farm'):
        FarmOverview(workflow.db).get('other', 'farm')


@pytest.mark.parametrize('when,timing,today', [
    ('2026-10-20T15:59:59+00:00', 'upcoming', 1),
    ('2026-10-20T16:00:00+00:00', 'ongoing', 1),
    ('2026-10-20T17:00:00+00:00', 'elapsed', 1),
    ('2026-10-21T00:00:00+00:00', 'elapsed', 0),
])
def test_half_open_intervals_never_imply_actual_work(setup, when, timing, today):
    workflow, farms = setup
    reserve(EquipmentCalendar(workflow.db))
    view = equipment(workflow, when)
    assert view['bookings'][0]['timing'] == timing
    assert view['counts'][timing] == 1 and view['counts']['today'] == today
    assert len(view['attention']) == today
    assert not farms.export('owner')['water_days']
    assert not farms.export('owner')['crop_records']


@pytest.mark.parametrize('zone,when,start,end', [
    ('Asia/Taipei', '2026-10-20T16:00:00+00:00', '2026-10-20T15:00:00Z', '2026-10-20T16:00:00Z'),
    ('Europe/Paris', '2026-03-29T12:00:00+00:00', '2026-03-29T22:00:00Z', '2026-03-29T23:00:00Z'),
    ('Europe/Paris', '2026-10-25T12:00:00+00:00', '2026-10-24T21:00:00Z', '2026-10-24T22:00:00Z'),
])
def test_local_midnight_and_dst_boundaries_exclude_touching_intervals(setup, zone, when, start, end):
    workflow, _ = setup
    workflow.db.execute("UPDATE farms SET timezone=? WHERE id='farm'", (zone,))
    reserve(EquipmentCalendar(workflow.db), start=start, end=end)
    view = equipment(workflow, when)
    assert view['counts']['today'] == 0 and view['attention'] == []


@pytest.mark.parametrize('zone', [None, 'Invalid/Timezone'])
def test_unknown_timezone_keeps_utc_intervals_and_flags_booking_review(setup, zone):
    workflow, _ = setup
    reserve(EquipmentCalendar(workflow.db))
    workflow.db.execute("UPDATE farms SET timezone=? WHERE id='farm'", (zone,))
    view = equipment(workflow)
    assert view['counts']['today'] is None
    booking = view['attention'][0]
    assert booking['review']['timezone_changed'] and booking['review']['needs_review']
    interval = booking['intervals'][0]
    assert interval['local_start'] is None and interval['today'] is None
    assert interval['timing'] == 'ongoing' and interval['start'] == '2026-10-20T16:00:00+00:00'


def test_only_current_active_bookings_appear_and_equipment_correction_is_visible(setup):
    workflow, farms = setup
    calendar = EquipmentCalendar(workflow.db)
    pump = calendar.save_equipment('owner', 'farm', asset(), request_id='pump')
    old = reserve(calendar, pump=pump)
    updated = reserve(calendar, key='replace', start='2026-10-21T16:00:00Z', end='2026-10-21T17:00:00Z',
        pump=pump, supersedes_id=old['id'], reason='Reschedule')
    calendar.save_equipment('owner', 'farm', asset(status='unavailable'), request_id='broken',
        supersedes_id=pump['id'], reason='Requires repair')
    before = farms.export('owner')
    view = equipment(workflow)
    assert [b['booking_id'] for b in view['bookings']] == [updated['id']]
    assert view['counts']['today'] == 0 and view['counts']['needs_review'] == 1
    assert view['attention'][0]['review']['unavailable_equipment'] == [pump['root_id']]
    assert view['attention'][0]['review']['equipment_changed'] == [pump['root_id']]
    assert farms.export('owner') == before
    calendar.release_equipment('owner', 'farm', updated['id'], 'Operator', 'Cancel', request_id='release')
    assert equipment(workflow)['bookings'] == []


def test_timed_plan_booking_retains_work_ids_and_changed_plan_review(resources_setup):
    service, farms = resources_setup
    calendar, pump, plan = prepared(service)
    saved = book(calendar, pump, plan)
    CropRecords(service.db).correct_planting('owner', 'unit', 'cycle', 'pepper', '2026-07-02', 'Synthetic', 'Correction',
        request_id='correct', expected_revision=None)
    before = farms.export('owner')
    view = equipment(service)
    assert view['counts']['active'] == 1  # Two work intervals are one booking.
    row = view['attention'][0]
    assert row['booking_id'] == saved['id'] and row['plan_id'] == plan['id']
    assert {i['work_id'] for i in row['intervals']} == {w['id'] for w in plan['timing_inputs']['work']}
    assert row['review'] == calendar.equipment_calendar('owner', 'farm')['active'][0]['review']
    assert row['review']['plan']['needs_review']
    assert farms.export('owner') == before


@pytest.mark.parametrize('language', ['en', 'fr', 'zh-Hant'])
def test_equipment_pages_are_bounded_contextual_and_complete(setup, language):
    workflow, _ = setup
    calendar = EquipmentCalendar(workflow.db)
    reserve(calendar)
    report = FarmOverview(workflow.db).get('owner', 'farm', now=datetime(2026, 10, 20, 16, tzinfo=timezone.utc))
    farm = report['farms'][0]
    farm['farm']['name'] = 'FARM🌱'*100
    original = farm['equipment']['attention'][0]
    original['review'].update(equipment_changed=['x'], unavailable_equipment=['x'], timezone_changed=True)
    original['intervals'][0].update(equipment_name='PUMP🌱'*100, equipment_key='KEY🌱'*100)
    farm['equipment']['attention'] = [dict(original, title=f'BOOK-{index:02d} '+'🌱'*500, source='SOURCE🌱'*100)
                                      for index in range(30)]
    before = deepcopy(report)
    pages = []
    for page in range(1, 100):
        rendered = render_overview(report, language, page)
        assert len(rendered.encode('utf-16-le'))//2 < 3500
        assert 'FARM' in rendered
        pages.append(rendered)
        if f'/overview {language} {page+1}' not in rendered:
            break
    assert len(pages) > 1
    for index in range(30):
        assert sum(page.count(f'BOOK-{index:02d}') for page in pages) == 1
    assert report == before
