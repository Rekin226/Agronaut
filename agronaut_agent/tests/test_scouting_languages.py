"""Localized conversations preserve the original observation and explicit review."""

import json

import pytest

from agronaut_agent.channels.commands import dispatch
from agronaut_agent.crop_records import execute_crop_records
from agronaut_agent.scouting_consultation import ScoutingConsultation, render_scouting_intake
from agronaut_agent.store import _Db
from agronaut_agent.tests.test_crop_records import create, setup  # noqa: F401
from agronaut_agent.tests.test_scouting_consultation import ANSWERS
from agronaut_agent.tests.test_scouting_consultation_channels import prepare


@pytest.mark.parametrize('language,unknown,provenance,save,heading,notice', [
    ('fr', 'inconnu', 'rapporté', 'enregistrer', 'Observation de culture', 'Traduction provisoire'),
    ('zh-Hant', '未知', '他人回報', '儲存', '作物巡查紀錄', '翻譯草稿'),
])
def test_localized_chat_reviews_and_saves_verbatim_observation(tmp_path, language, unknown, provenance, save, heading, notice):
    brain,owner,cycles=prepare(tmp_path)
    reply=dispatch(brain,'cli','one','/scout '+language).text
    assert heading in reply and notice in reply
    wizard=brain._scouting_intake
    view=wizard.get(owner,'field')
    supplied=dict(ANSWERS, counts=unknown, provenance=provenance,
                  symptoms='Feuilles jaunes / 葉片發黃 — cause inconnue', source='Carnet 原始紀錄', location='Parcelle A / 甲區')
    while view['status']!='ready':
        if view['next_field'] in ('day','counts','provenance'):
            invalid=brain.handle_message('cli','one','incorrect')
            assert 'Détails techniques' not in invalid and '技術詳細資訊' not in invalid
            assert 'Use ' not in invalid and 'Choose ' not in invalid
            assert wizard.get(owner,'field')==view
        reply=brain.handle_message('cli','one',supplied[view['next_field']])
        view=wizard.get(owner,'field')
        assert view['question'] in reply
    assert save in reply and supplied['symptoms'] in reply and unknown in reply
    assert len(brain.crop_records('cli','one','get',dict(cycle_id=cycles['field']))['records'])==1
    # A confirmation token in another language is not approval for this draft.
    brain.handle_message('cli','one','儲存' if language=='fr' else 'enregistrer')
    assert wizard.get(owner,'field')==view
    reply=brain.handle_message('cli','one',save)
    assert ('enregistrée' if language=='fr' else '已儲存') in reply
    record=brain.crop_records('cli','one','get',dict(cycle_id=cycles['field']))['records'][-1]
    assert record['data']['provenance']=='reported'
    assert record['data']['inspected_count'] is None and record['data']['affected_count'] is None
    for key in ('location','symptoms','source'):
        assert record['data'][key]==supplied[key]
    assert record['operation']['intake']['language']==language
    assert not wizard.active(owner,'field')


def test_switch_language_preserves_answers_but_invalidates_review_and_survives_restart(setup):  # noqa: F811
    records,farms=setup
    cycle=create(records)['cycle']['id']
    wizard=ScoutingConsultation(records.db)
    view=execute_crop_records(records.db,'owner','field','start_scouting',{'language':'fr'})
    supplied=dict(ANSWERS, counts='10,0', provenance='observé')
    while view['status']!='ready':
        view=wizard.answer('owner','field',supplied[view['next_field']],view['revision'])
    switched=wizard.start('owner','field','zh-Hant')
    assert switched['answers']==view['answers'] and switched['draft_id']==view['draft_id']
    assert switched['revision']!=view['revision']
    assert '有症狀株數: 0' in render_scouting_intake(switched)
    with pytest.raises(ValueError,match='changed'):
        wizard.save('owner','field',view['draft_id'],view['revision'])
    assert len(records.get('owner','field',cycle)['records'])==1
    reopened=_Db(records.db.path)
    try:
        restarted=ScoutingConsultation(reopened)
        assert restarted.start('owner','field')==switched
        assert restarted.start('owner','field','zh-Hant')==switched
        assert farms.export('owner')['scouting_intakes'][0]['language']=='zh-Hant'
        saved=restarted.save('owner','field',switched['draft_id'],switched['revision'])
        restarted.start('owner','field','fr')
        assert restarted.save('owner','field',switched['draft_id'],switched['revision'])==saved
        assert restarted.get('owner','field')['language']=='fr'
    finally:
        reopened._conn.close()


