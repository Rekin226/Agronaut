"""Task decisions retain owned source revisions and surface subsequent changes."""
import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from agronaut_agent.crop_records import CropRecords
from agronaut_agent.farm_overview import FarmOverview
from agronaut_agent.farm_overview_text import render_overview
from agronaut_agent.observation_quality import ObservationReviews
from agronaut_agent.task_consultation import TaskConsultation, render_task_intake
from agronaut_agent.tests.test_crop_calendar import task
from agronaut_agent.tests.test_crop_records import harvest
from agronaut_agent.tests.test_field_workflow import setup as setup
from agronaut_agent.tests.test_field_workflow import water
from agronaut_agent.tests.test_measurement_entry import reading
from agronaut_agent.tests.test_task_consultation import finish


def scouting(**changes):
    return dict(dict(entry='scout-1',day='2026-07-06',location='North rows',symptoms='Yellow leaf margins; cause unknown',
        inspected_count=10,affected_count=2,source='Synthetic field notebook',provenance='observed'),**changes)


def link(row, kind='crop_record', **changes):
    return dict(dict(kind=kind,id=row['id'],reason='Inspect affected plants and compare nearby healthy plants'),**changes)


def calendar(records):
    return records.get('owner','field','cycle')['calendar']


def test_scouting_trigger_preserves_original_and_correction_flags_resolved_tasks(setup):
    workflow, farms = setup
    records = CropRecords(workflow.db)
    source = records.record_scouting('owner','field','cycle',scouting(),request_id='source')
    request = task(activity='scouting', trigger=link(source))
    planned = records.plan_task('owner','field','cycle',request,request_id='plan')
    assert planned['data']['trigger_snapshot'] == source
    assert calendar(records)['tasks'][0]['trigger_review'] == dict(state='current',needs_review=False)
    done = records.resolve_task('owner','field',planned['id'],'completed','Inspected','Operator',request_id='done',completed_on='2026-07-07')
    corrected = records.record_scouting('owner','field','cycle',scouting(affected_count=1),request_id='correction',supersedes_id=source['id'])
    item = calendar(records)['tasks'][0]
    assert item['status'] == 'completed' and item['trigger_review'] == dict(state='superseded',needs_review=True)
    assert item['completion_evidence_state'] == 'not_linked'
    assert records.plan_task('owner','field','cycle',request,request_id='plan') == planned
    assert done['data']['trigger_snapshot'] == source
    assert [c['id'] for c in calendar(records)['trigger_candidates']] == [corrected['id']]
    assert not farms.export('owner')['water_days']
    assert {r['kind'] for r in farms.export('owner')['crop_records']} == {'calendar','scouting'}


@pytest.mark.parametrize('kind', ['crop_record','measurement','water_day'])
def test_source_kinds_are_snapshotted_without_changing_originals(setup, kind):
    workflow, farms = setup
    records = CropRecords(workflow.db)
    if kind == 'crop_record':
        source = records.record_harvest('owner','field','cycle',harvest(),request_id='source')
    elif kind == 'water_day':
        source = workflow.record_day('owner','field',water(net_irrigation_mm=0),cycle_id='cycle',request_id='source')
    else:
        source = records.record_measurement('owner','field',reading(),cycle_id='cycle',request_id='source')
    trigger = link(source,kind,**({'review_revision_id':None} if kind == 'measurement' else {}))
    before = farms.export('owner')
    saved = records.plan_task('owner','field','cycle',task(trigger=trigger),request_id='plan')
    assert saved['data']['trigger']['id'] == source['id']
    assert saved['data']['trigger_snapshot']['id'] == source['id']
    for key in ('observations','observation_reviews','water_days'):
        assert farms.export('owner')[key] == before[key]
    candidates = calendar(records)['trigger_candidates']
    assert candidates[0]['kind'] == kind and candidates[0]['id'] == source['id']
    assert calendar(records)['tasks'][0]['trigger_review']['state'] == 'current'


