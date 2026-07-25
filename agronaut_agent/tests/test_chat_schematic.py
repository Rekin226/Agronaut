"""Drawing a system in chat: the render_system_schematic tool produces a PNG, the agent
surfaces it as a per-user attachment, and the channel adapters send it. Verified with fakes
— no network, no real bot.
"""

import os

from langchain_core.messages import AIMessage, ToolMessage

from agronaut_agent.core import AgronautAgent
from agronaut_agent import runtime
from agronaut_agent.tools import render_system_schematic, AGRONAUT_TOOLS


def test_tool_registered_and_records_a_png_attachment():
    assert "render_system_schematic" in {t.name for t in AGRONAUT_TOOLS}
    runtime.set_current(None, "cli:x")
    try:
        out = render_system_schematic.invoke({
            "fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 20,
            "temperature_c": 27, "water_budget_lpd": 5000})
        atts = runtime.get_attachments()
    finally:
        runtime.clear_current()
    assert "schematic" in out.lower()
    assert len(atts) == 1 and atts[0].endswith(".png")
    with open(atts[0], "rb") as fh:
        assert fh.read(8) == b"\x89PNG\r\n\x1a\n"
    os.remove(atts[0])


def test_tool_trust_gate_rejects_bad_input_and_attaches_nothing():
    runtime.set_current(None, "cli:x")
    try:
        out = render_system_schematic.invoke({
            "fish_species": "dragon", "crop": "lettuce", "grow_area_m2": 20,
            "temperature_c": 27, "water_budget_lpd": 5000})
        atts = runtime.get_attachments()
    finally:
        runtime.clear_current()
    assert "VALIDATION_FAILED" in out
    assert atts == []


class _DrawFake:
    """Turn 1 -> call render_system_schematic; then -> a final text reply."""
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="Here's your system diagram.")
        return AIMessage(content="", tool_calls=[{
            "name": "render_system_schematic", "id": "s1",
            "args": {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 20,
                     "temperature_c": 27, "water_budget_lpd": 5000}}])


def test_agent_surfaces_attachment_and_take_is_idempotent(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_DrawFake())
    reply = agent.handle_message("telegram", "77", "draw my system")
    assert "diagram" in reply.lower()
    atts = agent.take_attachments("telegram", "77")
    assert len(atts) == 1 and atts[0].endswith(".png")
    # draining is idempotent — a second take returns nothing
    assert agent.take_attachments("telegram", "77") == []
    os.remove(atts[0])