def test_language_is_unit_scoped_and_invalid_command_cannot_mutate_it(tmp_path):
    brain,owner,_=prepare(tmp_path)
    dispatch(brain,'cli','one','/scout fr')
    before=brain._scouting_intake.get(owner,'field')
    for command in ['/scout xx','/scout fr extra','/scout ../../file']:
        assert '/scout zh-Hant' in dispatch(brain,'cli','one',command).text
        assert brain._scouting_intake.get(owner,'field')==before
    reply=brain.handle_message('cli','one','photo',fact_text='invented observation')
    assert 'Répondez par du texte' in reply and brain._scouting_intake.get(owner,'field')==before
    brain.handle_message('cli','one','1')
    assert 'Quel cycle' in dispatch(brain,'cli','one','/back').text
    brain.select_unit('cli','one','hydro')
    assert 'Which crop cycle' in dispatch(brain,'cli','one','/scout').text
    brain.select_unit('cli','one','field')
    assert 'Quel cycle' in dispatch(brain,'cli','one','/scout').text
    assert 'annulé' in dispatch(brain,'cli','one','/cancel').text


def test_legacy_draft_and_save_receipt_without_language_remain_english(setup):  # noqa: F811
    records,_=setup
    create(records)
    wizard=ScoutingConsultation(records.db)
    view=wizard.start('owner','field')
    raw=json.loads(records.db.query('SELECT payload FROM scouting_intake_drafts')[0][0])
    raw.pop('language')
    records.db.execute('UPDATE scouting_intake_drafts SET payload=?',(json.dumps(raw),))
    assert wizard.start('owner','field')==view
    while view['status']!='ready':
        view=wizard.answer('owner','field',ANSWERS[view['next_field']],view['revision'])
    saved=wizard.save('owner','field',view['draft_id'],view['revision'])
    saved['record']['operation']['intake'].pop('language')
    records.db.execute('UPDATE crop_records SET payload=? WHERE id=?', (json.dumps(saved['record']),saved['record']['id']))
    assert wizard.save('owner','field',view['draft_id'],view['revision'])==saved


@pytest.mark.parametrize('language', ['fr','zh-Hant'])
def test_language_change_does_not_bypass_ownership_or_invalid_counts(setup, language):  # noqa: F811
    records,_=setup
    create(records)
    wizard=ScoutingConsultation(records.db)
    view=wizard.start('owner','field',language)
    with pytest.raises(ValueError):
        wizard.start('other','field','en')
    assert wizard.get('owner','field')==view
    for key in ('cycle_id','day','location','symptoms'):
        view=wizard.answer('owner','field',ANSWERS[key],view['revision'])
    for answer in ('0,0','2,3','10','10,未知','inconnu,0','-1,0'):
        with pytest.raises(ValueError):
            wizard.answer('owner','field',answer,view['revision'])
        assert wizard.get('owner','field')==view


@pytest.mark.parametrize('language,closed_text,changed_text',[
    ('fr','après la fin du cycle','Les détails de plantation ont changé'),
    ('zh-Hant','晚於作物期別結束日','種植資料已變更'),
])
def test_localized_recovery_when_cycle_closes_or_planting_changes(tmp_path,language,closed_text,changed_text):
    brain,owner,cycles=prepare(tmp_path)
    dispatch(brain,'cli','one','/scout '+language)
    brain.handle_message('cli','one','1')
    brain.crop_records('cli','one','close_cycle',dict(cycle_id=cycles['field'],ended_on='2026-07-05',
        reason='Synthetic end',source='Synthetic notebook',request_id='close',expected_revision=None))
    before=brain._scouting_intake.get(owner,'field')
    reply=brain.handle_message('cli','one','2026-07-06')
    assert closed_text in reply and '2026-07-05' in reply
    assert brain._scouting_intake.get(owner,'field')==before
    planting=brain.crop_records('cli','one','get',dict(cycle_id=cycles['field']))['planting']
    brain.crop_records('cli','one','correct_planting',dict(cycle_id=cycles['field'],crop='onion',planted_on='2026-07-01',
        source='Synthetic correction',reason='Wrong crop',request_id='amend',expected_revision=planting['revision_id']))
    reply=dispatch(brain,'cli','one','/back').text
    assert changed_text in reply and '/cancel' in reply
    assert brain._scouting_intake.get(owner,'field')==before
