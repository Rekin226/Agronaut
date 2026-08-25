"""DPG data-rights: a user can export everything Agronaut holds about them in a portable,
non-proprietary format (indicator 6), and delete all of it (do-no-harm / privacy). Both are
reachable from chat commands via the agent seam.
"""

import json

from langchain_core.messages import AIMessage

from agronaut_agent.core import AgronautAgent


class _Chatty:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="ok")


def _seed(agent, ch="telegram", u="1"):
    uid = agent._conv.get_or_create_user(ch, u)
    agent._mem.set_facts(uid, {"goal": "design", "fish_species": "tilapia"})
    agent._mem.add_memory(uid, "had an ammonia spike in June", "event")
    agent._conv.append_message(uid, "user", "hello")
    agent._conv.append_message(uid, "assistant", "hi")
    agent._calibration.record(uid, "tilapia.fcr", 1.6)
    return uid


def test_export_returns_all_personal_data_as_json(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_Chatty())
    _seed(agent)
    data = agent.export_user_data("telegram", "1")

    # portable: round-trips through JSON (non-proprietary format, indicator 6)
    blob = json.dumps(data)
    assert json.loads(blob)

    assert data["identity"]["channel"] == "telegram"
    assert data["profile"]["fish_species"] == "tilapia"
    assert any("ammonia spike" in m["content"] for m in data["memories"])
    assert any(msg["role"] == "user" for msg in data["messages"])
    assert any(m["coefficient"] == "tilapia.fcr" for m in data["measurements"])


def test_export_for_unknown_user_is_empty_not_error(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_Chatty())
    data = agent.export_user_data("telegram", "nobody")
    assert data["profile"] == {}
    assert data["memories"] == [] and data["messages"] == []


def test_delete_me_wipes_everything(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_Chatty())
    uid = _seed(agent)
    agent.delete_me("telegram", "1")

    data = agent.export_user_data("telegram", "1")
    assert data["profile"] == {}
    assert data["memories"] == []
    assert data["messages"] == []
    assert data["measurements"] == []
    # calibration measurements are gone too (not just conversation/profile)
    assert agent._calibration._by_coefficient(uid) == {}


def test_export_is_scoped_to_the_requesting_user(tmp_path):
    agent = AgronautAgent(db_path=tmp_path / "t.sqlite3", chat_model=_Chatty())
    _seed(agent, u="1")
    _seed(agent, u="2")
    agent._mem.set_facts("telegram:2", {"location": "secret-place"})

    data = agent.export_user_data("telegram", "1")
    assert "location" not in data["profile"]          # never leak another user's data
