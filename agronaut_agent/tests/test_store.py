"""Per-user persistence: identity, history, memory — concurrency-safe by construction."""

import pytest

from agronaut_agent.store import _Db, ConversationStore, MemoryStore, user_id_for
from agronaut_agent import memory_extract


@pytest.fixture
def stores(tmp_path):
    db = _Db(tmp_path / "t.sqlite3")
    return ConversationStore(db), MemoryStore(db)


def test_user_id_namespacing():
    assert user_id_for("telegram", "123") == "telegram:123"
    assert user_id_for("discord", "123") != user_id_for("telegram", "123")


def test_get_or_create_is_idempotent(stores):
    conv, _ = stores
    a = conv.get_or_create_user("telegram", "123", "Rachid")
    b = conv.get_or_create_user("telegram", "123", "Rachid")
    assert a == b == "telegram:123"


def test_message_history_ordered_and_resettable(stores):
    conv, _ = stores
    uid = conv.get_or_create_user("telegram", "123")
    conv.append_message(uid, "user", "hi")
    conv.append_message(uid, "assistant", "hello")
    assert [m["role"] for m in conv.recent_messages(uid)] == ["user", "assistant"]
    conv.reset_conversation(uid)
    assert conv.recent_messages(uid) == []


def test_facts_upsert_and_survive_conversation_reset(stores):
    conv, mem = stores
    uid = conv.get_or_create_user("telegram", "123")
    mem.set_facts(uid, {"temperature_c": "28", "fish_species": "tilapia"})
    mem.set_fact(uid, "temperature_c", "30", source="user_stated")  # overwrite
    assert mem.get_facts(uid) == {"temperature_c": "30", "fish_species": "tilapia"}
    conv.reset_conversation(uid)
    assert mem.get_facts(uid) == {"temperature_c": "30", "fish_species": "tilapia"}


def test_extract_facts_from_free_text():
    facts = memory_extract.extract_facts("water is 26C and pH 7.2, I keep tilapia")
    assert facts["temperature_c"] == "26.0"
    assert facts["ph"] == "7.2"
    assert facts["fish_species"].lower() == "tilapia"