def test_measurement_quality_revision_requires_explicit_adoption_and_review(setup):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    reviews = ObservationReviews(workflow.db)
    source = records.record_measurement('owner','field',reading(),cycle_id='cycle',request_id='source')
    trigger = link(source,'measurement',review_revision_id=None)
    saved = records.plan_task('owner','field','cycle',task(trigger=trigger),request_id='plan')
    checked = reviews.review('owner','field',source['id'],'checked','Checked notebook','Operator',request_id='check',expected_revision=None)
    assert calendar(records)['tasks'][0]['trigger_review']['state'] == 'review_changed'
    assert saved['data']['trigger_snapshot']['effective_quality'] == 'unverified'
    with pytest.raises(ValueError,match='review changed'):
        records.plan_task('owner','field','cycle',task(trigger=trigger),request_id='stale',supersedes_id=saved['id'])
    trigger['review_revision_id'] = checked['id']
    updated = records.plan_task('owner','field','cycle',task(trigger=trigger),request_id='reviewed',supersedes_id=saved['id'])
    assert not calendar(records)['tasks'][0]['needs_review']
    assert updated['data']['trigger_snapshot']['quality_review'] == checked
    reviews.review('owner','field',source['id'],'rejected','Wrong sensor','Operator',request_id='reject',expected_revision=checked['id'])
    assert calendar(records)['tasks'][0]['trigger_review']['state'] == 'rejected'
    assert calendar(records)['trigger_candidates'] == []


@pytest.mark.parametrize('fault', ['other_unit','other_owner','other_cycle','missing','calendar','voided','rejected','review_missing','review_wrong','injected_snapshot','blank_reason'])
def test_invalid_source_reference_never_saves_plan(setup, fault):
    workflow, farms = setup
    records = CropRecords(workflow.db)
    source = records.record_harvest('owner','field','cycle',harvest(),request_id='source')
    trigger = link(source)
    if fault in ('other_unit','other_owner'):
        unit, cycle, owner = ('field2','cycle2','owner') if fault == 'other_unit' else ('field3','cycle3','other')
        trigger = link(records.record_harvest(owner,unit,cycle,harvest(),request_id='foreign'))
    elif fault == 'other_cycle':
        cycle = records.create('owner','field','tomato','2026-07-01','Synthetic',request_id='other-cycle')['cycle']['id']
        trigger = link(records.record_harvest('owner','field',cycle,harvest(),request_id='other'))
    elif fault == 'missing':
        trigger['id'] = 'missing'
    elif fault == 'calendar':
        trigger = link(records.plan_task('owner','field','cycle',task(entry='existing'),request_id='other'))
    elif fault == 'voided':
        trigger = link(records.void_harvest('owner','field',source['id'],'Wrong batch',request_id='void'))
    elif fault in ('rejected','review_missing','review_wrong'):
        source = records.record_measurement('owner','field',reading(),cycle_id='cycle',request_id='measurement')
        trigger = link(source,'measurement')
        if fault == 'rejected':
            ObservationReviews(workflow.db).review('owner','field',source['id'],'rejected','Wrong sensor','Operator',request_id='reject',expected_revision=None)
            trigger['review_revision_id'] = None
        elif fault == 'review_wrong':
            trigger['review_revision_id'] = 'wrong'
    elif fault == 'injected_snapshot':
        trigger['snapshot'] = {'fake':'record'}
    else:
        trigger['reason'] = ''
    before = farms.export('owner')
    with pytest.raises(ValueError):
        records.plan_task('owner','field','cycle',task(trigger=trigger),request_id='bad')
    assert farms.export('owner') == before


def test_plan_revision_requires_explicit_unlink_and_preserves_history(setup):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    source = records.record_scouting('owner','field','cycle',scouting(),request_id='source')
    first = records.plan_task('owner','field','cycle',task(trigger=link(source)),request_id='plan')
    with pytest.raises(ValueError,match='explicitly'):
        records.plan_task('owner','field','cycle',task(),request_id='omit',supersedes_id=first['id'])
    second = records.plan_task('owner','field','cycle',task(trigger=None),request_id='unlink',supersedes_id=first['id'])
    assert second['data']['trigger'] is None and 'trigger_snapshot' not in second['data']
    assert calendar(records)['tasks'][0]['trigger_review']['state'] == 'not_linked'
    old = next(r for r in records.get('owner','field','cycle')['records'] if r['id'] == first['id'])
    assert old['data']['trigger_snapshot'] == source


