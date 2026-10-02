"""AgronautAgent.tool_results: what a named tool returned in the user's latest turn."""

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from agronaut_agent import runtime
from agronaut_agent.analytics import Analytics
from agronaut_agent.core import AgronautAgent


class _OneToolCall:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if [m for m in messages if isinstance(m, ToolMessage)]:
            return AIMessage(content="Sized it.")
        return AIMessage(
            content="",
            tool_calls=[{
                "name": "size_aquaponics_system",
                "id": "call_1",
                "args": {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 12,
                         "temperature_c": 27, "water_budget_lpd": 300},
            }],
        )


class _NoTool:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="How big is your space?")


@pytest.fixture(autouse=True)
def _clean_turn():
    yield
    runtime.end_turn()


def _agent(tmp_path, monkeypatch, chat):
    monkeypatch.setenv("AGRONAUT_ANALYTICS_PATH", str(tmp_path / "a.jsonl"))
    a = AgronautAgent(chat_model=chat, db_path=str(tmp_path / "db.sqlite"))
    a._analytics = Analytics(path=tmp_path / "a.jsonl")
    return a


def test_returns_the_tool_output_of_the_latest_turn(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch, _OneToolCall())
    a.handle_message("web", "u1", "Size a system for 12 m2 of lettuce with tilapia")
    results = a.tool_results("web", "u1", "size_aquaponics_system")
    assert len(results) == 1 and results[0]
    assert a.tool_results("web", "u1", "some_other_tool") == []
    assert a.tool_results("web", "someone_else", "size_aquaponics_system") == []


def test_a_new_turn_without_the_tool_clears_it(tmp_path, monkeypatch):
    a = _agent(tmp_path, monkeypatch, _OneToolCall())
    a.handle_message("web", "u1", "Size a system for 12 m2 of lettuce with tilapia")
    a._bound = _NoTool()  # the next turn asks a question and calls no tool
    a.handle_message("web", "u1", "Thanks")
    assert a.tool_results("web", "u1", "size_aquaponics_system") == []


def test_model_label_names_the_configured_model_only(tmp_path, monkeypatch):
    injected = _agent(tmp_path, monkeypatch, _NoTool())
    assert injected.model_label is None  # an injected model's provider is unknown here
