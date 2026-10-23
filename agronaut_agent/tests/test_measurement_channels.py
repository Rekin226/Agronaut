"""No-model raw readings retain explicit units, time, provenance and review."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agronaut_agent.channels.commands import dispatch
from agronaut_agent.core import AgronautAgent
from agronaut_agent.tests.test_measurement_consultation import ANSWERS
from agronaut_agent.tests.test_unit_agent import agent_and_units
from agronaut_agent.tests.test_unit_setup import NoModel

LANGUAGES = [('en', 'none', 'measured', 'save', 'Saved reading', 'UTC', '+00:00'),
             ('fr', 'aucun', 'mesuré', 'enregistrer', 'Mesure enregistrée', 'Africa/Ouagadougou', '+00:00'),
             ('zh-Hant', '無', '量測', '儲存', '已儲存讀值', 'Asia/Taipei', '+08:00')]


def create_unit(brain, channel, zone):
    return brain.create_unit(channel, '17', dict(farm=dict(name='Synthetic farm', timezone=zone),
        unit=dict(name='Field', production_system='soil', environment='open_field', medium='soil',
                  water_strategy='supplemental', irrigation_method='drip')))


@pytest.mark.parametrize('language,none,provenance,save,saved_text,zone,offset', LANGUAGES)
@pytest.mark.parametrize('channel', ['web', 'whatsapp'])
def test_raw_reading_after_unavailable_daily_total_without_model(tmp_path, monkeypatch, channel, language, none, provenance, save, saved_text, zone, offset):
    from agronaut_agent.channels.whatsapp_adapter import WhatsAppAdapter
    from app import _route_turn

    brain = AgronautAgent(db_path=tmp_path/'readings.sqlite3', llm_provider='no-such-provider', require_tools=False)
    unit = create_unit(brain, channel, zone)
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
            'id': f'measure-{message_id}', 'type': 'text', 'text': {'body': value}}]}}]}]})
        return sent[-1]
    route('/waterlog '+language)
    for value in (none, '2026-07-06', 'unknown'):
        route(value)
    assert brain._water_log.get(owner, unit['id'])['next_field'] == 'rainfall_mm'
    route('/cancel')
    route('/measure '+language)
    wizard = brain._measurement_intake
    view = wizard.get(owner, unit['id'])
    while view['status'] != 'ready':
        reply = route(dict(ANSWERS, cycle_id=none, provenance=provenance)[view['next_field']])
        view = wizard.get(owner, unit['id'])
        assert view['question'] in reply
    assert offset in reply and '25.5 L/h' in reply
    assert not brain._farms.observations(owner, unit['id'])
    route('enregistrer' if language != 'fr' else '儲存')
    assert wizard.get(owner, unit['id']) == view
    assert saved_text in route(save)
    reading = brain._farms.observations(owner, unit['id'])[0]
    assert reading['observed_at'] == '2026-07-06T15:30:00'+offset
    assert reading['value'] == 25.5 and reading['unit'] == 'L/h' and reading['quality'] == 'unverified'
    assert not brain._farms.export(owner)['water_days']


def test_media_scope_back_reset_and_competing_intakes(tmp_path):
    brain, owner = agent_and_units(tmp_path, NoModel())
    brain.select_unit('cli', 'one', 'field')
    def route(value):
        return dispatch(brain, 'cli', 'one', value).text
    route('/measure fr')
    wizard = brain._measurement_intake
    before = wizard.get(owner, 'field')
    assert 'propre texte' in brain.handle_message('cli', 'one', 'none', fact_text='generated meter reading')
    for command in ('/measure xx', '/measure fr extra'):
        assert '/measure zh-Hant' in route(command)
    assert wizard.get(owner, 'field') == before
    brain.handle_message('cli', 'one', 'aucun')
    assert 'Choisissez' in route('/back')
    for command in ('/plant', '/scout', '/fieldplan', '/waterlog'):
        assert 'measurement entry' in route(command)
    route('/newunit soil')
    brain.handle_message('cli', 'one', 'Another farm')
    assert 'unit setup' in route('/measure')
    route('/cancel')
    assert wizard.active(owner, 'field')
    brain.select_unit('cli', 'one', 'hydro')
    assert 'Choose' in route('/measure')
    assert 'cancelled' in route('/cancel')
    brain.select_unit('cli', 'one', 'field')
    assert 'Choisissez' in route('/measure')
    brain.reset('cli', 'one')
    assert not wizard.active(owner, 'field')


@pytest.mark.parametrize('language,none,provenance,save,saved_text,zone,offset', LANGUAGES)
def test_telegram_mocked_transport(tmp_path, monkeypatch, language, none, provenance, save, saved_text, zone, offset):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agronaut_agent.channels.telegram_adapter import TelegramAdapter

    async def inline_call(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', inline_call)
    brain = AgronautAgent(db_path=tmp_path/'telegram.sqlite3', chat_model=NoModel())
    create_unit(brain, 'telegram', zone)
    adapter = TelegramAdapter(brain, token='test', allowed_ids={17})
    reply = AsyncMock()
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=17, type='private'),
        effective_user=SimpleNamespace(id=17, full_name='Grower'),
        message=SimpleNamespace(text='/measure '+language, reply_text=reply))
    ctx = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))
    async def exercise():
        await adapter._on_command(update, ctx)
        for value in (none, ANSWERS['quantity'], ANSWERS['value'], ANSWERS['observed_at'], ANSWERS['source'], provenance, save):
            update.message.text = value
            await adapter._on_text(update, ctx)
    asyncio.run(exercise())
    assert any(saved_text in call.args[0] for call in reply.await_args_list)


@pytest.mark.parametrize('language,none,provenance,save,saved_text,zone,offset', LANGUAGES)
def test_stdlib_cli_resumes_draft_and_keeps_original_entered_time(tmp_path, language, none, provenance, save, saved_text, zone, offset):
    root = Path(__file__).resolve().parents[2]
    db, path = str(tmp_path/'cli.sqlite3'), tmp_path/'input.json'
    def command(*args):
        result = subprocess.run([sys.executable, '-S', '-m', 'agronaut_agent.cli', *args], cwd=root,
                                text=True, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stdout+result.stderr
        return json.loads(result.stdout)
    spec = json.loads((root/'examples/soil_unit.json').read_text())
    spec['farm']['timezone'] = zone
    path.write_text(json.dumps(spec))
    command('farm', '--db', db, 'create', '--input', str(path))
    def operation(name, data):
        path.write_text(json.dumps(data))
        return command('crop', '--db', db, name, '--input', str(path))
    view = operation('start_measurement', {'language': language})
    while view['status'] != 'ready':
        view = operation('answer_measurement', {'answer': dict(ANSWERS, cycle_id=none, provenance=provenance)[view['next_field']],
                                               'expected_revision': view['revision']})
    saved = operation('save_measurement', {'draft_id': view['draft_id'], 'expected_revision': view['revision']})
    assert saved['language'] == language and saved['record']['observed_at'].endswith(offset)
    assert operation('get_measurement', {}) is None
