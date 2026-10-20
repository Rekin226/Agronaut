"""Harvest capture reaches the same records through messaging and offline CLI."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agronaut_agent.channels.commands import dispatch
from agronaut_agent.core import AgronautAgent
from agronaut_agent.tests.test_harvest_consultation import ANSWERS
from agronaut_agent.tests.test_measurement_channels import create_unit
from agronaut_agent.tests.test_unit_setup import NoModel

LANGUAGES = [('en', 'fresh', 'measured', 'save', 'Saved', 'UTC'),
             ('fr', 'frais', 'mesuré', 'enregistrer', 'enregistré', 'Africa/Ouagadougou'),
             ('zh-Hant', '鮮重', '量測', '儲存', '已儲存', 'Asia/Taipei')]


def setup(tmp_path, channel, zone):
    brain = AgronautAgent(db_path=tmp_path/'harvest.sqlite3', chat_model=NoModel())
    unit = create_unit(brain, channel, zone)
    planting = brain.crop_records(channel, '17', 'create', dict(crop='tomato', planted_on='2026-07-01',
        source='Synthetic notebook', request_id='plant'))
    return brain, unit, planting['cycle']['id']


@pytest.mark.parametrize('channel', ['web', 'whatsapp', 'cli'])
@pytest.mark.parametrize('language,basis,provenance,save,saved,zone', LANGUAGES)
def test_reviewed_harvest_is_saved_once_through_text_channels(tmp_path, monkeypatch, channel, language, basis, provenance, save, saved, zone):
    from agronaut_agent.channels.whatsapp_adapter import WhatsAppAdapter
    from app import _route_turn

    brain, unit, cycle = setup(tmp_path, channel, zone)
    owner = brain._conv.get_or_create_user(channel, '17')
    adapter = WhatsAppAdapter(brain, token='test', phone_number_id='test', allowed_ids={'17'})
    sent = []
    monkeypatch.setattr(adapter, 'send_text', lambda number, text: sent.append(text))
    message_id = 0
    def route(value):
        nonlocal message_id
        if channel == 'web':
            return _route_turn(brain, '17', value, None)
        if channel == 'cli':
            reply = dispatch(brain, channel, '17', value)
            return reply.text if reply else brain.handle_message(channel, '17', value)
        message_id += 1
        adapter.handle_payload({'entry': [{'changes': [{'value': {'messages': [{'from': '17',
            'id': f'harvest-{message_id}', 'type': 'text', 'text': {'body': value}}]}}]}]})
        return sent[-1]
    route('/harvest '+language)
    wizard = brain._harvest_intake
    view = wizard.get(owner, unit['id'])
    while view['status'] != 'ready':
        reply = route(dict(ANSWERS, basis=basis, provenance=provenance)[view['next_field']])
        view = wizard.get(owner, unit['id'])
        assert view['question'] in reply
    assert '12.5' in reply and 'kg' in reply and cycle in reply
    assert not brain.crop_records(channel, '17', 'get', dict(cycle_id=cycle))['harvest_totals']
    route('儲存' if language != 'zh-Hant' else 'enregistrer')
    assert wizard.get(owner, unit['id']) == view
    reply = route(save)
    assert saved.lower() in reply.lower() or (language == 'en' and 'saved' in reply)
    journal = brain.crop_records(channel, '17', 'get', dict(cycle_id=cycle))
    assert journal['harvest_totals'][0]['quantity'] == '12.5'
    assert journal['lifecycle']['status'] == 'open'
    assert not brain._farms.export(owner)['water_days']
    assert wizard.save(owner, unit['id'], view['draft_id'], view['revision'])['record']['data']['provenance'] == 'measured'


@pytest.mark.parametrize('language,basis,provenance,save,saved,zone', LANGUAGES)
def test_telegram_transport_and_native_command(tmp_path, monkeypatch, language, basis, provenance, save, saved, zone):
    from agronaut_agent.channels.telegram_adapter import TelegramAdapter

    async def inline_call(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', inline_call)
    brain, _, cycle = setup(tmp_path, 'telegram', zone)
    adapter = TelegramAdapter(brain, token='test', allowed_ids={17})
    reply = AsyncMock()
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=17, type='private'),
        effective_user=SimpleNamespace(id=17, full_name='Grower'),
        message=SimpleNamespace(text='/harvest '+language, reply_text=reply))
    ctx = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))
    async def exercise():
        await adapter._on_command(update, ctx)
        for value in [*dict(ANSWERS, basis=basis, provenance=provenance).values(), save]:
            update.message.text = value
            await adapter._on_text(update, ctx)
    asyncio.run(exercise())
    assert any(saved.lower() in call.args[0].lower() for call in reply.await_args_list)
    assert brain.crop_records('telegram', '17', 'get', dict(cycle_id=cycle))['harvest_totals'][0]['batches'] == 1


def test_media_back_reset_and_exclusive_intakes(tmp_path):
    brain, unit, _ = setup(tmp_path, 'cli', 'UTC')
    owner = brain._conv.get_or_create_user('cli', '17')
    def route(value):
        return dispatch(brain, 'cli', '17', value).text
    route('/harvest fr')
    wizard = brain._harvest_intake
    view = wizard.get(owner, unit['id'])
    assert 'propre texte' in brain.handle_message('cli', '17', 'invented', fact_text='generated harvest quantity')
    for command in ('/harvest xx', '/harvest fr extra'):
        assert '/harvest zh-Hant' in route(command)
    assert wizard.get(owner, unit['id']) == view
    brain.handle_message('cli', '17', '1')
    route('/back')
    assert wizard.get(owner, unit['id'])['next_field'] == 'cycle_id'
    for command in ('/plant', '/scout', '/fieldplan', '/waterlog', '/measure'):
        assert 'harvest entry' in route(command)
    route('/newunit soil')
    assert 'configuration' in route('/harvest fr')
    route('/cancel')
    assert wizard.active(owner, unit['id'])
    brain.reset('cli', '17')
    assert not wizard.active(owner, unit['id'])
    for command in ('/plant', '/scout', '/fieldplan', '/waterlog', '/measure'):
        route(command)
        assert 'active intake' in route('/harvest')
        route('/cancel')


def test_unit_switch_preserves_separate_drafts_and_cancel_targets_selected_unit(tmp_path):
    brain, first, _ = setup(tmp_path, 'cli', 'UTC')
    owner = brain._conv.get_or_create_user('cli', '17')
    dispatch(brain, 'cli', '17', '/harvest fr')
    brain.handle_message('cli', '17', '1')
    wizard = brain._harvest_intake
    before = wizard.get(owner, first['id'])
    second = create_unit(brain, 'cli', 'Asia/Taipei')
    brain.crop_records('cli', '17', 'create', dict(crop='onion', planted_on='2026-07-01',
        source='Synthetic notebook', request_id='plant-second'))
    dispatch(brain, 'cli', '17', '/harvest zh-Hant')
    assert wizard.get(owner, second['id'])['language'] == 'zh-Hant'
    dispatch(brain, 'cli', '17', '/cancel')
    assert wizard.get(owner, second['id']) is None
    brain.select_unit('cli', '17', first['id'])
    assert wizard.get(owner, first['id']) == before
    dispatch(brain, 'cli', '17', '/harvest')
    assert wizard.get(owner, first['id']) == before


@pytest.mark.parametrize('language,basis,provenance,save,saved,zone', LANGUAGES)
def test_stdlib_cli_resumes_review_and_saves_actual_batch(tmp_path, language, basis, provenance, save, saved, zone):
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
    cycle = operation('create', dict(crop='tomato', planted_on='2026-07-01', source='Synthetic notebook', request_id='plant'))['cycle']['id']
    view = operation('start_harvest', {'language': language})
    while view['status'] != 'ready':
        view = operation('answer_harvest', dict(answer=dict(ANSWERS, basis=basis, provenance=provenance)[view['next_field']],
                                               expected_revision=view['revision']))
    recorded = operation('save_harvest', dict(draft_id=view['draft_id'], expected_revision=view['revision']))
    assert recorded['language'] == language and recorded['record']['data']['quantity'] == 12.5
    assert operation('get_harvest', {}) is None
    assert operation('get', dict(cycle_id=cycle))['harvest_totals'][0]['batches'] == 1
