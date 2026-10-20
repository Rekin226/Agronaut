"""Task planning and completion through all transports and the stdlib CLI."""
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agronaut_agent.channels.commands import dispatch
from agronaut_agent.tests.test_harvest_channels import setup
from agronaut_agent.tests.test_task_consultation import ANSWERS

LANGUAGES = [('en', 'save', 'UTC'), ('fr', 'enregistrer', 'Africa/Ouagadougou'), ('zh-Hant', '儲存', 'Asia/Taipei')]


@pytest.mark.parametrize('channel', ['web', 'whatsapp', 'telegram', 'cli'])
@pytest.mark.parametrize('language,save,zone', LANGUAGES)
def test_plan_then_complete_through_transports_without_model(tmp_path, monkeypatch, channel, language, save, zone):
    from agronaut_agent.tests.test_task_triggers import scouting
    brain, unit, cycle = setup(tmp_path, channel, zone)
    source = brain.crop_records(channel, '17', 'record_scouting', dict(cycle_id=cycle, data=scouting(day='2026-07-05'), request_id='source'))
    answers = dict(ANSWERS, trigger='1', trigger_reason='Inspect affected plants')
    owner = brain._conv.get_or_create_user(channel, '17')
    if channel == 'whatsapp':
        from agronaut_agent.channels.whatsapp_adapter import WhatsAppAdapter
        adapter = WhatsAppAdapter(brain, token='test', phone_number_id='test', allowed_ids={'17'})
        sent = []
        monkeypatch.setattr(adapter, 'send_text', lambda number, text: sent.append(text))
    elif channel == 'telegram':
        from agronaut_agent.channels.telegram_adapter import TelegramAdapter
        async def inline(function, *args, **kwargs):
            return function(*args, **kwargs)
        monkeypatch.setattr(asyncio, 'to_thread', inline)
        adapter = TelegramAdapter(brain, token='test', allowed_ids={17})
        sent = AsyncMock()
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=17, type='private'),
            effective_user=SimpleNamespace(id=17, full_name='Grower'), message=SimpleNamespace(text='', reply_text=sent))
        context = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))
        assert 'task' in {row[0] for row in adapter._command_specs()}
    count = 0
    def route(value):
        nonlocal count
        count += 1
        if channel == 'web':
            from app import _route_turn
            return _route_turn(brain, '17', value, None)
        if channel == 'cli':
            reply = dispatch(brain, channel, '17', value)
            return reply.text if reply else brain.handle_message(channel, '17', value)
        if channel == 'telegram':
            update.message.text = value
            asyncio.run(adapter._on_command(update, context) if value.startswith('/') else adapter._on_text(update, context))
            return sent.await_args.args[0]
        adapter.handle_payload({'entry':[{'changes':[{'value':{'messages':[{'from':'17','id':f'task-{count}',
            'type':'text','text':{'body':value}}]}}]}]})
        return sent[-1]
    wizard = brain._task_intake
    route('/task '+language)
    view = wizard.get(owner, unit['id'])
    while view['status'] != 'ready':
        if view['next_field'] == 'trigger':
            assert view['source_options']['items'][0]['id'] == source['id']
            assert '1.' in view['question'] and 'refresh' in view['question']
            refreshed = route('refresh')
            view = wizard.get(owner, unit['id'])
            assert view['question'] in refreshed
        reply = route(answers[view['next_field']])
        view = wizard.get(owner, unit['id'])
        assert view['question'] in reply
    assert brain.crop_records(channel, '17', 'get', {'cycle_id':cycle})['calendar']['tasks'] == []
    route(save)
    task = brain.crop_records(channel, '17', 'get', {'cycle_id':cycle})['calendar']['tasks'][0]
    assert task['trigger']['id'] == source['id']
    actual = brain.crop_records(channel, '17', 'record_scouting', dict(cycle_id=cycle, data=scouting(entry='completion'), request_id='actual'))
    route('/task '+language)
    resolution = dict(action='completed', record_id='1', completed_on='2026-07-06', evidence='1',
                      reason='Operator confirms inspection', source='Synthetic notebook')
    view = wizard.get(owner, unit['id'])
    while view['status'] != 'ready':
        if view['next_field'] == 'record_id':
            assert view['task_options']['items'][0]['id'] == task['record_id']
            refreshed = route('refresh')
            view = wizard.get(owner, unit['id'])
            assert view['question'] in refreshed
        if view['next_field'] == 'evidence':
            assert view['evidence_options']['total'] == 1 and view['evidence_options']['items'][0]['id'] == actual['id']
            refreshed = route('refresh')
            view = wizard.get(owner, unit['id'])
            assert view['question'] in refreshed
        route(resolution[view['next_field']])
        view = wizard.get(owner, unit['id'])
    route(save)
    task = brain.crop_records(channel, '17', 'get', {'cycle_id':cycle})['calendar']['tasks'][0]
    assert task['status'] == 'completed' and task['completion_evidence_state'] == 'current'
    assert task['completion_record']['id'] == actual['id']
    assert not brain._farms.export(owner)['water_days']
    assert {r['kind'] for r in brain._farms.export(owner)['crop_records']} == {'planting', 'calendar', 'scouting'}