@pytest.mark.parametrize('language',['en','fr','zh-Hant'])
def test_guided_source_snapshot_preview_and_later_change_require_backtracking(setup, language):
    workflow, farms = setup
    records = CropRecords(workflow.db)
    source = records.record_scouting('owner','field','cycle',scouting(),request_id='source')
    wizard = TaskConsultation(workflow.db)
    supplied = dict(trigger='crop_record:'+source['id'],trigger_reason='Inspect because of the reported symptoms')
    view = finish(wizard,supplied,language=language)
    assert scouting()['symptoms'] in render_task_intake(view)
    assert view['answers']['trigger_snapshot'] == source
    before = farms.export('owner')
    corrected = records.record_scouting('owner','field','cycle',scouting(affected_count=1),request_id='correction',supersedes_id=source['id'])
    with pytest.raises(ValueError,match='trigger changed'):
        wizard.save('owner','field',view['draft_id'],view['revision'])
    assert wizard.get('owner','field') == view
    assert len(farms.export('owner')['crop_records']) == len(before['crop_records'])+1
    while view['next_field'] != 'trigger':
        view = wizard.answer('owner','field','',view['revision'],back=True)
    assert 'trigger_snapshot' not in view['answers']
    supplied['trigger'] = 'crop_record:'+corrected['id']
    view = finish(wizard,supplied,language=language)
    saved = wizard.save('owner','field',view['draft_id'],view['revision'])
    assert saved['record']['data']['trigger_snapshot'] == corrected
    assert saved['record']['data']['trigger']['reason'] == supplied['trigger_reason']
    assert wizard.save('owner','field',view['draft_id'],view['revision']) == saved


def test_old_task_draft_keeps_original_questions_and_unlinked_contract(setup):
    workflow, _ = setup
    wizard = TaskConsultation(workflow.db)
    wizard.start('owner','field')
    draft = json.loads(workflow.db.query('SELECT payload FROM task_intake_drafts')[0][0])
    draft.pop('intake_version')
    workflow.db.execute('UPDATE task_intake_drafts SET payload=?',(json.dumps(draft),))
    view = finish(wizard)
    assert 'trigger' not in view['answers'] and 'trigger' not in view['fields']
    saved = wizard.save('owner','field',view['draft_id'],view['revision'])
    assert 'trigger' not in saved['record']['data'] and 'trigger_snapshot' not in saved['record']['data']


def test_guided_measurement_review_change_requires_reviewing_source_again(setup):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    source = records.record_measurement('owner','field',reading(),cycle_id='cycle',request_id='reading')
    wizard = TaskConsultation(workflow.db)
    supplied = dict(trigger='measurement:'+source['id'],trigger_reason='Inspect the sensor')
    view = finish(wizard,supplied)
    checked = ObservationReviews(workflow.db).review('owner','field',source['id'],'checked',
        'Checked notebook','Operator',request_id='checked',expected_revision=None)
    with pytest.raises(ValueError,match='trigger changed'):
        wizard.save('owner','field',view['draft_id'],view['revision'])
    assert calendar(records)['tasks'] == []
    while view['next_field'] != 'trigger':
        view = wizard.answer('owner','field','',view['revision'],back=True)
    view = finish(wizard,supplied)
    saved = wizard.save('owner','field',view['draft_id'],view['revision'])
    assert saved['record']['data']['trigger']['review_revision_id'] == checked['id']
    assert saved['record']['data']['trigger_snapshot']['effective_quality'] == 'checked'


def test_forecast_can_prompt_task_without_becoming_actual_completion_evidence(setup):
    workflow, farms = setup
    records = CropRecords(workflow.db)
    source = records.record_measurement('owner','field',dict(reading(),provenance='forecast'),
        cycle_id='cycle',request_id='forecast')
    saved = records.plan_task('owner','field','cycle',task(trigger=link(source,'measurement',review_revision_id=None)),request_id='plan')
    assert saved['data']['trigger_snapshot']['provenance'] == 'forecast'
    assert calendar(records)['trigger_candidates'][0]['provenance'] == 'forecast'
    with pytest.raises(ValueError):
        records.resolve_task('owner','field',saved['id'],'completed','Forecast only','Operator',
            request_id='invalid',completed_on='2026-07-07',completion_record=dict(kind='measurement',id=source['id']))
    assert calendar(records)['tasks'][0]['status'] == 'planned'
    assert not farms.export('owner')['water_days']


@pytest.mark.parametrize('language',['en','fr','zh-Hant'])
def test_overview_and_season_keep_trigger_reason_and_bounded_messages(setup, language):
    workflow, _ = setup
    records = CropRecords(workflow.db)
    source = records.record_scouting('owner','field','cycle',scouting(),request_id='source')
    records.plan_task('owner','field','cycle',task(trigger=link(source,reason='WHY🌱'*200)),request_id='plan')
    report = FarmOverview(workflow.db).get('owner','farm',now=datetime(2026,7,7,tzinfo=timezone.utc))
    before = deepcopy(report)
    reply = render_overview(report,language)
    assert source['id'] in reply and 'WHY' in reply
    assert len(reply.encode('utf-16-le'))//2 < 3500
    assert report == before
    season = records.season_review('owner','field','cycle')
    assert season['journal']['calendar']['tasks'][0]['trigger']['id'] == source['id']
