"""On-demand farm pages reach the same read-only service through mocked transports."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agronaut_agent.channels.commands import dispatch
from agronaut_agent.core import AgronautAgent
from agronaut_agent.equipment_calendar import EquipmentCalendar
from agronaut_agent.tests.test_crop_calendar import task
from agronaut_agent.tests.test_equipment_calendar import asset


class NoModel:
    def bind_tools(self, tools):
        return self

    def invoke(self, *args, **kwargs):
        raise AssertionError("overview must not call the model")


def prepared(tmp_path, channel):
    brain = AgronautAgent(db_path=tmp_path / "overview.sqlite3", chat_model=NoModel())
    unit = brain.create_unit(channel, "17", {"farm": {"name": "Home", "timezone": "Africa/Ouagadougou"},
        "unit": {"name": "Tomato field", "production_system": "soil", "medium": "soil",
                 "environment": "open_field", "water_strategy": "rainfed"}})
    cycle = brain.crop_records(channel, "17", "create", dict(crop="tomato", planted_on="2026-07-01",
        source="Synthetic", request_id="plant"))["cycle"]["id"]
    brain.crop_records(channel, "17", "plan_task", dict(cycle_id=cycle, data=task(), request_id="plan"))
    brain.start_measurement_intake(channel, "17", "fr")
    owner = brain._conv.get_or_create_user(channel, '17')
    calendar = EquipmentCalendar(brain._farms.db)
    pump = calendar.save_equipment(owner, unit['farm_id'], asset(), request_id='pump')
    calendar.reserve_equipment(owner, unit['farm_id'], dict(equipment_ids=[pump['id']],
        start='2099-10-20T08:00:00Z', end='2099-10-20T09:00:00Z', title='Pump booking',
        source='Synthetic equipment diary'), request_id='booking')
    calendar.save_equipment(owner, unit['farm_id'], asset(status='unavailable'), request_id='repair',
        supersedes_id=pump['id'], reason='Needs repair')
    return brain, unit


@pytest.mark.parametrize("language,title", [("en", "Farm overview"), ("fr", "Vue des exploitations"), ("zh-Hant", "農場總覽")])
@pytest.mark.parametrize("channel", ["web", "whatsapp", "telegram", "cli"])
def test_overview_preserves_selection_and_pending_intake(tmp_path, monkeypatch, capsys, language, title, channel):
    brain, unit = prepared(tmp_path, channel)
    owner = brain._conv.get_or_create_user(channel, "17")
    before = brain._farms.export(owner)
    draft = brain._measurement_intake.get(owner, unit["id"])
    command = f"/overview {language}"
    if channel == "web":
        from app import _route_turn
        reply = _route_turn(brain, "17", command, None)
    elif channel == "whatsapp":
        from agronaut_agent.channels.whatsapp_adapter import WhatsAppAdapter
        adapter = WhatsAppAdapter(brain, token="test", phone_number_id="test", allowed_ids={"17"})
        sent = []
        monkeypatch.setattr(adapter, "send_text", lambda number, text: sent.append(text))
        adapter.handle_payload({"entry": [{"changes": [{"value": {"messages": [{"from": "17", "id": "overview-1",
            "type": "text", "text": {"body": command}}]}}]}]})
        reply = sent[-1]
    elif channel == "telegram":
        from agronaut_agent.channels.telegram_adapter import TelegramAdapter
        async def inline(func, *args, **kwargs):
            return func(*args, **kwargs)
        monkeypatch.setattr(asyncio, "to_thread", inline)
        adapter = TelegramAdapter(brain, token="test", allowed_ids={17})
        sent = AsyncMock()
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=17, type="private"),
            effective_user=SimpleNamespace(id=17, full_name="Grower"), message=SimpleNamespace(text=command, reply_text=sent))
        asyncio.run(adapter._on_command(update, SimpleNamespace()))
        reply = sent.await_args.args[0]
        assert "overview" in {name for name, _, _ in adapter._command_specs()}
    else:
        from agronaut_agent.channels.repl import ReplChannel
        answers = iter((command, "quit"))
        monkeypatch.setattr("builtins.input", lambda *args: next(answers))
        ReplChannel(brain, "17").run()
        reply = capsys.readouterr().out
    assert title in reply and "Synthetic harvest inspection" in reply
    assert 'Shared pump A' in reply and 'Pump booking' in reply
    assert brain._farms.export(owner) == before
    assert brain._measurement_intake.get(owner, unit["id"]) == draft
    assert next(u["id"] for u in brain.list_units(channel, "17") if u["active"]) == unit["id"]
    assert "Tomato field" not in dispatch(brain, channel, "someone_else", command).text


def test_invalid_command_does_not_read_or_mutate_a_farm():
    class Unused:
        def farm_overview_text(self, *args):
            raise AssertionError("invalid arguments must not reach the service")
    assert "Use /overview" in dispatch(Unused(), "cli", "17", "/overview@bot fr -1").text
