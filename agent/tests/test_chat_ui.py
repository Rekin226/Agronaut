"""Headless tests: the Streamlit "Assistant (chat)" mode must drive the real tool-calling
agent (agronaut_agent), not the legacy srcs/chatbot state machine — with per-browser-session
identity so concurrent web users never share a conversation or profile.

Uses AppTest with a fake chat model injected at the agronaut_agent.core seam (no network).
"""

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402


class _FakeChat:
    """Turn 1 -> size tool call; after a ToolMessage -> a final answer carrying the numbers."""

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
        if tool_msgs:
            saw = "fish=" in tool_msgs[-1].content
            return AIMessage(content=f"Sized it (numbers_from_tool={saw}).")
        return AIMessage(content="", tool_calls=[{
            "name": "size_aquaponics_system", "id": "call_1",
            "args": {"fish_species": "tilapia", "crop": "lettuce", "grow_area_m2": 12,
                     "temperature_c": 27, "water_budget_lpd": 300},
        }])


@pytest.fixture
def fake_agent_backend(monkeypatch, tmp_path):
    import agronaut_agent.core as core
    monkeypatch.setattr(core, "get_chat_model", lambda *a, **k: _FakeChat())
    monkeypatch.setattr(core, "build_fallback_chat", lambda *a, **k: None)
    monkeypatch.setenv("AGRONAUT_DB", str(tmp_path / "web.sqlite3"))
    return tmp_path / "web.sqlite3"


def _open_chat(at):
    at.run(timeout=30)
    at.radio[0].set_value("Assistant (chat)")
    at.run(timeout=30)
    return at


def test_web_chat_routes_through_the_tool_calling_agent(fake_agent_backend):
    at = _open_chat(AppTest.from_file("app.py"))
    at.chat_input[0].set_value("size a 12 m2 tilapia + lettuce at 27C, 300 L/day").run(timeout=60)

    assert not at.exception
    blob = "\n".join(str(m.value) for m in at.chat_message[0].markdown) if at.chat_message else ""
    all_md = "\n".join(str(m.value) for m in at.markdown)
    assert "numbers_from_tool=True" in (blob + all_md)

    # the consultation persisted: a web-channel user exists with tool-captured facts
    from agronaut_agent.store import _Db, MemoryStore
    db = _Db(fake_agent_backend)
    users = db.query("SELECT user_id FROM users WHERE channel='web'")
    assert len(users) == 1
    facts = MemoryStore(db).get_facts(users[0]["user_id"])
    assert facts.get("crop") == "lettuce"
    assert facts.get("grow_area_m2") == "12"


def test_two_web_sessions_are_independent(fake_agent_backend):
    at1 = _open_chat(AppTest.from_file("app.py"))
    at1.chat_input[0].set_value("size a 12 m2 tilapia + lettuce at 27C, 300 L/day").run(timeout=60)
    at2 = _open_chat(AppTest.from_file("app.py"))
    at2.chat_input[0].set_value("hello, new user here").run(timeout=60)

    assert not at1.exception and not at2.exception
    # two distinct per-session identities in the store — never one shared conversation
    from agronaut_agent.store import _Db
    db = _Db(fake_agent_backend)
    users = db.query("SELECT DISTINCT user_id FROM users WHERE channel='web'")
    assert len(users) == 2
    # session 2's transcript must not contain session 1's conversation
    s2 = "\n".join(str(m.value) for m in at2.markdown)
    assert "12 m2 tilapia" not in s2


def test_web_chat_degrades_gracefully_without_a_provider(monkeypatch, tmp_path):
    import agronaut_agent.core as core

    def _boom(*a, **k):
        raise RuntimeError("no LLM provider configured")

    monkeypatch.setattr(core, "get_chat_model", _boom)
    monkeypatch.setenv("AGRONAUT_DB", str(tmp_path / "web.sqlite3"))

    at = _open_chat(AppTest.from_file("app.py"))
    assert not at.exception                      # never a traceback in the UI
    warnings = "\n".join(str(w.value) for w in at.warning)
    assert "chat" in warnings.lower() or "provider" in warnings.lower()


# --- photo upload in the web chat -------------------------------------------------------
# The routing decision is extracted from the widget code so it can be tested without a
# Streamlit script run. Photos must reach the SAME agent seam Telegram and WhatsApp use, so
# the observation guard and cited tools apply on the web too.

class _RecordingAgent:
    def __init__(self):
        self.messages, self.images = [], []

    def handle_message(self, channel, user, text, display_name=None):
        self.messages.append((channel, user, text))
        return "text reply"

    def handle_image(self, channel, user, image_bytes, caption=None, display_name=None):
        self.images.append((channel, user, image_bytes, caption))
        return "photo reply"


def test_route_turn_sends_a_photo_to_handle_image():
    import app
    agent = _RecordingAgent()
    reply = app._route_turn(agent, "web1", "what's wrong?", b"jpegbytes")
    assert reply == "photo reply"
    assert agent.images == [("web", "web1", b"jpegbytes", "what's wrong?")]
    assert agent.messages == []          # not double-handled as a text turn


def test_route_turn_photo_without_text_passes_no_caption():
    import app
    agent = _RecordingAgent()
    app._route_turn(agent, "web1", "", b"jpegbytes")
    assert agent.images[0][3] is None    # empty string must not become a caption


def test_route_turn_without_a_photo_uses_handle_message():
    import app
    agent = _RecordingAgent()
    reply = app._route_turn(agent, "web1", "size my system", None)
    assert reply == "text reply"
    assert agent.messages == [("web", "web1", "size my system")]
    assert agent.images == []


def test_chat_mode_still_renders_with_the_file_accepting_input(fake_agent_backend):
    # Guards the accept_file wiring: a signature mismatch would raise on first render.
    at = _open_chat(AppTest.from_file("app.py"))
    assert not at.exception
    assert at.chat_input
