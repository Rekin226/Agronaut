"""Guided setup is durable, atomic and independent of a model or active unit."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agro_model.farm import Farm, ProductionUnit
from agronaut_agent.channels.commands import dispatch
from agronaut_agent.core import AgronautAgent
from agronaut_agent.farm_store import FarmStore
from agronaut_agent.store import SCHEMA_VERSION, ConversationStore, _Db
from agronaut_agent.unit_context import UnitContexts
from agronaut_agent.unit_setup import UnitSetup, requests_soil_setup


class NoModel:
    def bind_tools(self, tools):
        return self

    def invoke(self, *args, **kwargs):
        raise AssertionError("unit setup must not call a model")


def setup(tmp_path):
    db = _Db(tmp_path/"setup.sqlite3")
    owner = ConversationStore(db).get_or_create_user("cli", "one")
    return db, owner, UnitSetup(db)


def answers(wizard, owner, values):
    reply = None
    for value in values:
        reply = wizard.respond(owner, value)
    return reply


@pytest.mark.parametrize("system,last,medium,strategy,method", [
    ("soil", ["supplemental", "drip irrigation"], "soil", "supplemental", "drip"),
    ("soil", ["rainfed"], "soil", "rainfed", None),
    ("hydroponics", ["substrate"], "substrate", "fully_irrigated", None),
    ("aquaponics", ["water"], "water", "fully_irrigated", None),
])
def test_create_and_select_compatible_units_only_after_all_answers(tmp_path, system, last, medium, strategy, method):
    db, owner, wizard = setup(tmp_path)
    wizard.start(owner, system)
    answers(wizard, owner, ["Home", "UTC", "North unit", "outdoors"])
    assert FarmStore(db).list_farms(owner) == []
    assert UnitContexts(db).active(owner) is None
    reply = answers(wizard, owner, last)
    assert "Created and selected" in reply
    units = FarmStore(db).list_units(owner)
    assert len(units) == 1
    assert units[0]["medium"] == medium and units[0]["water_strategy"] == strategy
    assert units[0]["irrigation_method"] == method
    assert UnitContexts(db).active(owner).unit_id == units[0]["id"]
    assert not wizard.active(owner)


def test_invalid_input_back_cancel_and_restart_preserve_selection(tmp_path):
    db, owner, wizard = setup(tmp_path)
    farms = FarmStore(db)
    farms.create_farm(Farm("home", owner, "Home", "UTC"))
    farms.create_unit(owner, ProductionUnit("pond", "home", "Pond", "aquaponics", "water", "greenhouse", "fully_irrigated"))
    UnitContexts(db).select(owner, "pond")
    wizard.start(owner)
    assert "Choose one" in wizard.respond(owner, "not sure")
    assert "Which farm" in wizard.respond(owner, "1")
    assert "farm's name" in wizard.respond(owner, "new")
    wizard.respond(owner, "Second farm")
    assert "IANA" in wizard.respond(owner, "not-a-timezone")
    wizard.respond(owner, "UTC")
    assert "timezone" in wizard.respond(owner, "", back=True)
    wizard.respond(owner, "Asia/Taipei")
    assert UnitContexts(db).active(owner).unit_id == "pond"
    assert "unchanged" in wizard.cancel(owner)
    assert len(farms.list_farms(owner)) == 1
    assert UnitContexts(db).active(owner).unit_id == "pond"
    assert not wizard.active(owner)


def test_resume_uses_saved_answers_and_original_farm_numbering(tmp_path):
    db, owner, wizard = setup(tmp_path)
    farms = FarmStore(db)
    farms.create_farm(Farm("b", owner, "Original farm", "UTC"))
    wizard.start(owner, "soil")
    # A farm added after the choices were shown cannot change what choice 1 means.
    farms.create_farm(Farm("a", owner, "Newly added farm", "UTC"))
    another_db = _Db(db.path)
    resumed = UnitSetup(another_db)
    assert "Resuming" in resumed.start(owner, "aquaponics")
    answers(resumed, owner, ["1", "Field", "open_field", "rainfed"])
    unit = UnitContexts(db).active(owner)
    assert unit.production_system == "soil"
    assert farms.list_units(owner)[0]["farm_id"] == "b"
    another_db._conn.close()


def test_completion_failure_rolls_back_farm_selection_and_draft_removal(tmp_path, monkeypatch):
    db, owner, wizard = setup(tmp_path)
    wizard.start(owner, "soil")
    answers(wizard, owner, ["Home", "UTC", "Field", "open_field", "supplemental"])
    select = UnitContexts.select

    def fail(self, *args, **kwargs):
        select(self, *args, **kwargs)
        raise RuntimeError("selection interrupted")

    monkeypatch.setattr(UnitContexts, "select", fail)
    with pytest.raises(RuntimeError):
        wizard.respond(owner, "sprinkler")
    assert FarmStore(db).list_farms(owner) == []
    assert UnitContexts(db).active(owner) is None
    assert wizard.active(owner)
    monkeypatch.setattr(UnitContexts, "select", select)
    assert "Created and selected" in wizard.respond(owner, "sprinkler")
    assert len(FarmStore(db).list_units(owner)) == 1


def test_legacy_farm_timezone_is_requested_and_saved_explicitly(tmp_path):
    db, owner, wizard = setup(tmp_path)
    db.execute("INSERT INTO farms(id,owner_id,name,legacy) VALUES(?,?,?,1)", ("legacy", owner, "Legacy"))
    wizard.start(owner, "soil")
    assert "timezone" in wizard.respond(owner, "1")
    answers(wizard, owner, ["Africa/Accra", "Field", "open_field", "rainfed"])
    assert FarmStore(db).list_farms(owner)[0]["timezone"] == "Africa/Accra"


def test_drafts_are_owner_scoped_exported_and_erased(tmp_path):
    db, owner, wizard = setup(tmp_path)
    other = ConversationStore(db).get_or_create_user("cli", "other")
    wizard.start(owner, "soil")
    wizard.respond(owner, "Private farm name")
    wizard.start(other, "hydroponics")
    export = FarmStore(db).export(owner)
    assert export["setup_drafts"][0]["payload"]["data"]["farm_name"] == "Private farm name"
    assert "Private farm name" not in json.dumps(FarmStore(db).export(other))
    FarmStore(db).purge(owner)
    assert not wizard.active(owner) and wizard.active(other)


@pytest.mark.parametrize("channel", ["cli", "telegram", "whatsapp"])
def test_shared_commands_and_plain_answers_complete_setup_without_model(tmp_path, channel):
    brain = AgronautAgent(db_path=tmp_path/f"{channel}.sqlite3", chat_model=NoModel())
    assert "farm's name" in dispatch(brain, channel, "one", "/newunit soil").text
    for value in ("Home", "UTC", "Tomatoes", "open_field", "fully_irrigated", "drip"):
        reply = brain.handle_message(channel, "one", value)
        assert "fish" not in reply.lower()
    assert "Created and selected" in reply
    units = brain.list_units(channel, "one")
    assert units[0]["active"] and units[0]["production_system"] == "soil"
    assert brain.profile_facts(channel, "one") == {}


def test_missing_provider_does_not_block_setup_and_soil_is_not_parsed_as_fish_intake(tmp_path):
    brain = AgronautAgent(db_path=tmp_path/"no-model.sqlite3", llm_provider="no-such-provider", require_tools=False)
    assert brain.chat_error
    reply = brain.handle_message("cli", "one", "I have a hectare of tomatoes and want drip irrigation")
    assert "/newunit soil" in reply
    dispatch(brain, "cli", "one", "/newunit soil")
    brain.handle_message("cli", "one", "Home")
    assert "timezone" in dispatch(brain, "cli", "one", "/newunit").text
    dispatch(brain, "cli", "one", "/cancel")
    assert brain.list_units("cli", "one") == []


def test_media_generated_text_cannot_answer_setup_and_reset_discards_draft(tmp_path):
    brain = AgronautAgent(db_path=tmp_path/"media.sqlite3", chat_model=NoModel())
    dispatch(brain, "cli", "one", "/newunit soil")
    reply = brain.handle_message("cli", "one", "The image looks like Farm One", fact_text="my farm")
    assert "text reply" in reply
    assert "farm's name" in dispatch(brain, "cli", "one", "/newunit").text
    brain.reset("cli", "one")
    owner = brain._conv.get_or_create_user("cli", "one")
    assert not brain._unit_setup.active(owner)


def test_routing_hint_does_not_assign_a_domain_to_a_mixed_or_aquatic_request():
    assert requests_soil_setup("surface irrigation for an open field")
    assert not requests_soil_setup("drip lines for my aquaponics system")
    assert not requests_soil_setup("I grow hydroponic tomatoes")
    assert not requests_soil_setup("my fish are gasping at the surface")


def test_v4_upgrade_preserves_records_and_keeps_a_backup(tmp_path):
    db, owner, _ = setup(tmp_path)
    FarmStore(db).create_farm(Farm("home", owner, "Home", "UTC"))
    db.execute("DROP TABLE farm_resource_plans")
    db.execute("DROP TABLE unit_setup_drafts")
    db.execute("DROP TABLE observation_reviews")
    db.execute("DROP TABLE crop_records")
    db.execute("PRAGMA user_version=4")
    path = db.path
    db._conn.close()
    upgraded = _Db(path)
    assert upgraded.query("PRAGMA user_version")[0][0] == SCHEMA_VERSION
    assert FarmStore(upgraded).list_farms(owner)[0]["name"] == "Home"
    backup = next(tmp_path.glob(f"setup.sqlite3.pre-v{SCHEMA_VERSION}-*.bak"))
    with sqlite3.connect(backup) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 4
        assert old.execute("SELECT name FROM sqlite_master WHERE name='unit_setup_drafts'").fetchone() is None


def test_terminal_entrypoint_runs_setup_with_an_unconfigured_model(tmp_path):
    path = tmp_path/"repl.sqlite3"
    process = subprocess.run([sys.executable, "-m", "agronaut_agent.cli", "chat"],
        input="/newunit soil\nHome\nUTC\nField\nopen_field\nrainfed\nquit\n",
        env={**os.environ, "LLM_PROVIDER": "no-such-provider", "AGRONAUT_DB": str(path)},
        cwd=Path(__file__).resolve().parents[2], text=True, capture_output=True, timeout=45)
    assert process.returncode == 0, process.stderr
    assert "Created and selected Field (soil)" in process.stdout
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT production_system,water_strategy FROM production_units").fetchone() == ("soil", "rainfed")


def test_telegram_text_handlers_complete_setup_without_network(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agronaut_agent.channels.telegram_adapter import TelegramAdapter

    # Test transport routing independently of executor/event-loop shutdown. Scope
    # across real threads and processes is covered by test_unit_agent.
    async def inline_call(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", inline_call)

    brain = AgronautAgent(db_path=tmp_path/"telegram.sqlite3", chat_model=NoModel())
    adapter = TelegramAdapter(brain, token="test", allowed_ids={17})
    reply = AsyncMock()
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=17, type="private"),
        effective_user=SimpleNamespace(id=17, full_name="Grower"),
        message=SimpleNamespace(text="/newunit soil", reply_text=reply))
    ctx = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))

    async def exercise():
        await adapter._on_command(update, ctx)
        for value in ("Home", "UTC", "Field", "open_field", "rainfed"):
            update.message.text = value
            await adapter._on_text(update, ctx)

    asyncio.run(exercise())
    assert any("Created and selected" in call.args[0] for call in reply.await_args_list)
    assert brain.list_units("telegram", "17")[0]["active"]


def test_whatsapp_payloads_complete_setup_without_network(tmp_path, monkeypatch):
    from agronaut_agent.channels.whatsapp_adapter import WhatsAppAdapter

    brain = AgronautAgent(db_path=tmp_path/"whatsapp.sqlite3", chat_model=NoModel())
    adapter = WhatsAppAdapter(brain, token="test", phone_number_id="test", allowed_ids={"15551234567"})
    sent = []
    monkeypatch.setattr(adapter, "send_text", lambda number, text: sent.append(text))
    for index, value in enumerate(("/newunit soil", "Home", "UTC", "Field", "open_field", "rainfed")):
        payload = {"entry": [{"changes": [{"value": {"messages": [{"from": "15551234567",
            "id": f"message-{index}", "type": "text", "text": {"body": value}}]}}]}]}
        adapter.handle_payload(payload)
    assert any("Created and selected" in text for text in sent)
    assert brain.list_units("whatsapp", "15551234567")[0]["active"]


def test_web_chat_routes_setup_commands_and_replies_to_the_same_service(tmp_path):
    import app

    brain = AgronautAgent(db_path=tmp_path/"web.sqlite3", chat_model=NoModel())
    assert "farm's name" in app._route_turn(brain, "one", "/newunit soil", None)
    for value in ("Home", "UTC", "Field", "open_field", "rainfed"):
        reply = app._route_turn(brain, "one", value, None)
    assert "Created and selected" in reply
    assert brain.list_units("web", "one")[0]["active"]
