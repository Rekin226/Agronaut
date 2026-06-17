"""The tool-loop, exercised with a fake chat model (no network).

Asserts the agent invokes the requested tool, feeds the result back as a ToolMessage,
returns a final answer that carries the tool's numbers, persists an audit trail, and
extracts user facts deterministically.
"""

from langchain_core.messages import AIMessage, ToolMessage

from agronaut_agent.core import AgronautAgent


class _FakeChat:
    """Turn 1 -> a tool call; turn 2 (after a ToolMessage) -> a final answer."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
        if tool_msgs:
            saw_numbers = "fish=" in tool_msgs[-1].content
            return AIMessage(content=f"Sized it (numbers_from_tool={saw_numbers}).")
        return AIMessage(
            content="",
            tool_calls=[{
                "name": "size_aquaponics_system",
                "id": "call_1",
                "args": {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 12,
                         "temperature_c": 27, "water_budget_lpd": 300},
            }],
        )


class _ChattyFake:
    """Never calls a tool — just replies. Verifies the no-tool path."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="Hello! Tell me about your system.")


def test_tool_loop_calls_tool_and_returns_numbers(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_FakeChat())
    reply = agent.handle_message("cli", "tester", "size a 12 m2 tilapia + lettuce at 27C, 300 L/day")
    assert "numbers_from_tool=True" in reply

    roles = [m["role"] for m in agent._conv.recent_messages("cli:tester", limit=10)]
    assert roles == ["user", "tool", "assistant"]  # audit trail persisted
    assert agent._mem.get_facts("cli:tester")["temperature_c"] == "27.0"


def test_no_tool_path_returns_plain_reply(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ChattyFake())
    reply = agent.handle_message("cli", "u2", "hi there")
    assert "Hello" in reply
    assert [m["role"] for m in agent._conv.recent_messages("cli:u2")] == ["user", "assistant"]


def test_reset_clears_history(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_ChattyFake())
    agent.handle_message("cli", "u3", "hi")
    agent.reset("cli", "u3")
    assert agent._conv.recent_messages("cli:u3") == []