def test_media_back_reset_and_language_preserve_draft(tmp_path):
    brain, unit, _ = setup(tmp_path,'cli','UTC')
    owner = brain._conv.get_or_create_user('cli','17')
    def route(command):
        return dispatch(brain,'cli','17',command).text
    route('/task fr')
    wizard = brain._task_intake
    before = wizard.get(owner,unit['id'])
    brain.handle_message('cli','17','plan',fact_text='generated facts')
    assert wizard.get(owner,unit['id']) == before
    assert '/task zh-Hant' in route('/task unsupported')
    brain.handle_message('cli','17','plan')
    route('/back')
    assert wizard.get(owner,unit['id'])['next_field'] == 'action'
    for command in ('/plant','/scout','/fieldplan','/measure','/waterlog','/harvest'):
        assert 'task entry' in route(command)
    route('/task zh-Hant')
    assert wizard.get(owner,unit['id'])['language'] == 'zh-Hant'
    brain.reset('cli','17')
    assert wizard.get(owner,unit['id']) is None
    route('/task')
    route('/cancel')
    assert wizard.get(owner,unit['id']) is None


@pytest.mark.parametrize('language,save,zone', LANGUAGES)
def test_stdlib_cli_persists_preview_and_task_save(tmp_path, language, save, zone):
    from agronaut_agent.tests.test_task_triggers import scouting
    root = Path(__file__).resolve().parents[2]
    db, path = str(tmp_path/'task.sqlite3'), tmp_path/'input.json'
    def command(*args):
        result = subprocess.run([sys.executable,'-S','-m','agronaut_agent.cli',*args],cwd=root,text=True,capture_output=True,timeout=30)
        assert result.returncode == 0, result.stdout+result.stderr
        return json.loads(result.stdout)
    spec = json.loads((root/'examples/soil_unit.json').read_text())
    spec['farm']['timezone'] = zone
    path.write_text(json.dumps(spec))
    command('farm','--db',db,'create','--input',str(path))
    def operation(name, data):
        path.write_text(json.dumps(data))
        return command('crop','--db',db,name,'--input',str(path))
    cycle = operation('create',dict(crop='tomato',planted_on='2026-07-01',source='Synthetic',request_id='plant'))['cycle']['id']
    source = operation('record_scouting', dict(cycle_id=cycle, data=scouting(day='2026-07-05'), request_id='source'))
    answers = dict(ANSWERS, trigger='1', trigger_reason='Inspect the affected row')
    view = operation('start_task',dict(language=language))
    while view['status'] != 'ready':
        if view['next_field'] == 'trigger':
            view = operation('answer_task', dict(answer='refresh', expected_revision=view['revision']))
            assert operation('get_task', {}) == view
            assert view['source_options']['items'][0]['id'] == source['id']
        view = operation('answer_task',dict(answer=answers[view['next_field']],expected_revision=view['revision']))
    result = operation('save_task',dict(draft_id=view['draft_id'],expected_revision=view['revision']))
    assert result['language'] == language
    assert result['record']['data']['trigger']['id'] == source['id']
    assert operation('get_task',{}) is None
    assert operation('get',dict(cycle_id=cycle))['calendar']['tasks'][0]['record_id'] == result['record']['id']
    actual = operation('record_scouting', dict(cycle_id=cycle, data=scouting(entry='completion'), request_id='actual'))
    view = operation('start_task', dict(language=language))
    answers = dict(action='completed', record_id='1', completed_on='2026-07-06', evidence='1', reason='Work completed', source='Operator')
    while view['status'] != 'ready':
        if view['next_field'] == 'record_id':
            view = operation('answer_task', dict(answer='refresh', expected_revision=view['revision']))
            assert operation('get_task', {}) == view
            assert view['task_options']['items'][0]['id'] == result['record']['id']
        if view['next_field'] == 'evidence':
            view = operation('answer_task', dict(answer='refresh', expected_revision=view['revision']))
            assert operation('get_task', {}) == view
            assert view['evidence_options']['items'][0]['id'] == actual['id']
        view = operation('answer_task', dict(answer=answers[view['next_field']], expected_revision=view['revision']))
    completed = operation('save_task', dict(draft_id=view['draft_id'], expected_revision=view['revision']))
    assert completed['record']['supersedes_id'] == result['record']['id']
    assert completed['record']['data']['status'] == 'completed'
    assert completed['record']['data']['resolution']['completion_record']['snapshot'] == actual
