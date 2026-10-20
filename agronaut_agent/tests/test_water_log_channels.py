"""Guided water totals use the same reviewed records across chat and offline CLI."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agronaut_agent.channels.commands import dispatch
from agronaut_agent.core import AgronautAgent
from agronaut_agent.field_workflow import FieldWorkflow
from agronaut_agent.tests.test_unit_agent import agent_and_units
from agronaut_agent.tests.test_unit_setup import NoModel
from agronaut_agent.tests.test_water_log_consultation import ANSWERS

LANGUAGES = [('en', 'none', 'estimated', 'save', 'Saved daily water record'),
             ('fr', 'aucun', 'estimé', 'enregistrer', 'Relevé journalier enregistré'),
             ('zh-Hant', '無', '估算', '儲存', '已儲存每日水量紀錄')]


def create_unit(brain, channel, identity):
    return brain.create_unit(channel, identity, dict(farm=dict(name='Synthetic farm', timezone='Africa/Ouagadougou'),
        unit=dict(name='Field', production_system='soil', environment='open_field', medium='soil',
                  water_strategy='supplemental', irrigation_method='drip')))


@pytest.mark.parametrize('language,none,provenance,save,saved_text', LANGUAGES)
@pytest.mark.parametrize('channel', ['web', 'whatsapp'])
def test_no_model_routes_review_before_recording(tmp_path, monkeypatch, channel, language, none, provenance, save, saved_text):
    from agronaut_agent.channels.whatsapp_adapter import WhatsAppAdapter
    from app import _route_turn

    brain = AgronautAgent(db_path=tmp_path/'water.sqlite3', llm_provider='no-such-provider', require_tools=False)
    unit = create_unit(brain, channel, '17')
    owner = brain._conv.get_or_create_user(channel, '17')
    adapter = WhatsAppAdapter(brain, token='test', phone_number_id='test', allowed_ids={'17'})
    sent = []
    monkeypatch.setattr(adapter, 'send_text', lambda number, text: sent.append(text))
    message_id = 0
    def route(value):
        nonlocal message_id
        if channel == 'web':
            return _route_turn(brain, '17', value, None)
        message_id += 1
        adapter.handle_payload({'entry': [{'changes': [{'value': {'messages': [{'from': '17',
            'id': f'message-{message_id}', 'type': 'text', 'text': {'body': value}}]}}]}]})
        return sent[-1]
    route('/waterlog '+language)
    wizard = brain._water_log
    view = wizard.get(owner, unit['id'])
    values = dict(ANSWERS, cycle_id=none, provenance=provenance, source='Carnet 原始紀錄 — synthetic net-depth estimate')
    while view['status'] != 'ready':
        reply = route(values[view['next_field']])
        view = wizard.get(owner, unit['id'])
        assert view['question'] in reply
    workflow = FieldWorkflow(brain._farms.db)
    assert workflow.list(owner, unit['id'])['water_days'] == []
    route('enregistrer' if language != 'fr' else '儲存')
    assert wizard.get(owner, unit['id']) == view
    assert saved_text in route(save)
    saved = workflow.list(owner, unit['id'])['water_days']
    assert len(saved) == 1 and saved[0]['data']['quality'] == 'unverified'
    assert saved[0]['data']['source'] == values['source'] and saved[0]['data']['provenance'] == 'estimated'
    assert not wizard.active(owner, unit['id'])


def test_scope_media_back_cancel_reset_and_other_intakes(tmp_path):
    brain, owner = agent_and_units(tmp_path, NoModel())
    brain.select_unit('cli', 'one', 'field')
    def route(value):
        return dispatch(brain, 'cli', 'one', value).text
    route('/waterlog fr')
    wizard = brain._water_log
    view = wizard.get(owner, 'field')
    assert 'propre texte' in brain.handle_message('cli', 'one', 'none', fact_text='generated image claim')
    for command in ('/waterlog xx', '/waterlog fr extra'):
        assert '/waterlog zh-Hant' in route(command)
    assert wizard.get(owner, 'field') == view
    brain.handle_message('cli', 'one', 'aucun')
    assert 'Choisissez' in route('/back')
    for command in ('/plant', '/scout', '/fieldplan'):
        assert 'water logging' in route(command)
    route('/newunit soil')
    brain.handle_message('cli', 'one', 'Another farm')
    assert 'unit setup' in route('/waterlog')
    route('/cancel')
    assert wizard.active(owner, 'field')
    brain.select_unit('cli', 'one', 'hydro')
    assert 'soil production unit' in route('/waterlog')
    brain.select_unit('cli', 'one', 'field')
    assert 'Choisissez' in route('/waterlog')
    brain.reset('cli', 'one')
    assert not wizard.active(owner, 'field')
    route('/waterlog')
    assert 'cancelled' in route('/cancel')


@pytest.mark.parametrize('language,none,provenance,save,saved_text', LANGUAGES)
def test_telegram_mocked_transport(tmp_path, monkeypatch, language, none, provenance, save, saved_text):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agronaut_agent.channels.telegram_adapter import TelegramAdapter

    async def inline_call(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', inline_call)
    brain = AgronautAgent(db_path=tmp_path/'telegram.sqlite3', chat_model=NoModel())
    create_unit(brain, 'telegram', '17')
    adapter = TelegramAdapter(brain, token='test', allowed_ids={17})
    reply = AsyncMock()
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=17, type='private'),
        effective_user=SimpleNamespace(id=17, full_name='Grower'),
        message=SimpleNamespace(text='/waterlog '+language, reply_text=reply))
    ctx = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))
    async def exercise():
        await adapter._on_command(update, ctx)
        for value in (none, ANSWERS['day'], '5', '3', '1', ANSWERS['source'], provenance, save):
            update.message.text = value
            await adapter._on_text(update, ctx)
    asyncio.run(exercise())
    assert any(saved_text in call.args[0] for call in reply.await_args_list)


@pytest.mark.parametrize('language,none,provenance,save,saved_text', LANGUAGES)
def test_stdlib_cli_resumes_without_model_or_network(tmp_path, language, none, provenance, save, saved_text):
    root = Path(__file__).resolve().parents[2]
    db, path = str(tmp_path/'cli.sqlite3'), tmp_path/'request.json'
    def command(*args):
        process = subprocess.run([sys.executable, '-S', '-m', 'agronaut_agent.cli', *args], cwd=root,
                                 text=True, capture_output=True, timeout=30)
        assert process.returncode == 0, process.stdout+process.stderr
        return json.loads(process.stdout)
    command('farm', '--db', db, 'create', '--input', str(root/'examples/soil_unit.json'))
    def operation(name, data):
        path.write_text(json.dumps(data))
        return command('field', '--db', db, name, '--input', str(path))
    view = operation('start_water_log', {'language': language})
    while view['status'] != 'ready':
        view = operation('answer_water_log', {'answer': dict(ANSWERS, cycle_id=none, provenance=provenance)[view['next_field']],
                                             'expected_revision': view['revision']})
    saved = operation('save_water_log', {'draft_id': view['draft_id'], 'expected_revision': view['revision']})
    assert saved['language'] == language and saved['record']['data']['quality'] == 'unverified'
    assert operation('get_water_log', {}) is None
