"""Scouting entry works through real channel routing without model interpretation."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agronaut_agent.channels.commands import dispatch
from agronaut_agent.scouting_consultation import ScoutingConsultation
from agronaut_agent.tests.test_scouting_consultation import ANSWERS
from agronaut_agent.tests.test_unit_agent import agent_and_units
from agronaut_agent.tests.test_unit_setup import NoModel


def prepare(tmp_path):
    brain,owner=agent_and_units(tmp_path,NoModel())
    cycles={}
    for unit in ('field','hydro'):
        brain.select_unit('cli','one',unit)
        cycles[unit]=brain.crop_records('cli','one','create',dict(crop='tomato',planted_on='2026-07-01',source='Synthetic planting',request_id='plant'))['cycle']['id']
    brain.select_unit('cli','one','field')
    return brain,owner,cycles


def test_chat_finishes_only_after_review_and_explicit_save(tmp_path):
    brain,owner,cycles=prepare(tmp_path)
    assert 'Which crop cycle' in dispatch(brain,'cli','one','/scout').text
    wizard=ScoutingConsultation(brain._farms.db)
    view=wizard.get(owner,'field')
    while view['status']!='ready':
        reply=brain.handle_message('cli','one',ANSWERS[view['next_field']])
        view=wizard.get(owner,'field')
        assert view['question'] in reply
    assert 'Reply save' in reply and 'Affected plants: 2' in reply
    assert len(brain.crop_records('cli','one','get',dict(cycle_id=cycles['field']))['records'])==1
    assert 'Saved scouting observation' in brain.handle_message('cli','one','save')
    assert not wizard.active(owner,'field')
    records=brain.crop_records('cli','one','get',dict(cycle_id=cycles['field']))['records']
    assert len(records)==2 and any(r['data'].get('symptoms')==ANSWERS['symptoms'] for r in records)


def test_media_switch_resume_back_cancel_and_reset_preserve_unit_scope(tmp_path):
    brain,owner,_=prepare(tmp_path)
    dispatch(brain,'cli','one','/scout')
    before=brain._scouting_intake.get(owner,'field')
    assert 'text reply' in brain.handle_message('cli','one','1',fact_text='model generated photo observation')
    assert brain._scouting_intake.get(owner,'field')==before
    brain.handle_message('cli','one','1')
    assert 'Which crop cycle' in dispatch(brain,'cli','one','/back').text
    brain.select_unit('cli','one','hydro')
    dispatch(brain,'cli','one','/scout')
    assert 'cancelled' in dispatch(brain,'cli','one','/cancel').text
    assert brain._scouting_intake.active(owner,'field')
    brain.select_unit('cli','one','field')
    assert 'Which crop cycle' in dispatch(brain,'cli','one','/scout').text
    brain.reset('cli','one')
    assert not brain._scouting_intake.active(owner,'field')


def test_changed_crop_gives_recovery_instructions_for_back_and_preserves_draft(tmp_path):
    brain,owner,cycles=prepare(tmp_path)
    dispatch(brain,'cli','one','/scout')
    brain.handle_message('cli','one','1')
    brain.crop_records('cli','one','correct_planting',dict(cycle_id=cycles['field'],crop='onion',planted_on='2026-07-01',
        source='Synthetic correction',reason='Wrong crop',request_id='amend',expected_revision=brain.crop_records('cli','one','get',dict(cycle_id=cycles['field']))['planting']['revision_id']))
    reply=dispatch(brain,'cli','one','/back').text
    assert 'cancel' in reply and 'changed' in reply
    assert brain._scouting_intake.active(owner,'field')
    assert 'cancelled' in dispatch(brain,'cli','one','/cancel').text


def test_field_and_scouting_commands_do_not_answer_each_others_drafts(tmp_path):
    brain,owner,_=prepare(tmp_path)
    dispatch(brain,'cli','one','/scout')
    assert 'scouting' in dispatch(brain,'cli','one','/fieldplan').text
    assert brain._field_intake.get(owner,'field') is None
    dispatch(brain,'cli','one','/cancel')
    dispatch(brain,'cli','one','/fieldplan')
    assert 'field planning' in dispatch(brain,'cli','one','/scout').text
    assert brain._scouting_intake.get(owner,'field') is None


@pytest.mark.parametrize('channel',['web','whatsapp'])
@pytest.mark.parametrize('language,start_text,unknown,observed,save,saved_text',[
    ('en','Which crop cycle','unknown','observed','save','Saved scouting observation'),
    ('fr','Quel cycle','inconnu','observé','enregistrer','Observation de culture enregistrée'),
    ('zh-Hant','哪一期作物','未知','親自觀察','儲存','已儲存作物巡查紀錄'),
])
def test_router_with_unconfigured_model_records_supplied_observation(tmp_path,channel,language,start_text,unknown,observed,save,saved_text):
    from agronaut_agent.core import AgronautAgent
    from app import _route_turn

    brain=AgronautAgent(db_path=tmp_path/'no-model.sqlite3',llm_provider='no-such-provider',require_tools=False)
    unit=brain.create_unit(channel,'operator',dict(farm=dict(name='Home',timezone='UTC'),
        unit=dict(name='Field',production_system='soil',environment='open_field',medium='soil',water_strategy='rainfed',irrigation_method=None)))
    brain.crop_records(channel,'operator','create',dict(crop='tomato',planted_on='2026-07-01',source='Synthetic planting',request_id='plant'))
    owner=brain._conv.get_or_create_user(channel,'operator')
    def route(value):
        if channel=='web':
            return _route_turn(brain,'operator',value,None)
        return dispatch(brain,channel,'operator',value).text if value.startswith('/') else brain.handle_message(channel,'operator',value)
    assert start_text in route('/scout '+language)
    view=brain._scouting_intake.get(owner,unit['id'])
    while view['status']!='ready':
        route(dict(ANSWERS,counts=unknown,provenance=observed)[view['next_field']])
        view=brain._scouting_intake.get(owner,unit['id'])
    assert saved_text in route(save)


@pytest.mark.parametrize('language,provenance,save,saved_text',[
    ('en','observed','save','Saved scouting observation'),
    ('fr','observé','enregistrer','Observation de culture enregistrée'),
    ('zh-Hant','親自觀察','儲存','已儲存作物巡查紀錄'),
])
def test_telegram_intake_uses_mocked_transport_and_no_model(tmp_path,monkeypatch,language,provenance,save,saved_text):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agronaut_agent.channels.telegram_adapter import TelegramAdapter
    from agronaut_agent.core import AgronautAgent

    async def inline_call(function,*args,**kwargs):
        return function(*args,**kwargs)
    monkeypatch.setattr(asyncio,'to_thread',inline_call)
    brain=AgronautAgent(db_path=tmp_path/'telegram.sqlite3',chat_model=NoModel())
    unit=brain.create_unit('telegram','17',dict(farm=dict(name='Home',timezone='UTC'),
        unit=dict(name='Field',production_system='soil',environment='open_field',medium='soil',water_strategy='rainfed',irrigation_method=None)))
    brain.crop_records('telegram','17','create',dict(crop='tomato',planted_on='2026-07-01',source='Synthetic planting',request_id='plant'))
    owner=brain._conv.get_or_create_user('telegram','17')
    adapter=TelegramAdapter(brain,token='test',allowed_ids={17})
    reply=AsyncMock()
    update=SimpleNamespace(effective_chat=SimpleNamespace(id=17,type='private'),effective_user=SimpleNamespace(id=17,full_name='Grower'),
        message=SimpleNamespace(text='/scout '+language,reply_text=reply))
    ctx=SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))
    async def exercise():
        await adapter._on_command(update,ctx)
        view=brain._scouting_intake.get(owner,unit['id'])
        while view['status']!='ready':
            update.message.text=dict(ANSWERS,provenance=provenance)[view['next_field']]
            await adapter._on_text(update,ctx)
            view=brain._scouting_intake.get(owner,unit['id'])
        update.message.text=save
        await adapter._on_text(update,ctx)
    asyncio.run(exercise())
    assert any(saved_text in call.args[0] for call in reply.await_args_list)


@pytest.mark.parametrize('language,provenance',[('en','observed'),('fr','observé'),('zh-Hant','親自觀察')])
def test_stdlib_cli_persists_questions_between_processes(tmp_path,language,provenance):
    root=Path(__file__).resolve().parents[2]
    path,db=tmp_path/'input.json',str(tmp_path/'cli.sqlite3')
    def command(*args):
        result=subprocess.run([sys.executable,'-S','-m','agronaut_agent.cli',*args],cwd=root,capture_output=True,text=True,timeout=30)
        assert result.returncode==0,result.stdout+result.stderr
        return json.loads(result.stdout)
    unit=json.loads((root/'examples/soil_unit.json').read_text())
    unit['farm']['timezone']='UTC'
    path.write_text(json.dumps(unit))
    command('farm','--db',db,'create','--input',str(path))
    def operation(name,data):
        path.write_text(json.dumps(data))
        return command('crop','--db',db,name,'--input',str(path))
    operation('create',dict(crop='tomato',planted_on='2026-07-01',source='Synthetic planting',request_id='plant'))
    view=operation('start_scouting',{'language':language})
    while view['status']!='ready':
        assert view['language']==language
        view=operation('answer_scouting',dict(answer=dict(ANSWERS,provenance=provenance)[view['next_field']],expected_revision=view['revision']))
    saved=operation('save_scouting',dict(draft_id=view['draft_id'],expected_revision=view['revision']))
    assert saved['record']['data']['affected_count']==2
    assert saved['language']==language and saved['record']['data']['provenance']=='observed'
    assert operation('get_scouting',{}) is None
