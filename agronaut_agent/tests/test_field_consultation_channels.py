"""The same unit-scoped intake through ordinary chat without a model."""

import pytest

from agronaut_agent.channels.commands import dispatch
from agronaut_agent.tests.test_field_consultation import ANSWERS
from agronaut_agent.tests.test_unit_agent import agent_and_units


def test_plain_text_question_flow_preview_and_explicit_save(tmp_path):
    from agronaut_agent.tests.test_unit_setup import NoModel
    brain, owner = agent_and_units(tmp_path, NoModel())
    brain.select_unit("cli", "one", "field")
    assert "Choose 1" in dispatch(brain, "cli", "one", "/fieldplan").text
    view = brain._field_intake.get(owner, "field")
    while view["status"] != "ready":
        reply = brain.handle_message("cli", "one", ANSWERS[view["next_field"]])
        view = brain._field_intake.get(owner, "field")
        assert view["question"] in reply
    assert "Reply save" in reply
    assert brain.field_workflow("cli", "one", "list", {})["calculations"] == []
    saved = brain.handle_message("cli", "one", "save")
    assert "Saved field-water calculation" in saved
    assert not brain._field_intake.active(owner, "field")
    assert brain.field_workflow("cli", "one", "list", {})["water_days"] == []


def test_intake_switch_media_back_cancel_and_reset_are_scoped(tmp_path):
    from agronaut_agent.tests.test_unit_setup import NoModel
    brain, owner = agent_and_units(tmp_path, NoModel())
    brain.select_unit("cli", "one", "field")
    dispatch(brain, "cli", "one", "/fieldplan")
    before = brain._field_intake.get(owner, "field")
    assert "text reply" in brain.handle_message("cli", "one", "2", fact_text="a photograph")
    assert brain._field_intake.get(owner, "field") == before
    brain.handle_message("cli", "one", "1")
    assert "Choose 1" in dispatch(brain, "cli", "one", "/back").text
    brain.select_unit("cli", "one", "hydro")
    assert "Select a soil unit" in dispatch(brain, "cli", "one", "/fieldplan").text
    dispatch(brain, "cli", "one", "/cancel")
    assert brain._field_intake.active(owner, "field")
    brain.select_unit("cli", "one", "field")
    assert "Choose 1" in dispatch(brain, "cli", "one", "/fieldplan").text
    assert "Field-water intake cancelled" in dispatch(brain, "cli", "one", "/cancel").text
    dispatch(brain, "cli", "one", "/fieldplan")
    brain.reset("cli", "one")
    assert not brain._field_intake.active(owner, "field")


def test_intake_runs_when_model_is_unconfigured(tmp_path):
    from agronaut_agent.core import AgronautAgent
    brain = AgronautAgent(db_path=tmp_path / "no-model.sqlite3", llm_provider="no-such-provider", require_tools=False)
    dispatch(brain, "cli", "one", "/newunit soil")
    for answer in ("Home", "UTC", "Field", "open_field", "rainfed"):
        brain.handle_message("cli", "one", answer)
    assert "Choose 1" in dispatch(brain, "cli", "one", "/fieldplan").text
    assert "Which crop" in brain.handle_message("cli", "one", "1")


def test_web_chat_router_and_whatsapp_commands_reach_the_intake(tmp_path):
    from agronaut_agent.core import AgronautAgent
    from agronaut_agent.tests.test_unit_setup import NoModel
    from app import _route_turn
    brain = AgronautAgent(db_path=tmp_path / "channels.sqlite3", chat_model=NoModel())
    for channel in ("web", "whatsapp"):
        brain.create_unit(channel, "operator", dict(farm=dict(name="Home", timezone="UTC"),
            unit=dict(name="Field", production_system="soil", environment="open_field", medium="soil",
                      water_strategy="rainfed", irrigation_method=None)))
    assert "Choose 1" in _route_turn(brain, "operator", "/fieldplan", None)
    assert "Which crop" in _route_turn(brain, "operator", "1", None)
    assert "Choose 1" in dispatch(brain, "whatsapp", "operator", "/fieldplan").text
    assert "soil field" not in brain.handle_message("whatsapp", "operator", "2")
    assert "cancelled" in _route_turn(brain, "operator", "/cancel", None)
    other_owner = brain._conv.get_or_create_user("whatsapp", "operator")
    other_unit = brain._contexts.active(other_owner).unit_id
    assert brain._field_intake.get(other_owner, other_unit)["answers"]["task"] == "root_zone_budget"


@pytest.mark.parametrize("task", ["crop_water", "drip_design", "sprinkler_design", "surface_evaluation"])
def test_telegram_handlers_complete_intake_without_network(tmp_path, monkeypatch, task):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agronaut_agent.channels.telegram_adapter import TelegramAdapter
    from agronaut_agent.core import AgronautAgent
    from agronaut_agent.tests.test_equipment_consultation import answers
    from agronaut_agent.tests.test_surface_consultation import answers as surface_answers
    from agronaut_agent.tests.test_unit_setup import NoModel

    async def inline_call(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr(asyncio, "to_thread", inline_call)
    brain = AgronautAgent(db_path=tmp_path / "telegram.sqlite3", chat_model=NoModel())
    unit = brain.create_unit("telegram", "17", dict(farm=dict(name="Home", timezone="UTC"),
        unit=dict(name="Field", production_system="soil", environment="open_field", medium="soil",
                  water_strategy="rainfed", irrigation_method=None)))
    owner = brain._conv.get_or_create_user("telegram", "17")
    adapter = TelegramAdapter(brain, token="test", allowed_ids={17})
    reply = AsyncMock()
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=17, type="private"),
        effective_user=SimpleNamespace(id=17, full_name="Grower"), message=SimpleNamespace(text="/fieldplan", reply_text=reply))
    ctx = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))
    if task == "crop_water":
        supplied = dict(ANSWERS, task="1")
    elif task == "surface_evaluation":
        supplied = surface_answers()
    else:
        supplied = answers(task)

    async def exercise():
        await adapter._on_command(update, ctx)
        view = brain._field_intake.get(owner, unit["id"])
        while view["status"] != "ready":
            update.message.text = supplied[view["next_field"]]
            await adapter._on_text(update, ctx)
            view = brain._field_intake.get(owner, unit["id"])
        update.message.text = "save"
        await adapter._on_text(update, ctx)
    asyncio.run(exercise())
    label = "Saved field-water calculation" if task == "crop_water" else "Saved irrigation-equipment calculation"
    if task == "surface_evaluation":
        label = "Saved surface-event evaluation"
    assert any(label in call.args[0] for call in reply.await_args_list)
    records = brain.field_workflow("telegram", "17", "list", {})
    assert len(records["calculations"]) == 1
    assert records["calculations"][0]["task"] == task
    assert records["water_days"] == []
