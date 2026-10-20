"""A grower can establish a cycle and scout it through deterministic chat."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agronaut_agent.channels.commands import dispatch
from agronaut_agent.core import AgronautAgent
from agronaut_agent.tests.test_planting_consultation import ANSWERS
from agronaut_agent.tests.test_scouting_consultation import ANSWERS as SCOUTING
from agronaut_agent.tests.test_unit_agent import agent_and_units
from agronaut_agent.tests.test_unit_setup import NoModel

LANGUAGES = [('en', 'save', 'Saved new crop cycle'),
             ('fr', 'enregistrer', 'Nouveau cycle de culture enregistré'),
             ('zh-Hant', '儲存', '已儲存新作期')]


@pytest.mark.parametrize('language,save,saved_text', LANGUAGES)
@pytest.mark.parametrize('channel', ['cli', 'web', 'whatsapp'])
def test_new_unit_planting_and_scouting_without_model(tmp_path, channel, language, save, saved_text):
    from app import _route_turn

    brain = AgronautAgent(db_path=tmp_path/'grower.sqlite3', llm_provider='no-such-provider', require_tools=False)
    def route(value):
        if channel == 'web':
            return _route_turn(brain, 'grower', value, None)
        return dispatch(brain, channel, 'grower', value).text if value.startswith('/') else brain.handle_message(channel, 'grower', value)

    route('/newunit soil')
    for value in ('Synthetic farm', 'Africa/Ouagadougou', 'Test field', 'open_field', 'rainfed'):
        reply = route(value)
    assert 'Created and selected' in reply
    unit = brain.list_units(channel, 'grower')[0]['id']
    owner = brain._conv.get_or_create_user(channel, 'grower')
    route('/plant '+language)
    wizard = brain._planting_intake
    view = wizard.get(owner, unit)
    while view['status'] != 'ready':
        reply = route(ANSWERS[view['next_field']])
        view = wizard.get(owner, unit)
        assert view['question'] in reply
    assert save in reply and ANSWERS['source'] in reply
    assert brain._farms.list_cycles(owner, unit) == []
    # A confirmation in another language must not create the cycle.
    route('enregistrer' if language != 'fr' else '儲存')
    assert wizard.get(owner, unit) == view
    assert saved_text in route(save)
    cycles = brain._farms.list_cycles(owner, unit)
    assert len(cycles) == 1 and not wizard.active(owner, unit)
    route('/scout en')
    scout = brain._scouting_intake.get(owner, unit)
    while scout['status'] != 'ready':
        route(SCOUTING[scout['next_field']])
        scout = brain._scouting_intake.get(owner, unit)
    assert 'Saved scouting observation' in route('save')
    records = brain.crop_records(channel, 'grower', 'get', {'cycle_id': cycles[0]['id']})['records']
    assert len(records) == 2
    assert {record['kind'] for record in records} == {'planting', 'scouting'}


def test_media_language_back_reset_and_setup_priority(tmp_path):
    brain, owner = agent_and_units(tmp_path, NoModel())
    brain.select_unit('cli', 'one', 'field')
    def route(value):
        return dispatch(brain, 'cli', 'one', value).text
    route('/plant fr')
    wizard = brain._planting_intake
    before = wizard.get(owner, 'field')
    assert 'Répondez par du texte' in brain.handle_message('cli', 'one', 'tomato', fact_text='generated image claim')
    for command in ('/plant xx', '/plant fr extra'):
        assert '/plant zh-Hant' in route(command)
    assert wizard.get(owner, 'field') == before
    brain.handle_message('cli', 'one', 'Tomate / 番茄')
    assert 'Quelle culture' in route('/back')
    for command in ('/fieldplan', '/scout'):
        assert 'planting' in route(command)
    route('/newunit soil')
    brain.handle_message('cli', 'one', 'Second farm')
    assert 'unit setup' in route('/plant')
    assert wizard.get(owner, 'field')['answers'] == {}
    route('/cancel')  # Cancel global setup, keeping the unit's planting draft.
    assert wizard.active(owner, 'field')
    brain.select_unit('cli', 'one', 'hydro')
    route('/plant')
    assert wizard.get(owner, 'hydro')['language'] == 'en'
    assert 'cancelled' in route('/cancel')
    brain.select_unit('cli', 'one', 'field')
    assert 'Quelle culture' in route('/plant')
    brain.reset('cli', 'one')
    assert not wizard.active(owner, 'field')


@pytest.mark.parametrize('language,save,saved_text', LANGUAGES)
def test_telegram_planting_with_mocked_transport(tmp_path, monkeypatch, language, save, saved_text):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agronaut_agent.channels.telegram_adapter import TelegramAdapter

    async def inline_call(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', inline_call)
    brain = AgronautAgent(db_path=tmp_path/'telegram.sqlite3', chat_model=NoModel())
    adapter = TelegramAdapter(brain, token='test', allowed_ids={17})
    reply = AsyncMock()
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=17, type='private'),
        effective_user=SimpleNamespace(id=17, full_name='Grower'),
        message=SimpleNamespace(text='/newunit soil', reply_text=reply))
    ctx = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))
    async def exercise():
        await adapter._on_command(update, ctx)
        for value in ('Synthetic farm', 'Asia/Taipei', 'Field', 'open_field', 'rainfed'):
            update.message.text = value
            await adapter._on_text(update, ctx)
        update.message.text = '/plant '+language
        await adapter._on_command(update, ctx)
        for value in (*ANSWERS.values(), save):
            update.message.text = value
            await adapter._on_text(update, ctx)
    asyncio.run(exercise())
    assert any(saved_text in call.args[0] for call in reply.await_args_list)
    owner = brain._conv.get_or_create_user('telegram', '17')
    unit = brain.list_units('telegram', '17')[0]['id']
    assert len(brain._farms.list_cycles(owner, unit)) == 1


@pytest.mark.parametrize('language,save,saved_text', LANGUAGES)
def test_stdlib_cli_resumes_planting_between_processes(tmp_path, language, save, saved_text):
    root = Path(__file__).resolve().parents[2]
    path, db = tmp_path/'input.json', str(tmp_path/'cli.sqlite3')
    def command(*args):
        result = subprocess.run([sys.executable, '-S', '-m', 'agronaut_agent.cli', *args], cwd=root,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout+result.stderr
        return json.loads(result.stdout)
    path.write_text((root/'examples/soil_unit.json').read_text())
    command('farm', '--db', db, 'create', '--input', str(path))
    def operation(name, data):
        path.write_text(json.dumps(data))
        return command('crop', '--db', db, name, '--input', str(path))
    view = operation('start_planting', {'language': language})
    while view['status'] != 'ready':
        view = operation('answer_planting', {'answer': ANSWERS[view['next_field']], 'expected_revision': view['revision']})
    saved = operation('save_planting', {'draft_id': view['draft_id'], 'expected_revision': view['revision']})
    assert saved['language'] == language and saved['record']['data'] == ANSWERS
    assert operation('get_planting', {}) is None
    assert operation('start_scouting', {})['next_field'] == 'cycle_id'
