"""Translated planning uses the shared deterministic route in every chat channel."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agronaut_agent.channels.commands import dispatch
from agronaut_agent.core import AgronautAgent
from agronaut_agent.tests.test_field_consultation import ANSWERS
from agronaut_agent.tests.test_unit_agent import agent_and_units
from agronaut_agent.tests.test_unit_setup import NoModel


@pytest.mark.parametrize("language,save", [("fr", "enregistrer"), ("zh-Hant", "儲存")])
def test_core_completes_localized_flow_and_rejects_image_answers(tmp_path, language, save):
    brain, owner = agent_and_units(tmp_path, NoModel())
    brain.select_unit("cli", "one", "field")
    reply = dispatch(brain, "cli", "one", f"/fieldplan {language}").text
    assert ("Traduction provisoire" if language == "fr" else "翻譯草稿") in reply
    before = brain._field_intake.get(owner, "field")
    brain.handle_message("cli", "one", "2", fact_text="image interpretation")
    assert brain._field_intake.get(owner, "field") == before
    view = before
    while view["status"] != "ready":
        reply = brain.handle_message("cli", "one", ANSWERS[view["next_field"]])
        view = brain._field_intake.get(owner, "field")
        assert view["question"] in reply
    assert brain.field_workflow("cli", "one", "list", {})["calculations"] == []
    reply = brain.handle_message("cli", "one", save)
    assert ("Calcul enregistré" if language == "fr" else "已儲存計算") in reply
    assert not brain._field_intake.active(owner, "field")
    assert brain.field_workflow("cli", "one", "list", {})["water_days"] == []


@pytest.mark.parametrize("language", ["fr", "zh-Hant"])
@pytest.mark.parametrize("channel", ["web", "whatsapp", "telegram", "cli"])
def test_channel_command_starts_the_requested_language(tmp_path, monkeypatch, capsys, language, channel):
    brain = AgronautAgent(db_path=tmp_path / "channels.sqlite3", chat_model=NoModel())
    unit = brain.create_unit(channel, "17", dict(farm=dict(name="Home", timezone="UTC"),
        unit=dict(name="Field", production_system="soil", environment="open_field", medium="soil", water_strategy="rainfed")))
    command = f"/fieldplan {language}"
    if channel == "web":
        from app import _route_turn
        reply = _route_turn(brain, "17", command, None)
    elif channel == "whatsapp":
        from agronaut_agent.channels.whatsapp_adapter import WhatsAppAdapter
        adapter = WhatsAppAdapter(brain, token="test", phone_number_id="test", allowed_ids={"17"})
        sent = []
        monkeypatch.setattr(adapter, "send_text", lambda number, text: sent.append(text))
        adapter.handle_payload({"entry": [{"changes": [{"value": {"messages": [{"from": "17", "id": "language-1",
            "type": "text", "text": {"body": command}}]}}]}]})
        reply = "\n".join(sent)
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
        reply = "\n".join(call.args[0] for call in sent.await_args_list)
    else:
        from agronaut_agent.channels.repl import ReplChannel
        answers = iter((command, "quit"))
        monkeypatch.setattr("builtins.input", lambda *args: next(answers))
        ReplChannel(brain, "17").run()
        reply = capsys.readouterr().out
    assert ("Choisissez 1" if language == "fr" else "選 1") in reply
    owner = brain._conv.get_or_create_user(channel, "17")
    view = brain._field_intake.get(owner, unit["id"])
    assert view["language"] == language
    other_owner = brain._conv.get_or_create_user(channel, "other")
    with pytest.raises(ValueError):
        brain._field_intake.get(other_owner, unit["id"])
    assert ("annulé" if language == "fr" else "已取消") in dispatch(brain, channel, "17", "/cancel").text


def test_missing_translation_can_be_resumed_in_english(tmp_path, monkeypatch):
    brain, owner = agent_and_units(tmp_path, NoModel())
    brain.select_unit("cli", "one", "field")
    dispatch(brain, "cli", "one", "/fieldplan fr")
    before = brain._farms.export(owner)
    monkeypatch.setenv("AGRONAUT_CORPUS_DIR", str(tmp_path / "missing"))
    assert "FIELD_TRANSLATION_UNAVAILABLE" in brain.handle_message("cli", "one", "1")
    assert brain._farms.export(owner) == before
    assert "Choose 1" in dispatch(brain, "cli", "one", "/fieldplan en").text
